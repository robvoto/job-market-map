from __future__ import annotations

from typing import Any

from collector.db import connect


def population_stats(codes: list[str]) -> dict[str, Any]:
    with connect() as conn:
        stats: dict[str, Any] = {
            "jobs": int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]),
            "seek_jobs": int(
                conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE source='seek'"
                ).fetchone()[0]
            ),
            "jd_markers": int(
                conn.execute("SELECT COUNT(*) FROM jd_fetch_registry").fetchone()[0]
            ),
            "seek_with_jd": int(
                conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE source='seek' AND full_description IS NOT NULL AND trim(full_description)<>''"
                ).fetchone()[0]
            ),
            "seek_without_jd": int(
                conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE source='seek' AND (full_description IS NULL OR trim(full_description)='')"
                ).fetchone()[0]
            ),
            "seek_unavailable": int(
                conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE source='seek' AND source_status='no_longer_advertised'"
                ).fetchone()[0]
            ),
            "coverage": {},
        }
        for code in codes:
            row = conn.execute(
                """
                SELECT status,reported_results,collected_unique_jobs
                  FROM seek_partitions
                 WHERE geography_code=? AND parent_id IS NULL
                 ORDER BY id DESC LIMIT 1
                """,
                (code,),
            ).fetchone()
            stats["coverage"][code] = (
                {
                    "status": row["status"],
                    "reported": row["reported_results"],
                    "covered": row["collected_unique_jobs"],
                }
                if row
                else {"status": "NOT_RUN", "reported": None, "covered": 0}
            )
    return stats


def log_run_summary(
    log,
    *,
    run_id: int,
    status: str,
    duration_seconds: float,
    baseline: dict[str, Any],
    current: dict[str, Any],
    partitions_processed: int,
    jd_totals: dict[str, int],
) -> None:
    log.info(
        "RUN SUMMARY run_id=%s status=%s duration_s=%.1f jobs=%s delta_jobs=%+d "
        "seek_jobs=%s delta_seek_jobs=%+d jd_markers=%s delta_jds=%+d "
        "seek_with_jd=%s seek_without_jd=%s unavailable=%s delta_unavailable=%+d "
        "partitions=%s jd_attempted=%s jd_stored=%s jd_failed=%s jd_unavailable=%s",
        run_id,
        status,
        duration_seconds,
        current["jobs"],
        current["jobs"] - baseline["jobs"],
        current["seek_jobs"],
        current["seek_jobs"] - baseline["seek_jobs"],
        current["jd_markers"],
        current["jd_markers"] - baseline["jd_markers"],
        current["seek_with_jd"],
        current["seek_without_jd"],
        current["seek_unavailable"],
        current["seek_unavailable"] - baseline["seek_unavailable"],
        partitions_processed,
        jd_totals.get("attempted", 0),
        jd_totals.get("stored", 0),
        jd_totals.get("failed", 0),
        jd_totals.get("unavailable", 0),
    )
    for code, coverage in current["coverage"].items():
        log.info(
            "COVERAGE SUMMARY run_id=%s geography=%s status=%s covered=%s reported=%s",
            run_id,
            code,
            coverage["status"],
            coverage["covered"],
            coverage["reported"],
        )
