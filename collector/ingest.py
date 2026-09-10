from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from collector.db import connect, init_db
from collector.duplicates import fingerprints, refresh_duplicate_links
from collector.identity import job_identity_key
from collector.models import CardObservation

TRACKING_QUERY_KEYS = {
    "ref",
    "refid",
    "trackingid",
    "trk",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


@dataclass(frozen=True)
class IngestResult:
    job_id: int
    created: bool
    query_id: int | None
    resurrected: bool = False


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def canonicalise_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        raise ValueError("canonical_url is required")
    parts = urlsplit(value)
    kept = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_QUERY_KEYS
        and not key.casefold().startswith("utm_")
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(kept),
            "",
        )
    )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or None


def _query_id(conn, obs: CardObservation, captured_at: str) -> int | None:
    query_text = _clean(obs.query_text)
    if not query_text:
        return None
    location = _clean(obs.query_location) or ""
    source = _clean(obs.source)
    conn.execute(
        """
        INSERT INTO queries(source, query_text, location, geography_code, active, created_at)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(source, query_text, location) DO UPDATE SET
            geography_code=COALESCE(excluded.geography_code, queries.geography_code)
        """,
        (source, query_text, location, _clean(obs.geography_code), captured_at),
    )
    row = conn.execute(
        "SELECT id FROM queries WHERE source = ? AND query_text = ? AND location = ?",
        (source, query_text, location),
    ).fetchone()
    return int(row[0])


