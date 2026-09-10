from types import SimpleNamespace

from collector import db


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
    runs = campaign.registry_runs(sources={"seek"})
    assert len(runs) == 327
    assert any(run["registry_key"] == "normal-business-analyst" for run in runs)
    assert any(run["registry_key"] == "edge-technical-customer-success" for run in runs)
    assert any(run["registry_key"] == "z-cleaning" for run in runs)


def test_campaign_db_query_toggle_is_operational(tmp_path, monkeypatch):
    campaign, query_admin = _seed_isolated_registry(tmp_path, monkeypatch)
    row = query_admin.add_query(
        source="seek", query_text="unique admin query", active=True
    )
    assert any(
        r["query_id"] == row["id"] for r in campaign.registry_runs(sources={"seek"})
    )
    query_admin.set_query_active(row["id"], False)
    assert not any(
        r["query_id"] == row["id"] for r in campaign.registry_runs(sources={"seek"})
    )


def test_multi_step_campaign_opens_one_tab_and_reuses_page_id(monkeypatch):
    from collector import campaign

    opened = {"count": 0}
    page_ids = []

    def fake_open_tab(url, active=False):
        assert url == "about:blank"
        assert active is False
        opened["count"] += 1
        return SimpleNamespace(result={"pageId": 777})

    def fake_run_one(run, *, linkedin_max_offsets=None, days=None, page_id=None):
        page_ids.append(page_id)
        return campaign.CampaignStep(
            run["registry_key"],
            run["source"],
            run["query_text"],
            run["location"],
            "COMPLETE",
            1,
            1,
            {},
        )

    import collector.browser_broker as broker

    monkeypatch.setattr(broker, "open_tab", fake_open_tab)
    monkeypatch.setattr(campaign, "run_one", fake_run_one)
    runs = [
        {
            "registry_key": "a",
            "source": "seek",
            "query_text": "one",
            "location": "Sydney NSW",
        },
        {
            "registry_key": "b",
            "source": "linkedin",
            "query_text": "two",
            "location": "Sydney NSW",
        },
        {
            "registry_key": "c",
            "source": "seek",
            "query_text": "three",
            "location": "Sydney NSW",
        },
    ]
    steps = campaign.run_steps_in_one_browser_tab(runs)
    assert len(steps) == 3
    assert opened["count"] == 1
    assert page_ids == [777, 777, 777]
