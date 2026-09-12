from datetime import datetime
from zoneinfo import ZoneInfo


def test_scheduler_due_only_inside_window_and_once_per_local_day(monkeypatch):
    from collector import scheduler

    settings = {
        "scheduler.enabled": True,
        "scheduler.seek_enabled": True,
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

    settings["scheduler.seek_enabled"] = False
    assert service.due_now(datetime(2026, 9, 10, 3, 0, tzinfo=tz)) is False
    settings["scheduler.seek_enabled"] = True

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


def test_linkedin_uses_four_hour_slots_and_does_not_repeat_terminal_slot(monkeypatch):
    from collector import scheduler

    settings = {
        "scheduler.enabled": True,
        "scheduler.linkedin_enabled": True,
        "scheduler.linkedin_interval_hours": 4,
        "collection.linkedin_enabled": True,
    }
    monkeypatch.setattr(scheduler, "get_setting", lambda key: settings[key])
    monkeypatch.setattr(scheduler, "get_cycle", lambda _source: None)
    tz = ZoneInfo("Australia/Sydney")
    now = datetime(2026, 9, 11, 22, 15, tzinfo=tz)

    due, cycle_key, slot = scheduler.SchedulerService.linkedin_due_context(now)
    assert due is True
    assert slot == datetime(2026, 9, 11, 20, 0, tzinfo=tz)
    assert cycle_key == "linkedin:2026-09-11T20:00+10:00"

    monkeypatch.setattr(
        scheduler,
        "get_cycle",
        lambda _source: {"cycle_key": cycle_key, "status": "COMPLETE"},
    )
    due, _, _ = scheduler.SchedulerService.linkedin_due_context(now)
    assert due is False

    settings["scheduler.linkedin_enabled"] = False
    monkeypatch.setattr(scheduler, "get_cycle", lambda _source: None)
    due, _, _ = scheduler.SchedulerService.linkedin_due_context(now)
    assert due is False


def test_linkedin_partial_cycle_is_resumed_before_new_slot(monkeypatch):
    from collector import scheduler

    settings = {
        "scheduler.enabled": True,
        "scheduler.linkedin_enabled": True,
        "scheduler.linkedin_interval_hours": 4,
        "collection.linkedin_enabled": True,
    }
    monkeypatch.setattr(scheduler, "get_setting", lambda key: settings[key])
    monkeypatch.setattr(
        scheduler,
        "get_cycle",
        lambda _source: {"cycle_key": "linkedin:2026-09-11T16:00+10:00", "status": "PARTIAL"},
    )
    tz = ZoneInfo("Australia/Sydney")
    due, cycle_key, _ = scheduler.SchedulerService.linkedin_due_context(
        datetime(2026, 9, 11, 22, 15, tzinfo=tz)
    )
    assert due is True
    assert cycle_key == "linkedin:2026-09-11T16:00+10:00"


def test_scheduler_prioritises_due_linkedin_before_seek(monkeypatch):
    from collector import scheduler

    service = scheduler.SchedulerService()
    calls = []
    monkeypatch.setattr(scheduler, "update_scheduler_state", lambda **_kwargs: None)
    monkeypatch.setattr(
        service,
        "linkedin_due_context",
        lambda _now=None: (True, "linkedin:2026-09-13T00:00+10:00", _now),
    )
    monkeypatch.setattr(service, "due_now", lambda _now=None: True)
    monkeypatch.setattr(
        scheduler,
        "get_setting",
        lambda key: 5 if key == "collection.linkedin_window_hours" else 1,
    )

    def start_linkedin(**kwargs):
        calls.append(("linkedin", kwargs))
        service._stop.set()

    monkeypatch.setattr(scheduler.PROCESS_MANAGER, "start_linkedin", start_linkedin)
    monkeypatch.setattr(
        scheduler.PROCESS_MANAGER,
        "start",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("SEEK must not start in the same scheduler tick as due LinkedIn")
        ),
    )

    service._loop()

    assert calls == [
        (
            "linkedin",
            {
                "hours_old": 5,
                "cycle_key": "linkedin:2026-09-13T00:00+10:00",
            },
        )
    ]


