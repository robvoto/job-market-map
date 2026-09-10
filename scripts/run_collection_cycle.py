from __future__ import annotations

import argparse
import signal
import time
from dataclasses import asdict
from threading import Event

from collector.backup import create_backup
from collector.browser_broker import close_browser, open_tab
from collector.run_lock import CollectionAlreadyRunning, collection_run_lock
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
    if args.days is not None and args.days < 1:
        parser.error("--days must be >= 1")
    if args.max_runtime_minutes is not None and args.max_runtime_minutes < 0:
        parser.error("--max-runtime-minutes must be >= 0")

    stop_event = Event()

    def request_stop(*_args) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    try:
        with collection_run_lock(args.trigger):
            codes = enabled_state_codes()
            if not codes:
                print("No enabled SEEK geographies.")
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

            if bool(get_setting("backup.before_collection_enabled")):
                backup = create_backup()
                set_market_run_backup(run_id, backup.path)
                print(f"Backup verified: {backup.path}", flush=True)

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
            started = time.monotonic()

            def deadline_reached() -> bool:
                return (
                    bool(max_minutes)
                    and (time.monotonic() - started) >= max_minutes * 60
                )

            list_page_id = int(open_tab("about:blank", active=False).result["pageId"])
            result = run_seek_cycle(
                page_id=list_page_id,
                codes=codes,
                days=days,
                should_stop=stop_event.is_set,
                deadline_reached=deadline_reached,
            )

            jd_result = None
            final_status = result.status
            if (
                result.status == "COMPLETE"
                and not stop_event.is_set()
                and not deadline_reached()
            ):
                detail_page_id = int(
                    open_tab("about:blank", active=False).result["pageId"]
                )
                jd_result = enrich_seek_coverage_jds(
                    page_id=detail_page_id,
                    codes=codes,
                    days=days,
                    should_stop=stop_event.is_set,
                    deadline_reached=deadline_reached,
                    include_existing_unfetched=args.backfill_existing_jds,
                )
                if stop_event.is_set():
                    final_status = "STOPPED"
                elif deadline_reached() and jd_result.remaining:
                    final_status = "PARTIAL_TIME_LIMIT"
                elif jd_result.remaining:
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
                    f"remaining={jd_result.remaining}."
                )
            finish_market_run(run_id, status=final_status, message=message)
            if args.trigger == "scheduled":
                update_scheduler_state(
                    last_finished_at=utc_now(),
                    last_status=final_status,
                    last_message=message,
                )
            print(
                {
                    "coverage": asdict(result),
                    "jd": asdict(jd_result) if jd_result is not None else None,
                    "status": final_status,
                    "days": days,
                },
                flush=True,
            )
            return (
                0
                if final_status in {"COMPLETE", "PARTIAL_TIME_LIMIT", "STOPPED"}
                else 1
            )
    except CollectionAlreadyRunning as exc:
        print(str(exc), flush=True)
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
            print(f"Collection failed: {exc}", flush=True)
        return 1
    finally:
        close_browser()


if __name__ == "__main__":
    raise SystemExit(main())
