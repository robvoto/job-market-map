"""Bounded, source-only repair for missing canonical posting dates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from collector.db import connect, update_job_source_facts
from collector.linkedin_detail import LinkedInDetailError, fetch_linkedin_detail
from collector.posting_dates import normalize_posting_date


@dataclass(frozen=True)
class PostedAtRepairResult:
    candidates: int
    checked: int
    filled: int
    closed: int
    without_usable_date: int
    failures: int


def repair_linkedin_posted_at(*, limit: int) -> PostedAtRepairResult:
    """Fill missing LinkedIn dates from a current source detail recheck.

    A source page may not expose its original date. The evidence outcome is
    persisted so the same page is not requested again indefinitely.
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    with connect() as conn:
        candidates = [
            dict(row)
            for row in conn.execute(
                """
                SELECT jobs.id, jobs.canonical_url, jobs.posted_at,
                       CASE WHEN jobs.posted_at IS NOT NULL AND trim(jobs.posted_at)<>''
                                 AND julianday(jobs.posted_at) IS NOT NULL THEN 1 ELSE 0 END AS posted_valid,
                       field_state.state AS posted_state
                FROM jobs
                LEFT JOIN job_field_states field_state
                  ON field_state.job_id=jobs.id AND field_state.field_name='posted_at'
                WHERE jobs.source = 'linkedin'
                  AND ((jobs.posted_at IS NULL OR trim(jobs.posted_at)='')
                       OR (jobs.posted_at IS NOT NULL AND trim(jobs.posted_at)<>''
                           AND julianday(jobs.posted_at) IS NULL)
                       OR field_state.state='not_present')
                  AND jobs.source_status IS NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM posted_at_repair_attempts AS attempt
                      WHERE attempt.job_id = jobs.id
                  )
                ORDER BY id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]

    checked = filled = closed = without_usable_date = failures = 0
    for job in candidates:
        checked += 1
        try:
            detail = fetch_linkedin_detail(str(job["canonical_url"]))
        except LinkedInDetailError:
            failures += 1
            continue
        checked_at = datetime.now(UTC).isoformat(timespec="seconds")
        posting_date = normalize_posting_date(
            posted_at=detail.posted_at,
            posted_text=detail.header_text,
            captured_at=checked_at,
        )
        facts = detail.facts
        if posting_date:
            facts["posted_at"] = posting_date.value
            facts["posted_at_basis"] = posting_date.basis
            update_job_source_facts(int(job["id"]), **facts)
        else:
            update_job_source_facts(
                int(job["id"]),
                **{key: value for key, value in facts.items() if key not in {"posted_at", "posted_at_basis"}},
            )
            if job["posted_state"] == "not_present" or not job["posted_valid"]:
                with connect() as conn:
                    conn.execute(
                        "UPDATE jobs SET posted_at=NULL,posted_at_basis=NULL WHERE id=?",
                        (int(job["id"]),),
                    )
        if posting_date:
            filled += 1
            outcome = "filled"
        elif detail.source_status:
            closed += 1
            outcome = "closed_without_usable_date"
        else:
            without_usable_date += 1
            outcome = "source_date_unavailable"
        with connect() as conn:
            conn.execute(
                """INSERT INTO card_captures(job_id,captured_at,raw_json)
                   VALUES(?,?,?)""",
                (
                    int(job["id"]),
                    checked_at,
                    json.dumps(
                        {
                            "posted_text": detail.header_text,
                            "posted_at_normalized": posting_date.value if posting_date else None,
                            "posted_at_basis": posting_date.basis if posting_date else "unknown",
                            "previous_posted_at": job["posted_at"],
                            "repair_recheck": True,
                        },
                        sort_keys=True,
                    ),
                ),
            )
            conn.execute(
                """INSERT INTO job_field_states(
                           job_id,field_name,state,evidence_source,checked_at
                       ) VALUES(?, 'posted_at', ?, ?, ?)
                       ON CONFLICT(job_id,field_name) DO UPDATE SET
                           state=excluded.state,evidence_source=excluded.evidence_source,
                           checked_at=excluded.checked_at""",
                (
                    int(job["id"]),
                    "known" if posting_date else "unknown",
                    "repair:linkedin_detail" if posting_date else "repair:linkedin_no_date",
                    checked_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO posted_at_repair_attempts(job_id, source, outcome, attempted_at)
                VALUES (?, 'linkedin', ?, ?)
                ON CONFLICT(job_id) DO NOTHING
                """,
                (
                    int(job["id"]),
                    outcome,
                    checked_at,
                ),
            )
    return PostedAtRepairResult(
        candidates=len(candidates),
        checked=checked,
        filled=filled,
        closed=closed,
        without_usable_date=without_usable_date,
        failures=failures,
    )
