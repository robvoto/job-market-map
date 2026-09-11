from __future__ import annotations

from typing import Any

from collector.db import connect


def population_stats(codes: list[str]) -> dict[str, Any]:
    with connect() as conn:
        source_jobs = {
            str(row["source"]): int(row["count"])
            for row in conn.execute(
                "SELECT source, COUNT(*) AS count FROM jobs GROUP BY source ORDER BY source"
            ).fetchall()
        }
        stats: dict[str, Any] = {
            "jobs": int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]),
            "source_jobs": source_jobs,
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


def build_run_stats(
    *,
    duration_seconds: float,
    baseline: dict[str, Any],
    current: dict[str, Any],
    partitions_processed: int,
    jd_totals: dict[str, int],
) -> dict[str, Any]:
    baseline_sources = dict(baseline.get("source_jobs") or {})
    current_sources = dict(current.get("source_jobs") or {})
    source_jobs_added = {
        source: int(current_sources.get(source, 0)) - int(baseline_sources.get(source, 0))
        for source in sorted(set(baseline_sources) | set(current_sources))
    }
    return {
        "duration_seconds": round(float(duration_seconds), 1),
        "jobs_total": current["jobs"],
        "jobs_added": current["jobs"] - baseline["jobs"],
        "source_jobs": current_sources,
        "source_jobs_added": source_jobs_added,
        "seek_jobs_total": current["seek_jobs"],
        "seek_jobs_added": current["seek_jobs"] - baseline["seek_jobs"],
        "jd_markers_total": current["jd_markers"],
        "jds_added": current["jd_markers"] - baseline["jd_markers"],
        "seek_with_jd": current["seek_with_jd"],
        "seek_without_jd": current["seek_without_jd"],
        "seek_unavailable_total": current["seek_unavailable"],
        "seek_unavailable_added": current["seek_unavailable"]
        - baseline["seek_unavailable"],
        "partitions_processed": int(partitions_processed),
        "jd_attempted": int(jd_totals.get("attempted", 0)),
        "jd_stored": int(jd_totals.get("stored", 0)),
        "jd_failed": int(jd_totals.get("failed", 0)),
        "jd_unavailable": int(jd_totals.get("unavailable", 0)),
        "coverage": dict(current.get("coverage") or {}),
    }


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
    stats = build_run_stats(
        duration_seconds=duration_seconds,
        baseline=baseline,
        current=current,
        partitions_processed=partitions_processed,
        jd_totals=jd_totals,
    )
    log.info(
        "RUN SUMMARY run_id=%s status=%s duration_s=%.1f jobs=%s delta_jobs=%+d "
        "seek_jobs=%s delta_seek_jobs=%+d jd_markers=%s delta_jds=%+d "
        "seek_with_jd=%s seek_without_jd=%s unavailable=%s delta_unavailable=%+d "
        "partitions=%s jd_attempted=%s jd_stored=%s jd_failed=%s jd_unavailable=%s",
        run_id,
        status,
        stats["duration_seconds"],
        stats["jobs_total"],
        stats["jobs_added"],
        stats["seek_jobs_total"],
        stats["seek_jobs_added"],
        stats["jd_markers_total"],
        stats["jds_added"],
        stats["seek_with_jd"],
        stats["seek_without_jd"],
        stats["seek_unavailable_total"],
        stats["seek_unavailable_added"],
        stats["partitions_processed"],
        stats["jd_attempted"],
        stats["jd_stored"],
        stats["jd_failed"],
        stats["jd_unavailable"],
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
