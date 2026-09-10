from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from collector.db import connect, init_db
from collector.settings import get_setting


@dataclass(frozen=True)
class RetentionResult:
    captures_deleted: int
    jobs_archived: int
    jobs_removed: int
    tombstones_written: int

    @property
    def jobs_compacted(self) -> int:
        """Backward-compatible name used by early tests/docs."""
        return self.jobs_archived


def _current(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _cutoff_iso(days: int, now: datetime | None = None) -> str:
    return (_current(now) - timedelta(days=days)).isoformat(timespec="seconds")


def apply_retention(
    *,
    raw_capture_days: int | None = None,
    archive_after_days: int | None = None,
    remove_archived_after_days: int | None = None,
    prune_raw_captures_enabled: bool | None = None,
    archive_jobs_enabled: bool | None = None,
    remove_archived_jobs_enabled: bool | None = None,
    now: datetime | None = None,
) -> RetentionResult:
    """Apply explicitly enabled retention phases.

    Historical market/card evidence is useful for later audit, scam/phishing
    investigation, and learning from application outcomes. Therefore destructive
    retention is fail-safe: every destructive phase is OFF by default and must be
    explicitly enabled in Admin/settings (or by a deliberate caller override).
    """
    init_db()
    raw_days = int(
        raw_capture_days
        if raw_capture_days is not None
        else get_setting("retention.raw_capture_days")
    )
    archive_days = int(
        archive_after_days
        if archive_after_days is not None
        else get_setting("retention.archive_after_days")
    )
    remove_days = int(
        remove_archived_after_days
        if remove_archived_after_days is not None
        else get_setting("retention.remove_archived_after_days")
    )
    prune_raw = bool(
        get_setting("retention.prune_raw_captures_enabled")
        if prune_raw_captures_enabled is None
        else prune_raw_captures_enabled
    )
    archive_jobs = bool(
        get_setting("retention.archive_jobs_enabled")
        if archive_jobs_enabled is None
        else archive_jobs_enabled
    )
    remove_archived = bool(
        get_setting("retention.remove_archived_jobs_enabled")
        if remove_archived_jobs_enabled is None
        else remove_archived_jobs_enabled
    )
    if min(raw_days, archive_days, remove_days) < 1:
        raise ValueError("retention days must be >= 1")
    if remove_days <= archive_days:
        raise ValueError(
            "remove_archived_after_days must be greater than archive_after_days"
        )

    current = _current(now)
    archived_at = current.isoformat(timespec="seconds")
    raw_cutoff = _cutoff_iso(raw_days, current)
    archive_cutoff = _cutoff_iso(archive_days, current)
    remove_cutoff = _cutoff_iso(remove_days, current)

    with connect() as conn:
        # Age thresholds describe *when* cleanup may happen; they are not
        # permission to destroy evidence. The switches below are the policy gates.
        captures_deleted = 0
        if prune_raw:
            captures_deleted = conn.execute(
                "DELETE FROM card_captures WHERE captured_at < ?",
                (raw_cutoff,),
            ).rowcount

        jobs_archived = 0
        if archive_jobs:
            archive_ids = [
                int(row[0])
                for row in conn.execute(
                    """
                    SELECT job_id FROM job_observation_state
                     WHERE last_seen_at < ? AND archived=0
                    """,
                    (archive_cutoff,),
                )
            ]
            if archive_ids:
                placeholders = ",".join("?" for _ in archive_ids)
                conn.execute(
                    f"UPDATE job_observation_state SET archived=1, compacted_at=? WHERE job_id IN ({placeholders})",
                    (archived_at, *archive_ids),
                )
                conn.execute(
                    f"UPDATE jobs SET teaser_text=NULL, raw_card_text=NULL WHERE id IN ({placeholders})",
                    archive_ids,
                )
                jobs_archived = len(archive_ids)

        removable = []
        if remove_archived:
            removable = conn.execute(
                """
                SELECT j.*, s.first_seen_at, s.last_seen_at, s.capture_count
                  FROM jobs j
                  JOIN job_observation_state s ON s.job_id=j.id
                 WHERE s.archived=1
                   AND s.last_seen_at < ?
                 ORDER BY j.id
                """,
                (remove_cutoff,),
            ).fetchall()

        tombstones_written = 0
        jobs_removed = 0
        for row in removable:
            job = dict(row)
            history = [
                dict(item)
                for item in conn.execute(
                    """
                    SELECT q.registry_key, q.source, q.query_text, q.location,
                           h.first_seen_at, h.last_seen_at, h.hit_count
                      FROM job_query_hits h
                      JOIN queries q ON q.id=h.query_id
                     WHERE h.job_id=?
                     ORDER BY q.source, q.query_text, q.location
                    """,
                    (job["id"],),
                )
            ]
            conn.execute(
                """
                INSERT INTO job_tombstones(
                    source, source_job_id, identity_key, canonical_url, title, employer, location,
                    core_fingerprint, exact_card_fingerprint, first_seen_at, last_seen_at,
                    capture_count, query_history_json, removed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, canonical_url) DO UPDATE SET
                    source_job_id=excluded.source_job_id,
                    identity_key=excluded.identity_key,
                    title=excluded.title,
                    employer=excluded.employer,
                    location=excluded.location,
                    core_fingerprint=excluded.core_fingerprint,
                    exact_card_fingerprint=excluded.exact_card_fingerprint,
                    first_seen_at=MIN(job_tombstones.first_seen_at, excluded.first_seen_at),
                    last_seen_at=MAX(job_tombstones.last_seen_at, excluded.last_seen_at),
                    capture_count=MAX(job_tombstones.capture_count, excluded.capture_count),
                    query_history_json=excluded.query_history_json,
                    removed_at=excluded.removed_at
                """,
                (
                    job["source"],
                    job["source_job_id"],
                    job["identity_key"],
                    job["canonical_url"],
                    job["title"],
                    job["employer"],
                    job["location"],
                    job["core_fingerprint"],
                    job["exact_card_fingerprint"],
                    job["first_seen_at"],
                    job["last_seen_at"],
                    job["capture_count"],
                    json.dumps(history, ensure_ascii=False),
                    archived_at,
                ),
            )
            tombstones_written += 1
            conn.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
            jobs_removed += 1

    return RetentionResult(
        captures_deleted=captures_deleted,
        jobs_archived=jobs_archived,
        jobs_removed=jobs_removed,
        tombstones_written=tombstones_written,
    )


def compact_stale_data(days: int = 30, now: datetime | None = None) -> RetentionResult:
    """Compatibility wrapper for an explicitly requested prune/archive operation.

    Calling this legacy helper is itself an explicit cleanup request, so it enables
    only the two phases named by its historical behaviour. Normal scheduled/Admin
    retention remains governed by the OFF-by-default settings switches.
    """
    return apply_retention(
        raw_capture_days=days,
        archive_after_days=days,
        prune_raw_captures_enabled=True,
        archive_jobs_enabled=True,
        remove_archived_jobs_enabled=False,
        now=now,
    )


if __name__ == "__main__":
    result = apply_retention()
    print(
        f"captures_deleted={result.captures_deleted} jobs_archived={result.jobs_archived} "
        f"jobs_removed={result.jobs_removed} tombstones_written={result.tombstones_written}"
    )