def ingest_card(obs: CardObservation) -> IngestResult:
    init_db()
    source = (_clean(obs.source) or "").casefold()
    if not source:
        raise ValueError("source is required")
    source_job_id = _clean(obs.source_job_id)
    canonical_url = canonicalise_url(obs.canonical_url)
    captured_at = obs.captured_at or utc_now()

    resurrected = False
    with connect() as conn:
        if source_job_id:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE source = ? AND source_job_id = ?",
                (source, source_job_id),
            ).fetchone()
            tombstone = (
                conn.execute(
                    "SELECT * FROM job_tombstones WHERE source=? AND source_job_id=?",
                    (source, source_job_id),
                ).fetchone()
                if not existing
                else None
            )
        else:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE source = ? AND canonical_url = ?",
                (source, canonical_url),
            ).fetchone()
            tombstone = (
                conn.execute(
                    "SELECT * FROM job_tombstones WHERE source=? AND canonical_url=?",
                    (source, canonical_url),
                ).fetchone()
                if not existing
                else None
            )

        fields = {
            "title": _clean(obs.title),
            "employer": _clean(obs.employer),
            "location": _clean(obs.location),
            "geography_code": _clean(obs.geography_code),
            "salary_text": _clean(obs.salary_text),
            "employment_type": _clean(obs.employment_type),
            "workplace_type": _clean(obs.workplace_type),
            "posted_at": _clean(obs.posted_at),
            "applicant_count": obs.applicant_count,
            "teaser_text": _clean(obs.teaser_text),
            "raw_card_text": obs.raw_card_text,
            "classification_text": _clean(obs.classification_text),
            "subclassification_text": _clean(obs.subclassification_text),
            "card_tags_json": json.dumps(obs.card_tags, ensure_ascii=False)
            if obs.card_tags is not None
            else None,
        }

        if existing:
            job_id = int(existing[0])
            assignments = [
                "canonical_url = ?",
                "reposted = CASE WHEN ? THEN 1 ELSE reposted END",
                "easy_apply = COALESCE(?, easy_apply)",
            ]
            values: list[object] = [
                canonical_url,
                int(bool(obs.reposted)),
                None if obs.easy_apply is None else int(obs.easy_apply),
            ]
            for name, value in fields.items():
                if value is not None:
                    assignments.append(f"{name} = ?")
                    values.append(value)
            values.append(job_id)
            conn.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", values
            )
            created = False
        else:
            historical = dict(tombstone) if tombstone else None
            identity = (
                historical["identity_key"]
                if historical
                else job_identity_key(source, source_job_id, canonical_url)
            )
            cur = conn.execute(
                """
                INSERT INTO jobs(
                    source, source_job_id, identity_key, canonical_url, title, employer, location, geography_code,
                    salary_text, employment_type, workplace_type, posted_at,
                    reposted, applicant_count, easy_apply, teaser_text, raw_card_text,
                    classification_text, subclassification_text, card_tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    source_job_id,
                    identity,
                    canonical_url,
                    fields["title"],
                    fields["employer"],
                    fields["location"],
                    fields["geography_code"],
                    fields["salary_text"],
                    fields["employment_type"],
                    fields["workplace_type"],
                    fields["posted_at"],
                    int(bool(obs.reposted)),
                    fields["applicant_count"],
                    None if obs.easy_apply is None else int(obs.easy_apply),
                    fields["teaser_text"],
                    fields["raw_card_text"],
                    fields["classification_text"],
                    fields["subclassification_text"],
                    fields["card_tags_json"],
                ),
            )
            job_id = int(cur.lastrowid)
            resurrected = historical is not None
            created = not resurrected
            if historical:
                conn.execute(
                    "DELETE FROM job_tombstones WHERE id=?", (historical["id"],)
                )

        if existing:
            conn.execute(
                """
                INSERT INTO job_observation_state(
                    job_id,first_seen_at,last_seen_at,capture_count,archived,compacted_at
                ) VALUES(?,?,?,1,0,NULL)
                ON CONFLICT(job_id) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    capture_count=job_observation_state.capture_count+1,
                    archived=0,
                    compacted_at=NULL
                """,
                (job_id, captured_at, captured_at),
            )
        else:
            first_seen_at = historical["first_seen_at"] if historical else captured_at
            prior_capture_count = int(historical["capture_count"]) if historical else 0
            conn.execute(
                """
                INSERT INTO job_observation_state(
                    job_id,first_seen_at,last_seen_at,capture_count,archived,compacted_at
                ) VALUES(?,?,?,?,0,NULL)
                """,
                (job_id, first_seen_at, captured_at, prior_capture_count + 1),
            )

        stored_row = dict(
            conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        )
        core_fingerprint, exact_card_fingerprint = fingerprints(stored_row)
        conn.execute(
            "UPDATE jobs SET core_fingerprint=?, exact_card_fingerprint=? WHERE id=?",
            (core_fingerprint, exact_card_fingerprint, job_id),
        )

        query_id = _query_id(conn, obs, captured_at)
        capture_json = dict(obs.raw_json or {})
        if _clean(obs.posted_text):
            capture_json.setdefault("posted_text", _clean(obs.posted_text))
        conn.execute(
            """
            INSERT INTO card_captures(job_id, captured_at, query_id, rank, page_number, raw_card_text, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                captured_at,
                query_id,
                obs.rank,
                obs.page_number,
                obs.raw_card_text,
                json.dumps(capture_json, ensure_ascii=False, sort_keys=True)
                if capture_json
                else None,
            ),
        )

        if query_id is not None:
            conn.execute(
                """
                INSERT INTO job_query_hits(job_id, query_id, first_seen_at, last_seen_at, hit_count)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(job_id, query_id) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    hit_count = job_query_hits.hit_count + 1
                """,
                (job_id, query_id, captured_at, captured_at),
            )
            conn.execute(
                """
                UPDATE queries
                   SET cards_observed = cards_observed + 1,
                       unique_new_jobs = unique_new_jobs + ?,
                       duplicate_hits = duplicate_hits + ?
                 WHERE id = ?
                """,
                (int(created), int(not created), query_id),
            )

    refresh_duplicate_links(job_id)
    return IngestResult(
        job_id=job_id, created=created, query_id=query_id, resurrected=resurrected
    )
