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


def _activity_predicate(preserve_activity_jobs: bool) -> str:
    if not preserve_activity_jobs:
        return "1=1"
    return "NOT EXISTS (SELECT 1 FROM user_job_activity_events a WHERE a.job_identity_key=jobs.identity_key)"


def apply_retention(
    *,
    raw_capture_days: int | None = None,
    archive_after_days: int | None = None,
    remove_archived_after_days: int | None = None,
    preserve_activity_jobs: bool | None = None,
    now: datetime | None = None,
) -> RetentionResult:
    """Apply configurable archive-then-remove policy while retaining tombstone identity."""
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
    preserve = bool(
        preserve_activity_jobs
        if preserve_activity_jobs is not None
        else get_setting("retention.preserve_activity_jobs_forever")
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
    activity_clause = _activity_predicate(preserve)

    with connect() as conn:
        captures_deleted = conn.execute(
            "DELETE FROM card_captures WHERE captured_at < ?",
            (raw_cutoff,),
        ).rowcount

        jobs_archived = conn.execute(
            f"""
            UPDATE jobs
               SET archived=1,
                   compacted_at=?,
                   teaser_text=NULL,
                   raw_card_text=NULL
             WHERE last_seen_at < ?
               AND archived=0
               AND {activity_clause}
            """,
            (archived_at, archive_cutoff),
        ).rowcount

        removable = conn.execute(
            f"""
            SELECT * FROM jobs
             WHERE archived=1
               AND last_seen_at < ?
               AND {activity_clause}
             ORDER BY id
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
    """Compatibility wrapper: use one horizon for capture pruning/archive, normal configured removal horizon."""
    return apply_retention(raw_capture_days=days, archive_after_days=days, now=now)


if __name__ == "__main__":
    result = apply_retention()
    print(
        f"captures_deleted={result.captures_deleted} jobs_archived={result.jobs_archived} "
        f"jobs_removed={result.jobs_removed} tombstones_written={result.tombstones_written}"
    )
