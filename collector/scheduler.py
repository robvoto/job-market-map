from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta

from collector.service_manager import PROCESS_MANAGER, CollectionProcessError
from collector.service_state import scheduler_state, update_scheduler_state
from collector.settings import get_setting
from collector.source_campaign import get_cycle

LINKEDIN_TERMINAL_STATUSES = {"COMPLETE", "INCOMPLETE_CAP"}


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
    def schedule_window(now: datetime | None = None) -> tuple[datetime, datetime]:
        current = now or datetime.now().astimezone()
        hour = int(get_setting("scheduler.daily_hour"))
        minute = int(get_setting("scheduler.daily_minute"))
        start = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        end = start + timedelta(minutes=int(get_setting("scheduler.run_window_minutes")))
        return start, end

    @staticmethod
    def manual_run_schedule_date(
        started_at: datetime, finished_at: datetime
    ) -> str | None:
        """Return the scheduled local date a successful manual run actually covers."""
        for current in (started_at, finished_at):
            start, end = SchedulerService.schedule_window(current)
            if started_at <= end and finished_at >= start:
                return start.date().isoformat()
        return None

    def due_now(self, now: datetime | None = None) -> bool:
        current = now or datetime.now().astimezone()
        if not bool(get_setting("scheduler.enabled")):
            return False
        start, end = self.schedule_window(current)
        if not (start <= current <= end):
            return False
        state = scheduler_state()
        return str(state.get("last_attempt_local_date") or "") != current.date().isoformat()

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
        if not bool(get_setting("scheduler.enabled")) or not bool(
            get_setting("collection.linkedin_enabled")
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
        start, _ = self.schedule_window(now)
        state = scheduler_state()
        last_attempt = str(state.get("last_attempt_local_date") or "")
        next_run = start
        if now > start or last_attempt == now.date().isoformat():
            next_run = start + timedelta(days=1)
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
            "daily_time_local": f"{int(get_setting('scheduler.daily_hour')):02d}:{int(get_setting('scheduler.daily_minute')):02d}",
            "next_run_at": next_run.isoformat(timespec="seconds"),
            "linkedin_window_hours": int(get_setting("collection.linkedin_window_hours")),
            "linkedin_interval_hours": int(get_setting("scheduler.linkedin_interval_hours")),
            "linkedin_cycle_key": linkedin_cycle,
            "linkedin_due": linkedin_due,
            "linkedin_next_run_at": linkedin_next.isoformat(timespec="seconds"),
            **state,
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
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
                update_scheduler_state(
                    last_attempt_local_date=now.date().isoformat(),
                    last_status="STARTING",
                    last_message="Overnight SEEK collection is due.",
                )
                try:
                    PROCESS_MANAGER.start(trigger="scheduled")
                except CollectionProcessError as exc:
                    update_scheduler_state(last_status="SKIPPED_ACTIVE", last_message=str(exc))
            self._stop.wait(int(get_setting("scheduler.poll_seconds")))


SCHEDULER = SchedulerService()
