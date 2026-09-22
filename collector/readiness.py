from __future__ import annotations

from typing import Any

from collector.campaign import linkedin_campaign_progress
from collector.db import connect, init_db
from collector.service_state import latest_seek_market_run
from collector.source_status import source_status_is_active_sql


def _jd_coverage() -> dict[str, int]:
    """Return mutually exclusive JD readiness counts for active canonical vacancies."""
    init_db()
    active_sql = source_status_is_active_sql("j.source_status")
    with connect() as conn:
        row = conn.execute(
            f"""
            WITH active_primary AS (
                SELECT DISTINCT COALESCE(j.primary_job_id, j.id) AS primary_id
                  FROM jobs j
                  JOIN job_observation_state s ON s.job_id=j.id
                 WHERE COALESCE(s.archived, 0)=0
                   AND {active_sql}
            ),
            failed_primary AS (
                SELECT DISTINCT COALESCE(j.primary_job_id, j.id) AS primary_id
                  FROM jobs j
                  JOIN job_observation_state s ON s.job_id=j.id
                  JOIN jd_enrichment_queue q ON q.job_id=j.id
                 WHERE q.last_error IS NOT NULL
                   AND trim(q.last_error)<>''
                   AND COALESCE(s.archived, 0)=0
                   AND {active_sql}
            )
            SELECT
                SUM(CASE
                    WHEN p.full_description IS NOT NULL
                     AND trim(p.full_description)<>''
                    THEN 1 ELSE 0 END) AS available,
                SUM(CASE
                    WHEN (p.full_description IS NULL OR trim(p.full_description)='')
                     AND f.primary_id IS NULL
                    THEN 1 ELSE 0 END) AS missing_not_cached,
                SUM(CASE
                    WHEN (p.full_description IS NULL OR trim(p.full_description)='')
                     AND f.primary_id IS NOT NULL
                    THEN 1 ELSE 0 END) AS failed
              FROM active_primary a
              JOIN jobs p ON p.id=a.primary_id
              LEFT JOIN failed_primary f ON f.primary_id=a.primary_id
            """
        ).fetchone()
    return {
        "available": int(row["available"] or 0),
        "missing_not_cached": int(row["missing_not_cached"] or 0),
        "failed": int(row["failed"] or 0),
    }


def consumer_readiness() -> dict[str, Any]:
    """Return neutral collection/JD readiness facts for downstream consumers."""
    seek = latest_seek_market_run() or {}
    linkedin = linkedin_campaign_progress() or {}
    return {
        "source_runs": {
            "seek": {
                "status": str(seek.get("status") or "NOT_RUN"),
                "started_at": seek.get("started_at"),
                "finished_at": seek.get("finished_at"),
            },
            "linkedin": {
                "status": str(linkedin.get("status") or "NOT_RUN"),
                "started_at": linkedin.get("started_at"),
                "updated_at": linkedin.get("updated_at"),
                "finished_at": linkedin.get("completed_at"),
            },
        },
        "jd_coverage": _jd_coverage(),
    }
