from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo


def test_extra_fresh_run_gets_incremental_cutoff_but_daily_run_does_not(monkeypatch):
    from scripts import run_collection_cycle as runner

    monkeypatch.setattr(
        runner,
        "latest_complete_fresh_seek_started_at",
        lambda _codes: "2026-09-11T00:00:00+00:00",
    )
    monkeypatch.setattr(
        runner,
        "get_setting",
        lambda key: 120 if key == "collection.seek_incremental_overlap_minutes" else 1,
    )
    tz = ZoneInfo("UTC")

    extra = runner._seek_incremental_cutoff(
        mode="fresh",
        days=1,
        default_days=1,
        codes=["NSW"],
        started_at=datetime(2026, 9, 11, 10, 0, tzinfo=tz),
    )
    assert extra == datetime(2026, 9, 10, 22, 0, tzinfo=tz)

    daily = runner._seek_incremental_cutoff(
        mode="fresh",
        days=1,
        default_days=1,
        codes=["NSW"],
        started_at=datetime(2026, 9, 12, 0, 0, tzinfo=tz),
    )
    assert daily is None
    assert (
        runner._seek_incremental_cutoff(
            mode="resume",
            days=1,
            default_days=1,
            codes=["NSW"],
            started_at=datetime(2026, 9, 11, 10, 0, tzinfo=tz),
        )
        is None
    )


def test_successful_manual_default_run_marks_only_overlapping_schedule_slot(monkeypatch):
    from scripts import run_collection_cycle as runner

    updates = []
    monkeypatch.setattr(
        runner.SchedulerService,
        "manual_run_schedule_date",
        lambda _start, _finish: "2026-09-12",
    )
    monkeypatch.setattr(runner, "update_scheduler_state", lambda **kwargs: updates.append(kwargs))
    tz = ZoneInfo("Australia/Sydney")

    result = runner._satisfy_manual_schedule_slot(
        trigger="manual",
        final_status="COMPLETE",
        days=1,
        default_days=1,
        started_at=datetime(2026, 9, 12, 0, 30, tzinfo=tz),
        finished_at=datetime(2026, 9, 12, 1, 0, tzinfo=tz),
    )
    assert result == "2026-09-12"
    assert updates == [
        {
            "last_attempt_local_date": "2026-09-12",
            "last_status": "SATISFIED_MANUAL",
            "last_message": (
                "Successful manual collection satisfied the configured overnight slot "
                "for 2026-09-12."
            ),
        }
    ]

    updates.clear()
    assert (
        runner._satisfy_manual_schedule_slot(
            trigger="manual",
            final_status="PARTIAL_JD",
            days=1,
            default_days=1,
            started_at=datetime(2026, 9, 12, 0, 30, tzinfo=tz),
            finished_at=datetime(2026, 9, 12, 1, 0, tzinfo=tz),
        )
        is None
    )
    assert updates == []


def test_explicit_legacy_jd_backfill_runs_once_after_card_coverage(monkeypatch):
    from collector.seek_cycle import SeekCycleResult
    from collector.seek_jd import SeekJDEnrichmentResult
    from scripts import run_collection_cycle as runner

    events = []

    @contextmanager
    def fake_lock(_trigger, *, source=None):
        assert source == "seek"
        yield

    monkeypatch.setattr(runner, "collection_run_lock", fake_lock)

    class _TestLog:
        def info(self, *_args, **_kwargs):
            pass

        def error(self, *_args, **_kwargs):
            pass

        def exception(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(runner, "configure_collection_logging", lambda: _TestLog())
    monkeypatch.setattr(runner, "enabled_state_codes", lambda: ["ACT"])
    monkeypatch.setattr(runner, "state_root", lambda _code: {"id": 1})
    monkeypatch.setattr(runner, "all_states_complete", lambda _codes: False)
    monkeypatch.setattr(runner, "start_market_run", lambda **_kwargs: 1)
    monkeypatch.setattr(runner, "finish_market_run", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "set_market_run_backup", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "snapshot_and_reset_coverage", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "update_scheduler_state", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "close_browser", lambda: None)
    monkeypatch.setattr(runner, "close_tab", lambda *_a, **_k: None)
    monkeypatch.setattr(
        runner,
        "get_setting",
        lambda key: False if key == "backup.before_collection_enabled" else 0,
    )

    page_ids = iter([11, 12])
    monkeypatch.setattr(
        runner,
        "open_tab",
        lambda *_a, **_k: type(
            "Response", (), {"result": {"pageId": next(page_ids)}}
        )(),
    )

    def fake_enrich(**kwargs):
        events.append(("jd", bool(kwargs["include_existing_unfetched"])))
        return SeekJDEnrichmentResult(1, 0, 1, 1, 0, 0)

    monkeypatch.setattr(runner, "enrich_seek_coverage_jds", fake_enrich)

    def fake_cycle(**kwargs):
        from sources.seek_market_map import MarketMapResult

        events.append(("coverage", False))
        kwargs["after_progress"](
            MarketMapResult(
                geography_code="ACT",
                status="COMPLETE",
                root_partition_id=1,
                reported_results=1,
                covered_unique_jobs=1,
                incomplete_partitions=0,
                partitions_processed=1,
            )
        )
        return SeekCycleResult("COMPLETE", ["ACT"], 1, [])

    monkeypatch.setattr(runner, "run_seek_cycle", fake_cycle)
    assert (
        runner.main(
            [
                "--trigger",
                "manual",
                "--days",
                "3",
                "--backfill-existing-jds",
                "--max-runtime-minutes",
                "0",
            ]
        )
        == 0
    )
    assert events == [
        ("coverage", False),
        ("jd", True),
    ]


def test_empty_coverage_workspace_starts_fresh(monkeypatch):
    from scripts import run_collection_cycle as runner

    monkeypatch.setattr(runner, "enabled_state_codes", lambda: ["ACT", "NSW"])
    monkeypatch.setattr(runner, "state_root", lambda _code: None)
    monkeypatch.setattr(runner, "all_states_complete", lambda _codes: False)

    codes = runner.enabled_state_codes()
    has_coverage_workspace = any(runner.state_root(code) is not None for code in codes)
    mode = (
        "fresh"
        if not has_coverage_workspace or runner.all_states_complete(codes)
        else "resume"
    )

    assert mode == "fresh"
