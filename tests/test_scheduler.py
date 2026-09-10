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
