from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from collector.db import connect, init_db
from collector.geographies import list_geographies
from collector.run_logging import collection_logger
from sources.seek_market_map import (
    MarketMapResult,
    SeekHumanCheckRequired,
    collect_seek_state,
)


@dataclass(frozen=True)
class SeekCycleResult:
    status: str
    states: list[str]
    partitions_processed: int
    state_results: list[MarketMapResult]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def enabled_state_codes() -> list[str]:
    return [row["code"] for row in list_geographies(enabled_only=True)]


def state_root(code: str) -> dict | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM seek_partitions
             WHERE geography_code=? AND parent_id IS NULL
             ORDER BY id DESC LIMIT 1
            """,
            (code,),
        ).fetchone()
    return dict(row) if row else None


def all_states_complete(codes: list[str]) -> bool:
    if not codes:
        return False
    return all(
        (root := state_root(code)) is not None
        and str(root["status"]).startswith("COMPLETE")
        for code in codes
    )


def snapshot_and_reset_coverage(codes: list[str]) -> None:
    """Archive summary evidence, then reset only the current SEEK coverage workspace.

    Canonical jobs remain untouched. This reset exists so a new daily coverage cycle
    cannot inherit yesterday's partition memberships and falsely look complete.
    """
    if not codes:
        return
    init_db()
    placeholders = ",".join("?" for _ in codes)
    captured_at = _now()
    with connect() as conn:
        for code in codes:
            root = conn.execute(
                """
                SELECT * FROM seek_partitions
                 WHERE geography_code=? AND parent_id IS NULL
                 ORDER BY id DESC LIMIT 1
                """,
                (code,),
            ).fetchone()
            incomplete = int(
                conn.execute(
                    "SELECT COUNT(*) FROM seek_partitions WHERE geography_code=? AND status NOT LIKE 'COMPLETE%'",
                    (code,),
                ).fetchone()[0]
            )
            if root:
                conn.execute(
                    """
                    INSERT INTO seek_coverage_history(
                        captured_at,geography_code,root_status,reported_results,
                        covered_unique_jobs,incomplete_partitions
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        captured_at,
                        code,
                        root["status"],
                        root["reported_results"],
                        root["collected_unique_jobs"],
                        incomplete,
                    ),
                )
        conn.execute(
            f"""
            DELETE FROM seek_partition_jobs
             WHERE partition_id IN (
                 SELECT id FROM seek_partitions WHERE geography_code IN ({placeholders})
             )
            """,
            codes,
        )
        conn.execute(
            f"""
            UPDATE seek_partitions
               SET status='PENDING', reported_results=NULL, collected_unique_jobs=0,
                   child_count=0, completed_at=NULL, last_error=NULL, updated_at=?
             WHERE geography_code IN ({placeholders})
            """,
            (captured_at, *codes),
        )


def run_seek_cycle(
    *,
    page_id: int,
    codes: list[str],
    days: int | None = None,
    should_stop: Callable[[], bool],
    deadline_reached: Callable[[], bool],
    after_progress: Callable[[MarketMapResult], None] | None = None,
    cutoff_at: datetime | None = None,
) -> SeekCycleResult:
    latest: dict[str, MarketMapResult] = {}
    total_processed = 0
    while True:
        if should_stop():
            return SeekCycleResult(
                "STOPPED", codes, total_processed, list(latest.values())
            )
        if deadline_reached():
            return SeekCycleResult(
                "PARTIAL_TIME_LIMIT", codes, total_processed, list(latest.values())
            )
        if all_states_complete(codes):
            return SeekCycleResult(
                "COMPLETE", codes, total_processed, list(latest.values())
            )

        pass_progress = 0
        for code in codes:
            if should_stop() or deadline_reached():
                break
            try:
                result = collect_seek_state(
                    code,
                    page_id=page_id,
                    days=days,
                    max_partitions=1,
                    resume=True,
                    cutoff_at=cutoff_at,
                    should_stop=should_stop,
                )
            except InterruptedError:
                return SeekCycleResult(
                    "STOPPED", codes, total_processed, list(latest.values())
                )
            except SeekHumanCheckRequired as exc:
                collection_logger().warning(
                    "SEEK coverage blocked by human verification; leaving retryable geography=%s error=%s",
                    code,
                    exc,
                )
                return SeekCycleResult(
                    "BLOCKED_HUMAN", codes, total_processed, list(latest.values())
                )
            latest[code] = result
            pass_progress += result.partitions_processed
            total_processed += result.partitions_processed
            if result.partitions_processed and after_progress is not None:
                after_progress(result)

        if all_states_complete(codes):
            return SeekCycleResult(
                "COMPLETE", codes, total_processed, list(latest.values())
            )
        if should_stop():
            return SeekCycleResult(
                "STOPPED", codes, total_processed, list(latest.values())
            )
        if deadline_reached():
            return SeekCycleResult(
                "PARTIAL_TIME_LIMIT", codes, total_processed, list(latest.values())
            )
        if pass_progress == 0:
            return SeekCycleResult(
                "BLOCKED_INCOMPLETE", codes, total_processed, list(latest.values())
            )
