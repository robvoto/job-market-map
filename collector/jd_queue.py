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


def pending_primary_ids(*, source: str | None = None) -> list[int]:
    """Return distinct active canonical vacancies represented by pending source rows."""
    init_db()
    params: list[object] = []
    source_clause = ""
    if source:
        source_clause = " AND q.source=?"
        params.append(str(source).strip().casefold())
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
               {source_clause}
             ORDER BY primary_id
            """,
            params,
        ).fetchall()
    return [int(row["primary_id"]) for row in rows]


def record_pending_attempt(primary_job_id: int, *, error: str | None) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            UPDATE jd_enrichment_queue
               SET attempts=attempts+1,
                   last_attempt_at=datetime('now'),
                   last_error=?
             WHERE job_id IN (
                 SELECT id FROM jobs WHERE id=? OR primary_job_id=?
             )
            """,
            (str(error) if error else None, int(primary_job_id), int(primary_job_id)),
        )
