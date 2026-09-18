from __future__ import annotations

import logging
import math
import os
import threading
from datetime import datetime, timedelta

from collector.db import connect
from collector.service_manager import PROCESS_MANAGER, CollectionProcessError
from collector.service_state import (
    latest_market_run,
    scheduler_state,
    update_scheduler_state,
)
from collector.settings import get_setting
from collector.source_campaign import get_cycle

LINKEDIN_TERMINAL_STATUSES = {"COMPLETE", "INCOMPLETE_CAP", "PARTIAL_FAILURE"}
log = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        if self.active:
            return True
        if os.environ.get("JOB_MARKET_MAP_SCHEDULER_SERVICE") != "1":
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="job-market-map-scheduler", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    @staticmethod
    def seek_slot(now: datetime | None = None) -> datetime:
        current = now or datetime.now().astimezone()
        hour = int(get_setting("scheduler.daily_hour"))
        minute = int(get_setting("scheduler.daily_minute"))
        interval = int(get_setting("scheduler.seek_interval_hours"))
        start = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        while start > current:
            start -= timedelta(hours=interval)
        while start + timedelta(hours=interval) <= current:
            start += timedelta(hours=interval)
        return start

    @classmethod
    def seek_cycle_key(cls, now: datetime | None = None) -> str:
        return f"seek:{cls.seek_slot(now).isoformat(timespec='minutes')}"

    @classmethod
    def schedule_window(cls, now: datetime | None = None) -> tuple[datetime, datetime]:
        start = cls.seek_slot(now)
        end = start + timedelta(minutes=int(get_setting("scheduler.run_window_minutes")))
        return start, end

    @classmethod
    def manual_run_schedule_slot(
        cls,
        started_at: datetime, finished_at: datetime
    ) -> str | None:
        """Return the SEEK slot overlapped by a successful manual run."""
        for current in (started_at, finished_at):
            start, end = cls.schedule_window(current)
            if started_at <= end and finished_at >= start:
                return f"seek:{start.isoformat(timespec='minutes')}"
        return None

    @classmethod
    def manual_run_schedule_date(
        cls,
        started_at: datetime,
        finished_at: datetime,
    ) -> str | None:
        slot = cls.manual_run_schedule_slot(started_at, finished_at)
        return slot.removeprefix("seek:")[:10] if slot else None

    @staticmethod
    def _seek_retry_at(
        current: datetime, state: dict, slot_start: datetime
    ) -> datetime | None:
        if str(state.get("last_status") or "") != "FAILED":
            return None
        slot_key = f"seek:{slot_start.isoformat(timespec='minutes')}"
        if str(state.get("last_attempt_seek_slot") or "") != slot_key:
            return None
        latest = latest_market_run() or {}
        if (
            str(latest.get("trigger") or "") != "scheduled"
            or not str(latest.get("source_scope") or "").startswith("seek")
            or str(latest.get("status") or "") != "FAILED"
        ):
            return None
        latest_id = int(latest.get("id") or 0)
        if latest_id <= 0:
            return None
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT started_at FROM market_collection_runs
                 WHERE trigger='scheduled'
                   AND source_scope LIKE 'seek%'
                   AND id <= ?
                 ORDER BY id DESC LIMIT 2
                """,
                (latest_id,),
            ).fetchall()
        interval_end = slot_start + timedelta(
            hours=int(get_setting("scheduler.seek_interval_hours"))
        )
        attempts_in_slot = 0
        for row in rows:
            try:
                started = datetime.fromisoformat(str(row[0])).astimezone(current.tzinfo)
            except (TypeError, ValueError):
                continue
            if slot_start <= started < interval_end:
                attempts_in_slot += 1
        if attempts_in_slot >= 2:
            return None
        finished_raw = str(
            state.get("last_finished_at") or latest.get("finished_at") or ""
        )
        try:
            finished = datetime.fromisoformat(finished_raw).astimezone(current.tzinfo)
        except ValueError:
            return None
        return finished + timedelta(
            minutes=int(get_setting("scheduler.seek_failure_retry_minutes"))
        )

    def due_now(self, now: datetime | None = None) -> bool:
        current = now or datetime.now().astimezone()
        if not bool(get_setting("scheduler.enabled")) or not bool(
            get_setting("scheduler.seek_enabled")
        ):
            return False
        start, end = self.schedule_window(current)
        if not (start <= current <= end):
            return False
        state = scheduler_state()
        slot_key = f"seek:{start.isoformat(timespec='minutes')}"
        if str(state.get("last_attempt_seek_slot") or "") != slot_key:
            return True
        retry_at = self._seek_retry_at(current, state, start)
        return retry_at is not None and current >= retry_at

    @staticmethod
    def linkedin_slot(now: datetime | None = None) -> datetime:
        current = now or datetime.now().astimezone()
        interval = int(get_setting("scheduler.linkedin_interval_hours"))
        slot_hour = (current.hour // interval) * interval
        return current.replace(hour=slot_hour, minute=0, second=0, microsecond=0)

    @classmethod
    def linkedin_cycle_key(cls, now: datetime | None = None) -> str:
        return f"linkedin:{cls.linkedin_slot(now).isoformat(timespec='minutes')}"

    @classmethod
    def linkedin_due_context(
        cls, now: datetime | None = None
    ) -> tuple[bool, str, datetime]:
        current = now or datetime.now().astimezone()
        slot = cls.linkedin_slot(current)
        target_cycle = cls.linkedin_cycle_key(current)
        if (
            not bool(get_setting("scheduler.enabled"))
            or not bool(get_setting("scheduler.linkedin_enabled"))
            or not bool(get_setting("collection.linkedin_enabled"))
        ):
            return False, target_cycle, slot

        state = get_cycle("linkedin")
        if state and str(state.get("status") or "") not in LINKEDIN_TERMINAL_STATUSES:
            # Finish a partial slot before starting a newer rolling window.
            return True, str(state["cycle_key"]), slot
        if (
            state
            and str(state.get("cycle_key") or "") == target_cycle
            and str(state.get("status") or "") in LINKEDIN_TERMINAL_STATUSES
        ):
            return False, target_cycle, slot
        return True, target_cycle, slot

    def status(self) -> dict:
        now = datetime.now().astimezone()
        start, window_end = self.schedule_window(now)
        state = scheduler_state()
        slot_key = f"seek:{start.isoformat(timespec='minutes')}"
        next_slot = start + timedelta(hours=int(get_setting("scheduler.seek_interval_hours")))
        if str(state.get("last_attempt_seek_slot") or "") != slot_key and now <= window_end:
            next_run = max(start, now)
        else:
            retry_at = self._seek_retry_at(now, state, start)
            if retry_at is not None and now <= window_end and retry_at <= window_end:
                next_run = max(retry_at, now)
            else:
                next_run = next_slot
        linkedin_due, linkedin_cycle, linkedin_slot = self.linkedin_due_context(now)
        linkedin_next = (
            linkedin_slot
            if linkedin_due
            else linkedin_slot
            + timedelta(hours=int(get_setting("scheduler.linkedin_interval_hours")))
        )
        return {
            "service_active": self.active,
            "enabled": bool(get_setting("scheduler.enabled")),
            "seek_enabled": bool(get_setting("scheduler.seek_enabled")),
            "linkedin_enabled": bool(get_setting("scheduler.linkedin_enabled")),
            "daily_time_local": f"{int(get_setting('scheduler.daily_hour')):02d}:{int(get_setting('scheduler.daily_minute')):02d}",
            "next_run_at": next_run.isoformat(timespec="seconds"),
            "seek_interval_hours": int(get_setting("scheduler.seek_interval_hours")),
            "seek_cycle_key": slot_key,
            "linkedin_window_hours": int(get_setting("collection.linkedin_window_hours")),
            "linkedin_interval_hours": int(get_setting("scheduler.linkedin_interval_hours")),
            "poll_seconds": int(get_setting("scheduler.poll_seconds")),
            "linkedin_cycle_key": linkedin_cycle,
            "linkedin_due": linkedin_due,
            "linkedin_next_run_at": linkedin_next.isoformat(timespec="seconds"),
            **state,
        }

    def _tick(self) -> None:
        now = datetime.now().astimezone()
        update_scheduler_state(heartbeat_at=now.isoformat(timespec="seconds"))
        linkedin_due, linkedin_cycle, _ = self.linkedin_due_context(now)
        if linkedin_due:
            try:
                PROCESS_MANAGER.start_linkedin(
                    hours_old=int(get_setting("collection.linkedin_window_hours")),
                    cycle_key=linkedin_cycle,
                )
            except CollectionProcessError:
                pass
        elif self.due_now(now):
            seek_slot = self.seek_slot(now)
            seek_cycle = f"seek:{seek_slot.isoformat(timespec='minutes')}"
            _, window_end = self.schedule_window(now)
            remaining_seconds = max(0.0, (window_end - now).total_seconds())
            remaining_minutes = max(1, math.ceil(remaining_seconds / 60.0))
            update_scheduler_state(
                last_attempt_local_date=now.date().isoformat(),
                last_attempt_seek_slot=seek_cycle,
                last_status="STARTING",
                last_message=f"SEEK collection slot {seek_cycle} is due.",
            )
            try:
                PROCESS_MANAGER.start(
                    trigger="scheduled",
                    max_runtime_minutes=remaining_minutes,
                )
            except CollectionProcessError as exc:
                update_scheduler_state(last_status="SKIPPED_ACTIVE", last_message=str(exc))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                # A transient DB/source/runtime problem must not silently kill the
                # scheduler while the API process remains healthy.
                log.exception("Scheduler tick failed; scheduling will retry")
            try:
                poll_seconds = max(1, int(get_setting("scheduler.poll_seconds")))
            except Exception:
                log.exception("Scheduler poll setting failed; retrying in 30 seconds")
                poll_seconds = 30
            self._stop.wait(poll_seconds)


SCHEDULER = SchedulerService()
