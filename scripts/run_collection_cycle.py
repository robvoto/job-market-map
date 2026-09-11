from __future__ import annotations

import argparse
import signal
import time
from dataclasses import asdict
from threading import Event

from collector.backup import create_backup
from collector.browser_broker import close_browser, close_tab, open_tab
from collector.run_lock import CollectionAlreadyRunning, collection_run_lock
from collector.run_logging import LOG_PATH, configure_collection_logging
from collector.run_stats import log_run_summary, population_stats
from collector.seek_cycle import (
    all_states_complete,
    enabled_state_codes,
    run_seek_cycle,
    snapshot_and_reset_coverage,
)
from collector.seek_jd import enrich_seek_coverage_jds
from collector.service_state import (
    finish_market_run,
    set_market_run_backup,
    start_market_run,
    update_scheduler_state,
    utc_now,
)
from collector.settings import get_setting


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one safe SEEK discovery + write-once JD collection cycle."
    )
    parser.add_argument("--trigger", choices=["manual", "scheduled"], default="manual")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override the normal freshness window for this run only (initial load uses 3).",
    )
    parser.add_argument(
        "--backfill-existing-jds",
        action="store_true",
        help="Also fetch JDs for older SEEK rows that have never had a successful JD fetch.",
    )
    parser.add_argument(
        "--max-runtime-minutes",
        type=int,
        default=None,
        help="Override the runtime limit for this run only; 0 means no time limit.",
    )
    args = parser.parse_args(argv)
    log = configure_collection_logging()
    log.info(
        "runner invoked trigger=%s argv=%s log=%s", args.trigger, argv or [], LOG_PATH
    )
    if args.days is not None and args.days < 1:
        parser.error("--days must be >= 1")
    if args.max_runtime_minutes is not None and args.max_runtime_minutes < 0:
        parser.error("--max-runtime-minutes must be >= 0")

    stop_event = Event()
    run_started = time.monotonic()
    baseline_stats = None
    run_codes: list[str] = []
    run_partitions_processed = 0
    list_page_id: int | None = None
    detail_page_id: int | None = None
    jd_totals = {"attempted": 0, "stored": 0, "failed": 0, "unavailable": 0}

    def request_stop(*_args) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    try:
        with collection_run_lock(args.trigger):
            codes = enabled_state_codes()
            run_codes = list(codes)
            baseline_stats = population_stats(codes)
            if not codes:
                log.error("no enabled SEEK geographies")
                return 2

            days = int(
                args.days
                if args.days is not None
                else get_setting("collection.default_freshness_days")
            )
            mode = "fresh" if all_states_complete(codes) else "resume"
            run_id = start_market_run(
                trigger=args.trigger,
                mode=mode,
                states=codes,
                backup_path=None,
            )
            log.info(
                "run started run_id=%s mode=%s days=%s states=%s backfill_existing_jds=%s max_runtime_minutes=%s",
                run_id,
                mode,
                days,
                ",".join(codes),
                args.backfill_existing_jds,
                args.max_runtime_minutes,
            )

            if bool(get_setting("backup.before_collection_enabled")):
                backup = create_backup()
                set_market_run_backup(run_id, backup.path)
                log.info("backup verified path=%s", backup.path)

            if mode == "fresh":
                snapshot_and_reset_coverage(codes)
            if args.trigger == "scheduled":
                update_scheduler_state(
                    last_started_at=utc_now(),
                    last_status="RUNNING",
                    last_message=f"Scheduled SEEK collection started ({mode}, {days}d).",
                )

            configured_limit = int(get_setting("scheduler.max_runtime_minutes"))
            max_minutes = (
                args.max_runtime_minutes
                if args.max_runtime_minutes is not None
                else configured_limit
            )

            def deadline_reached() -> bool:
                return (
                    bool(max_minutes)
                    and (time.monotonic() - run_started) >= max_minutes * 60
                )

            list_page_id = int(open_tab("about:blank", active=False).result["pageId"])
            detail_page_id = int(open_tab("about:blank", active=False).result["pageId"])
            log.info(
                "browser pages ready list_page_id=%s detail_page_id=%s",
                list_page_id,
                detail_page_id,
            )
            jd_result = None

            def record_jd_progress(event: str) -> None:
                if event in jd_totals:
                    jd_totals[event] += 1

            def sweep_required_jds(*, include_existing_unfetched: bool = False) -> None:
                nonlocal jd_result
                if stop_event.is_set() or deadline_reached():
                    return
                log.info(
                    "JD sweep started include_existing_unfetched=%s",
                    include_existing_unfetched,
                )
                jd_result = enrich_seek_coverage_jds(
                    page_id=detail_page_id,
                    codes=codes,
                    days=days,
                    should_stop=stop_event.is_set,
                    deadline_reached=deadline_reached,
                    include_existing_unfetched=include_existing_unfetched,
                    on_progress=record_jd_progress,
                )
                log.info(
                    "JD sweep finished candidates=%s cached=%s attempted=%s stored=%s failed=%s unavailable=%s remaining=%s",
                    jd_result.candidates,
                    jd_result.cached,
                    jd_result.attempted,
                    jd_result.stored,
                    jd_result.failed,
                    jd_result.unavailable,
                    jd_result.remaining,
                )

            # A JMM-007 pass is full-evidence, not card-only. Catch up any jobs
            # already discovered by an interrupted/resumed pass before collecting more.
            sweep_required_jds()

            def after_coverage_progress(progress) -> None:
                log.info(
                    "coverage progress geography=%s status=%s reported=%s covered=%s incomplete=%s partitions_processed=%s",
                    progress.geography_code,
                    progress.status,
                    progress.reported_results,
                    progress.covered_unique_jobs,
                    progress.incomplete_partitions,
                    progress.partitions_processed,
                )
                # Do not let coverage run thousands of jobs ahead of JD acquisition.
                # Every completed partition is followed by a write-once JD catch-up.
                sweep_required_jds()

            result = run_seek_cycle(
                page_id=list_page_id,
                codes=codes,
                days=days,
                should_stop=stop_event.is_set,
                deadline_reached=deadline_reached,
                after_progress=after_coverage_progress,
            )

            run_partitions_processed = result.partitions_processed

            # Final sweep proves the pass has the required JDs. The one-off legacy
            # backfill is included only after current 3-day coverage is complete.
            if result.status == "COMPLETE":
                sweep_required_jds(
                    include_existing_unfetched=args.backfill_existing_jds
                )

            if stop_event.is_set():
                final_status = "STOPPED"
            elif deadline_reached() and (jd_result is None or jd_result.remaining):
                final_status = "PARTIAL_TIME_LIMIT"
            elif result.status != "COMPLETE":
                final_status = result.status
            elif jd_result is None or jd_result.remaining:
                final_status = "PARTIAL_JD"
            else:
                final_status = "COMPLETE"

            message = (
                f"SEEK {mode} {days}d cycle {final_status.lower()}; "
                f"processed {result.partitions_processed} partition(s)."
            )
            if jd_result is not None:
                message += (
                    f" JD candidates={jd_result.candidates}, cached={jd_result.cached}, "
                    f"stored={jd_result.stored}, failed={jd_result.failed}, "
                    f"unavailable={jd_result.unavailable}, remaining={jd_result.remaining}."
                )
            finish_market_run(run_id, status=final_status, message=message)
            if args.trigger == "scheduled":
                update_scheduler_state(
                    last_finished_at=utc_now(),
                    last_status=final_status,
                    last_message=message,
                )
            log.info(
                "run finished run_id=%s status=%s coverage=%s jd=%s days=%s",
                run_id,
                final_status,
                asdict(result),
                asdict(jd_result) if jd_result is not None else None,
                days,
            )
            if baseline_stats is not None:
                log_run_summary(
                    log,
                    run_id=run_id,
                    status=final_status,
                    duration_seconds=time.monotonic() - run_started,
                    baseline=baseline_stats,
                    current=population_stats(codes),
                    partitions_processed=run_partitions_processed,
                    jd_totals=jd_totals,
                )
            return (
                0
                if final_status
                in {"COMPLETE", "PARTIAL_TIME_LIMIT", "STOPPED", "BLOCKED_HUMAN"}
                else 1
            )
    except CollectionAlreadyRunning as exc:
        log.error("collection already running: %s", exc)
        return 3
    except Exception as exc:  # noqa: BLE001 - CLI boundary persists unexpected run failures.
        try:
            if "run_id" in locals():
                finish_market_run(
                    run_id,
                    status="FAILED",
                    message="Collection failed.",
                    error=str(exc),
                )
            if args.trigger == "scheduled":
                update_scheduler_state(
                    last_finished_at=utc_now(),
                    last_status="FAILED",
                    last_message=f"Scheduled collection failed: {exc}",
                )
        finally:
            log.exception("collection failed")
            if "run_id" in locals() and baseline_stats is not None:
                log_run_summary(
                    log,
                    run_id=run_id,
                    status="FAILED",
                    duration_seconds=time.monotonic() - run_started,
                    baseline=baseline_stats,
                    current=population_stats(run_codes),
                    partitions_processed=run_partitions_processed,
                    jd_totals=jd_totals,
                )
        return 1
    finally:
        for page_id in (detail_page_id, list_page_id):
            if page_id is None:
                continue
            try:
                close_tab(page_id)
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask run result.
                log.warning(
                    "failed to close JMM-owned tab page_id=%s error=%s", page_id, exc
                )
        close_browser()


if __name__ == "__main__":
    raise SystemExit(main())
