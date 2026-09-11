from collector import db
from collector.cursors import save_cursor


def _seed_isolated_registry(tmp_path, monkeypatch):
    from collector import campaign, query_admin, query_registry

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    for module in (query_admin, query_registry):
        monkeypatch.setattr(module, "connect", db.connect)
        monkeypatch.setattr(module, "init_db", db.init_db)
    query_registry.sync_registry()
    return campaign, query_admin


def test_campaign_uses_registry_without_fit_filtering(tmp_path, monkeypatch):
    campaign, _ = _seed_isolated_registry(tmp_path, monkeypatch)
    runs = campaign.registry_runs(sources={"linkedin"})
    assert len(runs) == 327
    assert any(run["registry_key"] == "normal-business-analyst" for run in runs)
    assert any(run["registry_key"] == "edge-technical-customer-success" for run in runs)
    assert any(run["registry_key"] == "z-cleaning" for run in runs)


def test_campaign_db_query_toggle_is_operational(tmp_path, monkeypatch):
    campaign, query_admin = _seed_isolated_registry(tmp_path, monkeypatch)
    row = query_admin.add_query(
        source="linkedin", query_text="unique admin query", active=True
    )
    assert any(
        r["query_id"] == row["id"] for r in campaign.registry_runs(sources={"linkedin"})
    )
    query_admin.set_query_active(row["id"], False)
    assert not any(
        r["query_id"] == row["id"] for r in campaign.registry_runs(sources={"linkedin"})
    )


def test_linkedin_campaign_resumes_same_cycle_after_runtime_limit(tmp_path, monkeypatch):
    campaign, query_admin = _seed_isolated_registry(tmp_path, monkeypatch)
    with db.connect() as conn:
        conn.execute("UPDATE queries SET active=0")
    for text in ("query one", "query two"):
        query_admin.add_query(
            source="linkedin",
            query_text=text,
            location="New South Wales, Australia",
            geography_code="NSW",
            active=True,
        )

    monkeypatch.setattr(
        campaign,
        "get_setting",
        lambda key: True if key == "collection.linkedin_enabled" else 1,
    )
    processed = []

    def fake_run_one(run, *, linkedin_cycle_key, **_kwargs):
        processed.append(run["query_text"])
        save_cursor(
            "linkedin",
            run["query_text"],
            run["location"],
            25,
            status="COMPLETE",
            cycle_key=linkedin_cycle_key,
        )
        return campaign.CampaignStep(
            run["registry_key"],
            "linkedin",
            run["query_text"],
            run["location"],
            "COMPLETE",
            2,
            1,
            {
                "cards_observed": 2,
                "unique_new_jobs": 1,
                "duplicate_observations": 1,
                "detail_attempted": 1,
                "detail_stored": 1,
                "detail_failed": 0,
            },
        )

    monkeypatch.setattr(campaign, "run_one", fake_run_one)
    first = campaign.run_linkedin_campaign(
        days=1,
        should_stop=lambda: False,
        deadline_reached=lambda: len(processed) >= 1,
    )
    assert first.status == "PARTIAL_TIME_LIMIT"
    assert first.queries_complete == 1
    first_cycle = first.cycle_key

    second = campaign.run_linkedin_campaign(
        days=1,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
    )
    assert second.status == "COMPLETE"
    assert second.queries_complete == 2
    assert second.cycle_key == first_cycle
    assert sorted(processed) == ["query one", "query two"]
    progress = campaign.linkedin_campaign_progress()
    assert progress["status"] == "COMPLETE"
    assert progress["queries_remaining"] == 0


def test_linkedin_campaign_has_no_browser_dependency():
    import inspect

    from collector import campaign
    from sources import linkedin_collector

    source = inspect.getsource(campaign) + inspect.getsource(linkedin_collector)
    assert "browser_broker" not in source
    assert "playwright" not in source.casefold()
    assert "chromium" not in source.casefold()


def test_linkedin_campaign_circuit_breaker_stops_source_outage(tmp_path, monkeypatch):
    campaign, query_admin = _seed_isolated_registry(tmp_path, monkeypatch)
    with db.connect() as conn:
        conn.execute("UPDATE queries SET active=0")
    for index in range(10):
        query_admin.add_query(
            source="linkedin",
            query_text=f"failing query {index}",
            location="New South Wales, Australia",
            geography_code="NSW",
            active=True,
        )

    def setting(key):
        if key == "collection.linkedin_enabled":
            return True
        if key == "collection.linkedin_max_consecutive_query_failures":
            return 6
        if key == "collection.seek_keyword_queries_enabled":
            return False
        raise AssertionError(key)

    monkeypatch.setattr(campaign, "get_setting", setting)
    calls = []

    def fail(run, **_kwargs):
        calls.append(run["query_text"])
        raise RuntimeError("LinkedIn unavailable")

    monkeypatch.setattr(campaign, "run_one", fail)
    result = campaign.run_linkedin_campaign(
        days=1,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
    )
    assert result.status == "PARTIAL_FAILURE"
    assert result.failed_queries == 6
    assert len(calls) == 6


def test_seek_keyword_registry_is_supplemental_and_off_by_default(
    tmp_path, monkeypatch
):
    campaign, _ = _seed_isolated_registry(tmp_path, monkeypatch)
    assert campaign.registry_runs(sources={"seek"}) == []


def test_seek_keyword_registry_can_be_enabled_by_admin_setting(tmp_path, monkeypatch):
    campaign, _ = _seed_isolated_registry(tmp_path, monkeypatch)
    from collector import settings

    monkeypatch.setattr(settings, "connect", db.connect)
    monkeypatch.setattr(settings, "init_db", db.init_db)
    settings.seed_settings()
    settings.set_setting("collection.seek_keyword_queries_enabled", True, actor="test")
    assert len(campaign.registry_runs(sources={"seek"})) == 327
