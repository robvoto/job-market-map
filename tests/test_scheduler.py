from datetime import datetime
from zoneinfo import ZoneInfo


def test_scheduler_due_only_inside_window_and_once_per_local_day(monkeypatch):
    from collector import scheduler

    settings = {
        "scheduler.enabled": True,
        "scheduler.daily_hour": 2,
        "scheduler.daily_minute": 0,
        "scheduler.run_window_minutes": 240,
    }
    monkeypatch.setattr(scheduler, "get_setting", lambda key: settings[key])
    monkeypatch.setattr(
        scheduler,
        "scheduler_state",
        lambda: {"last_attempt_local_date": None},
    )
    service = scheduler.SchedulerService()
    tz = ZoneInfo("Australia/Sydney")
    assert service.due_now(datetime(2026, 9, 10, 3, 0, tzinfo=tz)) is True
    assert service.due_now(datetime(2026, 9, 10, 12, 0, tzinfo=tz)) is False

    monkeypatch.setattr(
        scheduler,
        "scheduler_state",
        lambda: {"last_attempt_local_date": "2026-09-10"},
    )
    assert service.due_now(datetime(2026, 9, 10, 3, 0, tzinfo=tz)) is False


def test_manual_run_only_satisfies_schedule_when_it_overlaps_schedule_window(monkeypatch):
    from collector import scheduler

    settings = {
        "scheduler.daily_hour": 0,
        "scheduler.daily_minute": 0,
        "scheduler.run_window_minutes": 240,
    }
    monkeypatch.setattr(scheduler, "get_setting", lambda key: settings[key])
    tz = ZoneInfo("Australia/Sydney")

    assert (
        scheduler.SchedulerService.manual_run_schedule_date(
            datetime(2026, 9, 11, 19, 13, tzinfo=tz),
            datetime(2026, 9, 11, 20, 0, tzinfo=tz),
        )
        is None
    )
    assert scheduler.SchedulerService.manual_run_schedule_date(
        datetime(2026, 9, 12, 0, 30, tzinfo=tz),
        datetime(2026, 9, 12, 1, 15, tzinfo=tz),
    ) == "2026-09-12"
    assert scheduler.SchedulerService.manual_run_schedule_date(
        datetime(2026, 9, 11, 23, 55, tzinfo=tz),
        datetime(2026, 9, 12, 0, 20, tzinfo=tz),
    ) == "2026-09-12"
    assert scheduler.SchedulerService.manual_run_schedule_date(
        datetime(2026, 9, 12, 23, 30, tzinfo=tz),
        datetime(2026, 9, 13, 4, 30, tzinfo=tz),
    ) == "2026-09-13"
