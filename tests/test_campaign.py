import threading
import time

from collector import db
from collector.cursors import save_cursor
from sources.linkedin_collector import LinkedInChunkResult, LinkedInFetchedPage


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


def _fetched(status="COMPLETE", start=0, next_offset=10):
    return LinkedInFetchedPage(
        start_offset=start,
        next_offset=next_offset,
        status=status,
        rows=[],
        full_page_seen=status != "COMPLETE",
        later_rows_seen=False,
        http_attempts=1,
        elapsed_seconds=0.01,
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
    fetch_calls = []
    ingest_calls = []

    def fake_fetch(location, **kwargs):
        fetch_calls.append((location, kwargs))
        return _fetched()

    def fake_ingest(fetched, location, **kwargs):
        ingest_calls.append((location, kwargs))
        save_cursor(
            "linkedin", "", location, 10, status="COMPLETE", cycle_key=kwargs["cycle_key"]
        )
        return _result()

    monkeypatch.setattr(campaign, "fetch_linkedin_geography_page", fake_fetch)
    monkeypatch.setattr(campaign, "ingest_linkedin_geography_page", fake_ingest)
    result = campaign.run_linkedin_campaign(
        days=1, should_stop=lambda: False, deadline_reached=lambda: False
    )
    assert result.status == "COMPLETE"
    assert len(fetch_calls) == 1
    assert len(ingest_calls) == 1
    assert fetch_calls[0][0] == "New South Wales, Australia"
    assert fetch_calls[0][1]["hours_old"] == 24
    assert fetch_calls[0][1]["max_results"] == 1000


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

    monkeypatch.setattr(campaign, "fetch_linkedin_geography_page", lambda *_a, **_k: _fetched())

    def fake_ingest(fetched, location, **kwargs):
        processed.append(location)
        save_cursor(
            "linkedin", "", location, 10, status="COMPLETE", cycle_key=kwargs["cycle_key"]
        )
        return _result()

    monkeypatch.setattr(campaign, "ingest_linkedin_geography_page", fake_ingest)
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

    monkeypatch.setattr(
        campaign,
        "fetch_linkedin_geography_page",
        lambda *_a, **_k: _fetched(status="INCOMPLETE_CAP", start=990, next_offset=1000),
    )

    def fake_ingest(fetched, location, **kwargs):
        save_cursor(
            "linkedin",
            "",
            location,
            1000,
            status="INCOMPLETE_CAP",
            cycle_key=kwargs["cycle_key"],
        )
        return _result(status="INCOMPLETE_CAP")

    monkeypatch.setattr(campaign, "ingest_linkedin_geography_page", fake_ingest)
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

    monkeypatch.setattr(campaign, "fetch_linkedin_geography_page", fail)
    result = campaign.run_linkedin_campaign(
        days=1, should_stop=lambda: False, deadline_reached=lambda: False
    )
    assert result.status == "PARTIAL_FAILURE"
    assert result.failed_geographies == 3
    assert len(calls) == 3


def test_linkedin_fetches_geographies_in_parallel_but_ingests_on_one_thread(
    tmp_path, monkeypatch
):
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
    lock = threading.Lock()
    active_fetches = 0
    max_active_fetches = 0
    ingest_threads = []

    def fake_fetch(*_args, **_kwargs):
        nonlocal active_fetches, max_active_fetches
        with lock:
            active_fetches += 1
            max_active_fetches = max(max_active_fetches, active_fetches)
        time.sleep(0.05)
        with lock:
            active_fetches -= 1
        return _fetched()

    def fake_ingest(fetched, location, **kwargs):
        ingest_threads.append(threading.get_ident())
        save_cursor(
            "linkedin", "", location, 10, status="COMPLETE", cycle_key=kwargs["cycle_key"]
        )
        return _result()

    monkeypatch.setattr(campaign, "fetch_linkedin_geography_page", fake_fetch)
    monkeypatch.setattr(campaign, "ingest_linkedin_geography_page", fake_ingest)
    result = campaign.run_linkedin_campaign(
        days=1, should_stop=lambda: False, deadline_reached=lambda: False
    )
    assert result.status == "COMPLETE"
    assert max_active_fetches >= 2
    assert len(ingest_threads) == 3
    assert len(set(ingest_threads)) == 1
    assert ingest_threads[0] == threading.get_ident()
