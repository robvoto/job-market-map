from collector import db
from collector.cursors import save_cursor
from sources.linkedin_collector import LinkedInChunkResult


def _isolate(tmp_path, monkeypatch):
    from collector import campaign, query_admin, query_registry

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    for module in (query_admin, query_registry):
        monkeypatch.setattr(module, "connect", db.connect)
        monkeypatch.setattr(module, "init_db", db.init_db)
    db.init_db()
    return campaign, query_admin


def _result(status="COMPLETE", observed=10, new=10, duplicates=0):
    return LinkedInChunkResult(
        status=status,
        cycle_key="2026-09-11",
        start_offset=0,
        next_offset=10,
        cards_observed=observed,
        unique_new_jobs=new,
        duplicate_observations=duplicates,
        detail_attempted=0,
        detail_stored=0,
        detail_failed=0,
        elapsed_seconds=0.1,
    )


def test_production_linkedin_campaign_does_not_use_keyword_registry(tmp_path, monkeypatch):
    campaign, query_admin = _isolate(tmp_path, monkeypatch)
    row = query_admin.add_query(
        source="linkedin",
        query_text="unique admin query",
        location="New South Wales, Australia",
        geography_code="NSW",
        active=True,
    )
    assert any(
        run["query_id"] == row["id"]
        for run in campaign.registry_runs(sources={"linkedin"})
    )
    monkeypatch.setattr(
        campaign,
        "linkedin_geography_runs",
        lambda: [{"geography_code": "NSW", "location": "New South Wales, Australia"}],
    )
    monkeypatch.setattr(campaign, "get_setting", lambda key: True)
    calls = []

    def fake_collect(location, **kwargs):
        calls.append((location, kwargs))
        save_cursor(
            "linkedin", "", location, 10, status="COMPLETE", cycle_key=kwargs["cycle_key"]
        )
        return _result()

    monkeypatch.setattr(campaign, "collect_linkedin_geography_page", fake_collect)
    result = campaign.run_linkedin_campaign(
        days=1, should_stop=lambda: False, deadline_reached=lambda: False
    )
    assert result.status == "COMPLETE"
    assert len(calls) == 1
    assert calls[0][0] == "New South Wales, Australia"
    assert calls[0][1]["hours_old"] == 24
    assert calls[0][1]["max_results"] == 1000


def test_linkedin_geography_campaign_resumes_same_cycle_after_runtime_limit(
    tmp_path, monkeypatch
):
    campaign, _ = _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(campaign, "get_setting", lambda key: True)
    monkeypatch.setattr(
        campaign,
        "linkedin_geography_runs",
        lambda: [
            {"geography_code": "NSW", "location": "New South Wales, Australia"},
            {"geography_code": "QLD", "location": "Queensland, Australia"},
        ],
    )
    processed = []

    def fake_collect(location, **kwargs):
        processed.append(location)
        save_cursor(
            "linkedin", "", location, 10, status="COMPLETE", cycle_key=kwargs["cycle_key"]
        )
        return _result()

    monkeypatch.setattr(campaign, "collect_linkedin_geography_page", fake_collect)
    first = campaign.run_linkedin_campaign(
        days=1,
        should_stop=lambda: False,
        deadline_reached=lambda: len(processed) >= 1,
    )
    assert first.status == "PARTIAL_TIME_LIMIT"
    assert first.geographies_complete == 1
    first_cycle = first.cycle_key

    second = campaign.run_linkedin_campaign(
        days=1, should_stop=lambda: False, deadline_reached=lambda: False
    )
    assert second.status == "COMPLETE"
    assert second.geographies_complete == 2
    assert second.cycle_key == first_cycle
    assert sorted(processed) == ["New South Wales, Australia", "Queensland, Australia"]
    progress = campaign.linkedin_campaign_progress()
    assert progress["status"] == "COMPLETE"
    assert progress["geographies_remaining"] == 0


def test_linkedin_geography_cap_is_reported_not_hidden(tmp_path, monkeypatch):
    campaign, _ = _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(campaign, "get_setting", lambda key: True)
    monkeypatch.setattr(
        campaign,
        "linkedin_geography_runs",
        lambda: [{"geography_code": "NSW", "location": "New South Wales, Australia"}],
    )

    def fake_collect(location, **kwargs):
        save_cursor(
            "linkedin",
            "",
            location,
            1000,
            status="INCOMPLETE_CAP",
            cycle_key=kwargs["cycle_key"],
        )
        return _result(status="INCOMPLETE_CAP")

    monkeypatch.setattr(campaign, "collect_linkedin_geography_page", fake_collect)
    result = campaign.run_linkedin_campaign(
        days=1, should_stop=lambda: False, deadline_reached=lambda: False
    )
    assert result.status == "INCOMPLETE_CAP"
    assert result.capped_geographies == 1
    progress = campaign.linkedin_campaign_progress()
    assert progress["status"] == "INCOMPLETE_CAP"
    assert progress["geographies_capped"] == 1
    assert progress["geographies_remaining"] == 0


def test_linkedin_campaign_has_no_browser_dependency():
    import inspect

    from collector import campaign
    from sources import linkedin_collector

    source = inspect.getsource(campaign) + inspect.getsource(linkedin_collector)
    assert "browser_broker" not in source
    assert "playwright" not in source.casefold()
    assert "chromium" not in source.casefold()


def test_linkedin_geography_failures_remain_retryable(tmp_path, monkeypatch):
    campaign, _ = _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(campaign, "get_setting", lambda key: True)
    monkeypatch.setattr(
        campaign,
        "linkedin_geography_runs",
        lambda: [
            {"geography_code": "ACT", "location": "Australian Capital Territory, Australia"},
            {"geography_code": "NSW", "location": "New South Wales, Australia"},
            {"geography_code": "QLD", "location": "Queensland, Australia"},
        ],
    )
    calls = []

    def fail(location, **_kwargs):
        calls.append(location)
        raise RuntimeError("LinkedIn unavailable")

    monkeypatch.setattr(campaign, "collect_linkedin_geography_page", fail)
    result = campaign.run_linkedin_campaign(
        days=1, should_stop=lambda: False, deadline_reached=lambda: False
    )
    assert result.status == "PARTIAL_FAILURE"
    assert result.failed_geographies == 3
    assert len(calls) == 3
