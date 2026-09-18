from __future__ import annotations

from collector.db import connect, init_db
from collector.source_status import source_status_is_active_sql


def enqueue_job_for_jd(job_id: int, *, source: str, queued_at: str) -> bool:
    """Queue one newly observed source posting if its canonical vacancy still lacks a JD."""
    init_db()
    source = str(source or "").strip().casefold()
    queued_at = str(queued_at or "").strip()
    if not source or not queued_at:
        raise ValueError("source and queued_at are required")
    with connect() as conn:
        row = conn.execute(
            """
            SELECT j.id,COALESCE(j.primary_job_id,j.id) AS primary_id,p.full_description
              FROM jobs j
              JOIN jobs p ON p.id=COALESCE(j.primary_job_id,j.id)
             WHERE j.id=?
            """,
            (int(job_id),),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        if str(row["full_description"] or "").strip():
            return False
        conn.execute(
            """
            INSERT INTO jd_enrichment_queue(job_id,source,queued_at,attempts,last_attempt_at,last_error)
            VALUES(?,?,?,0,NULL,NULL)
            ON CONFLICT(job_id) DO NOTHING
            """,
            (int(job_id), source, queued_at),
        )
        return bool(conn.execute("SELECT changes()").fetchone()[0])




def enqueue_primaries_for_source(
    primary_job_ids: list[int], *, source: str, queued_at: str
) -> int:
    """Queue active source postings for canonical vacancies in one bulk DB operation."""
    init_db()
    source_value = str(source or "").strip().casefold()
    queued_at_value = str(queued_at or "").strip()
    if not source_value or not queued_at_value:
        raise ValueError("source and queued_at are required")
    ids = sorted({int(value) for value in primary_job_ids})
    if not ids:
        return 0
    with connect() as conn:
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _jd_queue_primary_ids(id INTEGER PRIMARY KEY)"
        )
        conn.execute("DELETE FROM _jd_queue_primary_ids")
        conn.executemany(
            "INSERT INTO _jd_queue_primary_ids(id) VALUES(?)",
            ((value,) for value in ids),
        )
        conn.execute(
            f"""
            INSERT OR IGNORE INTO jd_enrichment_queue(
                job_id,source,queued_at,attempts,last_attempt_at,last_error
            )
            SELECT j.id,j.source,?,0,NULL,NULL
              FROM jobs j
              JOIN job_observation_state s ON s.job_id=j.id
              JOIN jobs p ON p.id=COALESCE(j.primary_job_id,j.id)
              JOIN _jd_queue_primary_ids r
                ON r.id=COALESCE(j.primary_job_id,j.id)
             WHERE j.source=?
               AND COALESCE(s.archived,0)=0
               AND {source_status_is_active_sql('j.source_status')}
               AND (p.full_description IS NULL OR trim(p.full_description)='')
            """,
            (queued_at_value, source_value),
        )
        added = int(conn.execute("SELECT changes()").fetchone()[0])
        conn.execute("DELETE FROM _jd_queue_primary_ids")
    return added


def enqueue_primary_for_source(primary_job_id: int, *, source: str, queued_at: str) -> bool:
    return bool(
        enqueue_primaries_for_source(
            [primary_job_id], source=source, queued_at=queued_at
        )
    )


