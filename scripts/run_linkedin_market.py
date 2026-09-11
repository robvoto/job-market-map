from __future__ import annotations

import argparse
import json
import signal
import time
from dataclasses import asdict
from threading import Event

from collector.backup import create_backup
from collector.campaign import run_linkedin_campaign
from collector.run_lock import CollectionAlreadyRunning, collection_run_lock
from collector.run_logging import configure_collection_logging
from collector.settings import get_setting


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run LinkedIn geography-first cards-only discovery without SEEK."
    )
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--hours-old", type=int, default=None)
    parser.add_argument("--cycle-key", default=None)
    parser.add_argument("--max-runtime-minutes", type=int, default=0)
    parser.add_argument(
        "--trigger",
        choices=("linkedin-manual", "linkedin-scheduled"),
        default="linkedin-manual",
    )
    args = parser.parse_args(argv)
    if args.days is not None and args.days < 1:
        parser.error("--days must be >= 1")
    if args.hours_old is not None and args.hours_old < 1:
        parser.error("--hours-old must be >= 1")
    if args.days is not None and args.hours_old is not None:
        parser.error("use either --days or --hours-old, not both")
    if args.max_runtime_minutes < 0:
        parser.error("--max-runtime-minutes must be >= 0")

    days = (
        int(args.days)
        if args.days is not None
        else None
        if args.hours_old is not None
        else int(get_setting("collection.default_freshness_days"))
    )
    hours_old = int(args.hours_old) if args.hours_old is not None else None
    stop = Event()
    started = time.monotonic()
    log = configure_collection_logging()

    def request_stop(*_args) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    try:
        with collection_run_lock(args.trigger):
            log.info(
                "LinkedIn standalone run started days=%s hours_old=%s cycle_key=%s",
                days,
                hours_old,
                args.cycle_key,
            )
            backup = create_backup()
            log.info("backup verified path=%s", backup.path)
            print(f"backup={backup.path} integrity={backup.integrity}", flush=True)

            def deadline_reached() -> bool:
                return bool(args.max_runtime_minutes) and (
                    time.monotonic() - started
                ) >= args.max_runtime_minutes * 60

            result = run_linkedin_campaign(
                days=days,
                hours_old=hours_old,
                desired_cycle_key=args.cycle_key,
                should_stop=stop.is_set,
                deadline_reached=deadline_reached,
            )
            log.info("LinkedIn standalone run finished result=%s", asdict(result))
            print(json.dumps(asdict(result), indent=2, sort_keys=True), flush=True)
    except CollectionAlreadyRunning as exc:
        print(str(exc), flush=True)
        return 2

    return 0 if result.status in {"COMPLETE", "INCOMPLETE_CAP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
