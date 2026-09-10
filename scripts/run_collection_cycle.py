from __future__ import annotations

import argparse
import signal
import time
from dataclasses import asdict
from threading import Event

from collector.backup import create_backup
from collector.browser_broker import open_tab
from collector.run_lock import CollectionAlreadyRunning, collection_run_lock
from collector.seek_cycle import (
    all_states_complete,
    enabled_state_codes,
    run_seek_cycle,
    snapshot_and_reset_coverage,
)
from collector.service_state import (
    finish_market_run,
    set_market_run_backup,
    start_market_run,
    update_scheduler_state,
    utc_now,
)
from collector.settings import get_setting


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one safe neutral Job Market Map collection cycle.")
    parser.add_argument("--trigger", choices=["manual", "scheduled"], default="manual")
    args = parser.parse_args()

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
                print(f"Backup verified: {backup.path}")

            if mode == "fresh":
                snapshot_and_reset_coverage(codes)
            if args.trigger == "scheduled":
                update_scheduler_state(
                    last_started_at=utc_now(),
                    last_status="RUNNING",
                    last_message=f"Scheduled SEEK collection started ({mode}).",
                )

            max_minutes = int(get_setting("scheduler.max_runtime_minutes"))
            started = time.monotonic()
            page_id = int(open_tab("about:blank", active=False).result["pageId"])
            result = run_seek_cycle(
                page_id=page_id,
                codes=codes,
                should_stop=stop_event.is_set,
                deadline_reached=lambda: (time.monotonic() - started) >= max_minutes * 60,
            )
            message = (
                f"SEEK {mode} cycle {result.status.lower()}; "
                f"processed {result.partitions_processed} partition(s)."
            )
            finish_market_run(run_id, status=result.status, message=message)
            if args.trigger == "scheduled":
                update_scheduler_state(
                    last_finished_at=utc_now(),
                    last_status=result.status,
                    last_message=message,
                )
            print(asdict(result))
            return 0 if result.status in {"COMPLETE", "PARTIAL_TIME_LIMIT", "STOPPED"} else 1
    except CollectionAlreadyRunning as exc:
        print(str(exc))
        return 3
    except Exception as exc:  # noqa: BLE001 - CLI boundary persists unexpected run failures.
        try:
            if "run_id" in locals():
                finish_market_run(run_id, status="FAILED", message="Collection failed.", error=str(exc))
            if args.trigger == "scheduled":
                update_scheduler_state(
                    last_finished_at=utc_now(),
                    last_status="FAILED",
                    last_message=f"Scheduled collection failed: {exc}",
                )
        finally:
            print(f"Collection failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
