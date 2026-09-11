from collector import db, service_state


def test_market_run_persists_structured_stats_and_bootstrap_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(service_state, "connect", db.connect)
    monkeypatch.setattr(service_state, "init_db", db.init_db)

    run_id = service_state.start_market_run(
        trigger="manual",
        mode="fresh",
        states=["NSW"],
        backup_path=None,
        run_kind="bootstrap",
    )
    service_state.finish_market_run(
        run_id,
        status="COMPLETE",
        message="done",
        stats={"duration_seconds": 42.5, "jobs_added": 12},
    )

    latest = service_state.latest_market_run()
    bootstrap = service_state.bootstrap_market_run()
    assert latest["id"] == run_id
    assert latest["stats"] == {"duration_seconds": 42.5, "jobs_added": 12}
    assert bootstrap["id"] == run_id
    assert bootstrap["run_kind"] == "bootstrap"
