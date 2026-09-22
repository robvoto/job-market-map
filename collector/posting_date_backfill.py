"""Evidence-led historical posting-date repair."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from collector import db
from collector.db import connect, init_db
from collector.posting_dates import normalize_posting_date


@dataclass(frozen=True)
class SeekBackfillResult:
    eligible: int
    derivable: int
    updated: int
    cleared_stale: int
    no_usable_evidence: int


@dataclass(frozen=True)
class LinkedInWindowBackfillResult:
    eligible: int
    exact_or_relative: int
    bounded: int
    ambiguous_or_unbounded: int
    updated: int
    cleared_stale: int
    unresolved_job_ids: tuple[int, ...]


def _read_only_connect() -> sqlite3.Connection:
    uri = f"{db.DB_PATH.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def backfill_seek_posting_dates(*, apply: bool = False, limit: int | None = None) -> SeekBackfillResult:
    """Backfill missing SEEK dates from retained card text and that capture's time.

    If repeated captures produce different rounded-relative estimates, choose
    the oldest result. Existing canonical values are never replaced.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if apply:
        init_db()
        connection = connect()
    else:
        connection = _read_only_connect()
    with closing(connection) as conn:
        sql = """
            SELECT j.id, j.posted_at AS existing_posted_at, fs.state AS posted_state
              FROM jobs j
              LEFT JOIN job_field_states fs ON fs.job_id=j.id AND fs.field_name='posted_at'
             WHERE j.source='seek'
               AND ((j.posted_at IS NULL OR trim(j.posted_at)='')
                    OR (j.posted_at IS NOT NULL AND trim(j.posted_at)<>''
                        AND julianday(j.posted_at) IS NULL)
                    OR EXISTS (SELECT 1 FROM job_field_states fs WHERE fs.job_id=j.id
                               AND fs.field_name='posted_at' AND fs.state='not_present'))
             ORDER BY j.id
        """
        rows = conn.execute(sql).fetchall()

    candidates: dict[int, list[tuple[str, str, str, str | None]]] = {}
    prior_values: dict[int, str | None] = {}
    stale_ids: set[int] = set()
    for row in rows:
        job_id = int(row["id"])
        prior_values[job_id] = row["existing_posted_at"]
        old_value_invalid = bool(
            row["existing_posted_at"]
            and not normalize_posting_date(
                posted_at=str(row["existing_posted_at"]),
                posted_text=None,
                captured_at="1970-01-01T00:00:00+00:00",
            )
        )
        if row["posted_state"] == "not_present" or old_value_invalid:
            stale_ids.add(job_id)

    # The production database may not yet have an index on card_captures.job_id.
    # Stream that table once and filter in Python rather than repeatedly joining
    # every eligible job to an unindexed capture table.
    if prior_values:
        capture_conn = _read_only_connect() if not apply else connect()
        with closing(capture_conn) as conn:
            for row in conn.execute("SELECT job_id,captured_at,raw_json FROM card_captures"):
                job_id = int(row["job_id"])
                if job_id not in prior_values or not row["captured_at"]:
                    continue
                captured_at = str(row["captured_at"])
                try:
                    raw = json.loads(row["raw_json"] or "{}")
                except json.JSONDecodeError:
                    raw = {}
                if not isinstance(raw, dict):
                    raw = {}
                text = raw.get("posted_text")
                parsed = normalize_posting_date(
                    posted_at=raw.get("posted_at_source"),
                    posted_text=text,
                    captured_at=captured_at,
                )
                if parsed:
                    candidates.setdefault(job_id, []).append(
                        (parsed.value, parsed.basis, captured_at, text)
                    )
    eligible_ids = sorted(prior_values)
    if limit is not None:
        eligible_ids = eligible_ids[:limit]
    ordered = [(job_id, candidates[job_id]) for job_id in eligible_ids if job_id in candidates]
    stale_without_evidence = [
        job_id for job_id in eligible_ids if job_id in stale_ids and job_id not in candidates
    ]
    updated = cleared_stale = 0
    if apply:
        with connect() as conn:
            for job_id, evidence in ordered:
                # Exact retained source dates outrank rounded relative text.
                # Within the same evidence class, keep the conservative older
                # estimate when repeated captures differ.
                evidence_rank = {"source_exact": 3, "source_relative": 2, "search_window_bound": 1}
                value, basis, captured_at, posted_text = min(
                    evidence,
                    key=lambda item: (-evidence_rank.get(item[1], 0), item[0]),
                )
                prior_value = prior_values[job_id]
                conn.execute(
                    "UPDATE jobs SET posted_at=?,posted_at_basis=? "
                    "WHERE id=? AND ((posted_at IS NULL OR trim(posted_at)='') OR EXISTS "
                    "(SELECT 1 FROM jobs current WHERE current.id=jobs.id "
                    "AND current.posted_at IS NOT NULL AND trim(current.posted_at)<>'' "
                    "AND julianday(current.posted_at) IS NULL) OR EXISTS "
                    "(SELECT 1 FROM job_field_states fs WHERE fs.job_id=jobs.id "
                    "AND fs.field_name='posted_at' AND fs.state='not_present'))",
                    (value, basis, job_id),
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    conn.execute(
                        "INSERT INTO card_captures(job_id,captured_at,raw_json) VALUES(?,?,?)",
                        (
                            job_id,
                            captured_at,
                            json.dumps(
                                {
                                    "posted_at_backfill": True,
                                    "posted_at_basis": basis,
                                    "posted_at_normalized": value,
                                    "previous_posted_at": prior_value,
                                    "posted_text": posted_text,
                                },
                                sort_keys=True,
                            ),
                        ),
                    )
                    conn.execute(
                        """INSERT INTO job_field_states(
                               job_id,field_name,state,evidence_source,checked_at
                           ) VALUES(?, 'posted_at', 'known', 'backfill:seek_card', ?)
                           ON CONFLICT(job_id,field_name) DO UPDATE SET
                               state='known',evidence_source=excluded.evidence_source,
                               checked_at=excluded.checked_at""",
                        (job_id, captured_at),
                    )
                    updated += 1
            for job_id in stale_without_evidence:
                checked_at = datetime.now(UTC).isoformat(timespec="seconds")
                conn.execute(
                    "UPDATE jobs SET posted_at=NULL,posted_at_basis=NULL WHERE id=?",
                    (job_id,),
                )
                conn.execute(
                    """INSERT INTO job_field_states(
                           job_id,field_name,state,evidence_source,checked_at
                       ) VALUES(?, 'posted_at', 'unknown', 'backfill:seek_no_usable_evidence', ?)
                       ON CONFLICT(job_id,field_name) DO UPDATE SET state='unknown',
                           evidence_source=excluded.evidence_source,checked_at=excluded.checked_at""",
                    (job_id, checked_at),
                )
                conn.execute(
                    "INSERT INTO card_captures(job_id,captured_at,raw_json) VALUES(?,?,?)",
                    (
                        job_id,
                        checked_at,
                        json.dumps(
                            {
                                "posted_at_backfill": True,
                                "posted_at_basis": "unknown",
                                "previous_posted_at": prior_values[job_id],
                                "reason": "no_usable_source_date_evidence",
                            },
                            sort_keys=True,
                        ),
                    ),
                )
                cleared_stale += 1
    return SeekBackfillResult(
        eligible=len(prior_values),
        derivable=len(ordered),
        updated=updated,
        cleared_stale=cleared_stale,
        no_usable_evidence=max(0, len(eligible_ids) - len(ordered)),
    )


def result_json(result: SeekBackfillResult) -> str:
    return json.dumps(asdict(result), sort_keys=True)


def backfill_linkedin_posting_dates(*, apply: bool = False, limit: int | None = None):
    """Use retained exact/relative labels or uniquely matched bounded searches.

    An old card's offset must fall within one completed LinkedIn run's offset
    range and its capture time within that run. Ambiguous matches are left for
    source refresh rather than assigned a guessed freshness window.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    connection = connect() if apply else _read_only_connect()
    with closing(connection) as conn:
        rows = conn.execute(
            """SELECT j.id,j.posted_at,fs.state AS posted_state
                 FROM jobs j LEFT JOIN job_field_states fs
                   ON fs.job_id=j.id AND fs.field_name='posted_at'
                WHERE j.source='linkedin'
                  AND ((j.posted_at IS NULL OR trim(j.posted_at)='')
                       OR (julianday(j.posted_at) IS NULL AND trim(j.posted_at)<>'')
                       OR fs.state='not_present')
                ORDER BY j.id"""
        ).fetchall()
        runs = conn.execute(
            """SELECT started_at,finished_at,metadata_json FROM collection_runs
                WHERE source='linkedin' AND finished_at IS NOT NULL"""
        ).fetchall()
        captures = conn.execute(
            "SELECT job_id,captured_at,raw_json,raw_card_text FROM card_captures ORDER BY id"
        ).fetchall()

    eligible = {int(row["id"]): dict(row) for row in rows}
    if limit is not None:
        eligible = dict(list(eligible.items())[:limit])
    windows: list[tuple[datetime, datetime, int, int, int]] = []
    for run in runs:
        try:
            meta = json.loads(run["metadata_json"] or "{}")
            hours = int(meta["hours_old"])
            start_offset = int(meta["start_offset"])
            next_offset = int(meta["next_offset"])
            started = datetime.fromisoformat(str(run["started_at"]))
            finished = datetime.fromisoformat(str(run["finished_at"]))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        if hours <= 0 or next_offset <= start_offset:
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=UTC)
        windows.append((started, finished, start_offset, next_offset, hours))

    candidates: dict[int, list[tuple[str, str, str, str | None, int]]] = {}
    for capture in captures:
        job_id = int(capture["job_id"])
        if job_id not in eligible or not capture["captured_at"]:
            continue
        try:
            raw = json.loads(capture["raw_json"] or "{}")
        except json.JSONDecodeError:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        jobspy = raw.get("jobspy") if isinstance(raw.get("jobspy"), dict) else {}
        posted_source = raw.get("posted_at_source") or raw.get("date_posted") or jobspy.get("date_posted")
        posted_text = raw.get("posted_text")
        parsed = normalize_posting_date(
            posted_at=posted_source,
            posted_text=posted_text or capture["raw_card_text"],
            captured_at=str(capture["captured_at"]),
        )
        if parsed is None:
            try:
                offset = int(raw["offset"])
                captured = datetime.fromisoformat(str(capture["captured_at"]))
                if captured.tzinfo is None:
                    captured = captured.replace(tzinfo=UTC)
                matches = [
                    window
                    for window in windows
                    if window[2] <= offset < window[3]
                    and window[0] <= captured.astimezone(UTC) <= window[1]
                ]
            except (KeyError, TypeError, ValueError):
                matches = []
            unique_hours = {window[4] for window in matches}
            if len(unique_hours) == 1:
                parsed = normalize_posting_date(
                    posted_at=None,
                    posted_text=None,
                    captured_at=str(capture["captured_at"]),
                    search_window_hours=unique_hours.pop(),
                )
        if parsed:
            rank = {"source_exact": 3, "source_relative": 2, "search_window_bound": 1}.get(parsed.basis, 0)
            candidates.setdefault(job_id, []).append(
                (parsed.value, parsed.basis, str(capture["captured_at"]), posted_text, rank)
            )

    selected = {
        job_id: min(values, key=lambda item: (-item[4], item[0]))
        for job_id, values in candidates.items()
    }
    exact_or_relative = sum(value[1] != "search_window_bound" for value in selected.values())
    bounded = len(selected) - exact_or_relative
    updated = cleared_stale = 0
    if apply and selected:
        with connect() as conn:
            for job_id, (value, basis, captured_at, posted_text, _rank) in selected.items():
                conn.execute(
                    "UPDATE jobs SET posted_at=?,posted_at_basis=? WHERE id=? "
                    "AND (posted_at IS NULL OR trim(posted_at)='' OR julianday(posted_at) IS NULL "
                    "OR EXISTS (SELECT 1 FROM job_field_states fs WHERE fs.job_id=jobs.id "
                    "AND fs.field_name='posted_at' AND fs.state='not_present'))",
                    (value, basis, job_id),
                )
                if not conn.execute("SELECT changes()").fetchone()[0]:
                    continue
                conn.execute(
                    "INSERT INTO job_field_states(job_id,field_name,state,evidence_source,checked_at) "
                    "VALUES(?, 'posted_at','known','backfill:linkedin_retained_evidence',?) "
                    "ON CONFLICT(job_id,field_name) DO UPDATE SET state='known', "
                    "evidence_source=excluded.evidence_source,checked_at=excluded.checked_at",
                    (job_id, captured_at),
                )
                conn.execute(
                    "INSERT INTO card_captures(job_id,captured_at,raw_json) VALUES(?,?,?)",
                    (job_id, captured_at, json.dumps({"posted_at_backfill": True,
                        "posted_at_normalized": value, "posted_at_basis": basis,
                        "posted_text": posted_text}, sort_keys=True)),
                )
                updated += 1
            unresolved = [
                (job_id, row)
                for job_id, row in eligible.items()
                if job_id not in selected
                and (
                    row["posted_state"] == "not_present"
                    or (row["posted_at"] and not normalize_posting_date(
                        posted_at=str(row["posted_at"]), posted_text=None,
                        captured_at="1970-01-01T00:00:00+00:00",
                    ))
                )
            ]
            for job_id, row in unresolved:
                checked_at = datetime.now(UTC).isoformat(timespec="seconds")
                conn.execute(
                    "UPDATE jobs SET posted_at=NULL,posted_at_basis=NULL WHERE id=?",
                    (job_id,),
                )
                conn.execute(
                    "INSERT INTO job_field_states(job_id,field_name,state,evidence_source,checked_at) "
                    "VALUES(?, 'posted_at','unknown','backfill:linkedin_no_retained_date',?) "
                    "ON CONFLICT(job_id,field_name) DO UPDATE SET state='unknown', "
                    "evidence_source=excluded.evidence_source,checked_at=excluded.checked_at",
                    (job_id, checked_at),
                )
                conn.execute(
                    "INSERT INTO card_captures(job_id,captured_at,raw_json) VALUES(?,?,?)",
                    (job_id, checked_at, json.dumps({"posted_at_backfill": True,
                        "posted_at_basis": "unknown", "previous_posted_at": row["posted_at"],
                        "reason": "no_unique_retained_date_or_window_evidence"}, sort_keys=True)),
                )
                cleared_stale += 1
    return LinkedInWindowBackfillResult(
        eligible=len(eligible), exact_or_relative=exact_or_relative, bounded=bounded,
        ambiguous_or_unbounded=max(0, len(eligible) - len(selected)), updated=updated,
        cleared_stale=cleared_stale,
        unresolved_job_ids=tuple(sorted(set(eligible) - set(selected))),
    )