def seed_new_missing_since(*, source: str, first_seen_since: str) -> int:
    """Queue only source rows first discovered after the supplied boundary."""
    init_db()
    source = str(source or "").strip().casefold()
    with connect() as conn:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO jd_enrichment_queue(
                job_id,source,queued_at,attempts,last_attempt_at,last_error
            )
            SELECT j.id,j.source,s.first_seen_at,0,NULL,NULL
              FROM jobs j
              JOIN job_observation_state s ON s.job_id=j.id
              JOIN jobs p ON p.id=COALESCE(j.primary_job_id,j.id)
             WHERE j.source=?
               AND datetime(s.first_seen_at) >= datetime(?)
               AND COALESCE(s.archived,0)=0
               AND {source_status_is_active_sql('j.source_status')}
               AND (p.full_description IS NULL OR trim(p.full_description)='')
            """,
            (source, first_seen_since),
        )
        return int(conn.execute("SELECT changes()").fetchone()[0])


def pending_primary_ids(
    *,
    source: str | None = None,
    queued_since: str | None = None,
    limit: int | None = None,
) -> list[int]:
    """Return distinct active canonical vacancies represented by pending source rows."""
    init_db()
    params: list[object] = []
    source_clause = ""
    if source:
        source_clause = " AND q.source=?"
        params.append(str(source).strip().casefold())
    recency_clause = ""
    if queued_since:
        # Normal collection drains work created in the current operating window,
        # plus rows deliberately carried forward or previously attempted. This
        # keeps old untouched repair debt out of the live path while preserving
        # legitimate current-run work across a deadline/stop.
        recency_clause = (
            " AND (datetime(q.queued_at) >= datetime(?) "
            "OR q.attempts > 0 OR q.last_error IS NOT NULL)"
        )
        params.append(str(queued_since))
    limit_clause = ""
    if limit is not None:
        if int(limit) < 1:
            return []
        limit_clause = " LIMIT ?"
        params.append(int(limit))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT COALESCE(j.primary_job_id,j.id) AS primary_id
              FROM jd_enrichment_queue q
              JOIN jobs j ON j.id=q.job_id
              JOIN job_observation_state s ON s.job_id=j.id
              JOIN jobs p ON p.id=COALESCE(j.primary_job_id,j.id)
             WHERE COALESCE(s.archived,0)=0
               AND {source_status_is_active_sql('j.source_status')}
               AND (p.full_description IS NULL OR trim(p.full_description)='')
               AND q.attempts < 2
               {source_clause}
               {recency_clause}
             ORDER BY primary_id
             {limit_clause}
            """,
            params,
        ).fetchall()
    return [int(row["primary_id"]) for row in rows]


def mark_pending_deferred(
    *, source: str, queued_since: str, reason: str
) -> int:
    """Carry current-window untouched JD work into the next normal run."""
    init_db()
    source_value = str(source or "").strip().casefold()
    queued_since_value = str(queued_since or "").strip()
    reason_value = str(reason or "").strip()
    if not source_value or not queued_since_value or not reason_value:
        raise ValueError("source, queued_since and reason are required")
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE jd_enrichment_queue
               SET last_error=?
             WHERE source=?
               AND datetime(queued_at) >= datetime(?)
               AND attempts < 2
               AND last_error IS NULL
               AND job_id IN (
                   SELECT j.id
                     FROM jobs j
                     JOIN job_observation_state s ON s.job_id=j.id
                     JOIN jobs p ON p.id=COALESCE(j.primary_job_id,j.id)
                    WHERE COALESCE(s.archived,0)=0
                      AND {source_status_is_active_sql('j.source_status')}
                      AND (p.full_description IS NULL OR trim(p.full_description)='')
               )
            """,
            (reason_value, source_value, queued_since_value),
        )
        return int(conn.execute("SELECT changes()").fetchone()[0])


def record_pending_attempt(
    primary_job_id: int, *, source: str | None = None, error: str | None
) -> None:
    init_db()
    source_value = str(source or "").strip().casefold()
    source_clause = " AND source=?" if source_value else ""
    params: list[object] = [
        str(error) if error else None,
        int(primary_job_id),
        int(primary_job_id),
    ]
    if source_value:
        params.append(source_value)
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE jd_enrichment_queue
               SET attempts=attempts+1,
                   last_attempt_at=datetime('now'),
                   last_error=?
             WHERE job_id IN (
                 SELECT id FROM jobs WHERE id=? OR primary_job_id=?
             )
               {source_clause}
            """,
            params,
        )