def test_failed_seek_run_gets_one_same_day_retry_after_cooldown(monkeypatch):
    from collector import scheduler

    settings = {
        "scheduler.enabled": True,
        "scheduler.seek_enabled": True,
        "scheduler.daily_hour": 0,
        "scheduler.daily_minute": 0,
        "scheduler.run_window_minutes": 240,
        "scheduler.seek_failure_retry_minutes": 15,
    }
    monkeypatch.setattr(scheduler, "get_setting", lambda key: settings[key])
    monkeypatch.setattr(
        scheduler,
        "scheduler_state",
        lambda: {
            "last_attempt_local_date": "2026-09-13",
            "last_status": "FAILED",
            "last_finished_at": "2026-09-12T14:02:22+00:00",
        },
    )
    monkeypatch.setattr(
        scheduler,
        "latest_market_run",
        lambda: {
            "id": 26,
            "trigger": "scheduled",
            "source_scope": "seek_whole_state",
            "status": "FAILED",
            "finished_at": "2026-09-12T14:02:22+00:00",
        },
    )

    class _Conn:
        def execute(self, *_a, **_k):
            return self
        def fetchall(self):
            return [("2026-09-12T14:01:48+00:00",)]
        def __enter__(self):
            return self
        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(scheduler, "connect", lambda: _Conn())
    service = scheduler.SchedulerService()
    tz = ZoneInfo("Australia/Sydney")

    assert service.due_now(datetime(2026, 9, 13, 0, 10, tzinfo=tz)) is False
    assert service.due_now(datetime(2026, 9, 13, 0, 18, tzinfo=tz)) is True


def test_failed_seek_run_does_not_retry_more_than_once_same_day(monkeypatch):
    from collector import scheduler

    settings = {
        "scheduler.enabled": True,
        "scheduler.seek_enabled": True,
        "scheduler.daily_hour": 0,
        "scheduler.daily_minute": 0,
        "scheduler.run_window_minutes": 240,
        "scheduler.seek_failure_retry_minutes": 15,
    }
    monkeypatch.setattr(scheduler, "get_setting", lambda key: settings[key])
    monkeypatch.setattr(
        scheduler,
        "scheduler_state",
        lambda: {
            "last_attempt_local_date": "2026-09-13",
            "last_status": "FAILED",
            "last_finished_at": "2026-09-12T14:40:00+00:00",
        },
    )
    monkeypatch.setattr(
        scheduler,
        "latest_market_run",
        lambda: {
            "id": 27,
            "trigger": "scheduled",
            "source_scope": "seek_whole_state",
            "status": "FAILED",
            "finished_at": "2026-09-12T14:40:00+00:00",
        },
    )

    class _Conn:
        def execute(self, *_a, **_k):
            return self
        def fetchall(self):
            return [
                ("2026-09-12T14:30:00+00:00",),
                ("2026-09-12T14:01:48+00:00",),
            ]
        def __enter__(self):
            return self
        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(scheduler, "connect", lambda: _Conn())
    service = scheduler.SchedulerService()
    tz = ZoneInfo("Australia/Sydney")
    assert service.due_now(datetime(2026, 9, 13, 1, 0, tzinfo=tz)) is False


def test_scheduler_status_does_not_show_expired_same_day_retry(monkeypatch):
    from collector import scheduler

    settings = {
        "scheduler.enabled": True,
        "scheduler.seek_enabled": True,
        "scheduler.linkedin_enabled": False,
        "collection.linkedin_enabled": False,
        "scheduler.daily_hour": 0,
        "scheduler.daily_minute": 0,
        "scheduler.run_window_minutes": 240,
        "scheduler.seek_failure_retry_minutes": 15,
        "scheduler.linkedin_interval_hours": 4,
        "collection.linkedin_window_hours": 5,
    }
    monkeypatch.setattr(scheduler, "get_setting", lambda key: settings[key])
    monkeypatch.setattr(
        scheduler,
        "scheduler_state",
        lambda: {
            "last_attempt_local_date": "2026-09-13",
            "last_status": "FAILED",
            "last_finished_at": "2026-09-12T14:02:22+00:00",
        },
    )
    monkeypatch.setattr(
        scheduler,
        "latest_market_run",
        lambda: {
            "id": 26,
            "trigger": "scheduled",
            "source_scope": "seek_whole_state",
            "status": "FAILED",
            "finished_at": "2026-09-12T14:02:22+00:00",
        },
    )

    class _Conn:
        def execute(self, *_a, **_k):
            return self
        def fetchall(self):
            return [("2026-09-12T14:01:48+00:00",)]
        def __enter__(self):
            return self
        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(scheduler, "connect", lambda: _Conn())
    monkeypatch.setattr(scheduler, "get_cycle", lambda _source: None)
    service = scheduler.SchedulerService()
    tz = ZoneInfo("Australia/Sydney")
    now = datetime(2026, 9, 13, 9, 43, tzinfo=tz)
    monkeypatch.setattr(scheduler, "datetime", type("FixedDateTime", (datetime,), {"now": classmethod(lambda cls: now)}))

    status = service.status()
    assert status["next_run_at"] == "2026-09-14T00:00:00+10:00"
