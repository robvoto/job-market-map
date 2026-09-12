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


def test_latest_complete_fresh_seek_started_at_ignores_partial_and_resume_runs(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(service_state, "connect", db.connect)
    monkeypatch.setattr(service_state, "init_db", db.init_db)

    first = service_state.start_market_run(
        trigger="scheduled", mode="fresh", states=["NSW", "ACT"], backup_path=None
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE market_collection_runs SET started_at=? WHERE id=?",
            ("2026-09-11T00:00:00+00:00", first),
        )
    service_state.finish_market_run(
        first,
        status="COMPLETE",
        message="done",
        stats={
            "coverage": {
                "NSW": {"status": "COMPLETE_BY_PARTITION"},
                "ACT": {"status": "COMPLETE"},
            }
        },
    )

    partial = service_state.start_market_run(
        trigger="manual", mode="fresh", states=["NSW", "ACT"], backup_path=None
    )
    service_state.finish_market_run(
        partial,
        status="STOPPED",
        message="stopped",
        stats={
            "coverage": {
                "NSW": {"status": "COMPLETE_BY_PARTITION"},
                "ACT": {"status": "COMPLETE"},
            }
        },
    )

    resumed = service_state.start_market_run(
        trigger="manual", mode="resume", states=["NSW", "ACT"], backup_path=None
    )
    service_state.finish_market_run(
        resumed,
        status="COMPLETE",
        message="done on resume",
        stats={
            "coverage": {
                "NSW": {"status": "COMPLETE_BY_PARTITION"},
                "ACT": {"status": "COMPLETE"},
            }
        },
    )

    assert (
        service_state.latest_complete_fresh_seek_started_at(["NSW", "ACT"])
        == "2026-09-11T00:00:00+00:00"
    )


def test_latest_seek_market_run_excludes_bootstrap_and_other_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(service_state, "connect", db.connect)
    monkeypatch.setattr(service_state, "init_db", db.init_db)

    service_state.start_market_run(
        trigger="manual", mode="bootstrap", states=["NSW"], backup_path=None,
        source_scope="seek_whole_state", run_kind="bootstrap",
    )
    seek_id = service_state.start_market_run(
        trigger="manual", mode="fresh", states=["NSW"], backup_path=None,
        source_scope="seek_whole_state", run_kind="normal",
    )
    service_state.start_market_run(
        trigger="linkedin-manual", mode="rolling", states=["NSW"], backup_path=None,
        source_scope="linkedin_geography", run_kind="normal",
    )

    latest_seek = service_state.latest_seek_market_run()
    assert latest_seek is not None
    assert latest_seek["id"] == seek_id
    assert latest_seek["source_scope"] == "seek_whole_state"
