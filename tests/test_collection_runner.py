from contextlib import contextmanager


def test_full_evidence_pass_sweeps_jds_before_during_and_after_coverage(monkeypatch):
    from collector.seek_cycle import SeekCycleResult
    from collector.seek_jd import SeekJDEnrichmentResult
    from scripts import run_collection_cycle as runner

    events = []

    @contextmanager
    def fake_lock(_trigger):
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
    monkeypatch.setattr(runner, "all_states_complete", lambda _codes: False)
    monkeypatch.setattr(runner, "start_market_run", lambda **_kwargs: 1)
    monkeypatch.setattr(runner, "finish_market_run", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "set_market_run_backup", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "snapshot_and_reset_coverage", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "update_scheduler_state", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "close_browser", lambda: None)
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
        ("jd", False),
        ("coverage", False),
        ("jd", False),
        ("jd", True),
    ]
