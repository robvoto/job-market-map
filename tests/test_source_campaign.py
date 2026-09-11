from collector import db


def _wire(tmp_path, monkeypatch):
    from collector import source_campaign

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(source_campaign, "connect", db.connect)
    monkeypatch.setattr(source_campaign, "init_db", db.init_db)
    db.init_db()
    return source_campaign


def test_incomplete_cap_is_terminal_but_not_complete(tmp_path, monkeypatch):
    source_campaign = _wire(tmp_path, monkeypatch)
    first = source_campaign.get_or_start_cycle("linkedin", desired_cycle_key="2026-09-11")
    capped = source_campaign.set_cycle_status(
        "linkedin", cycle_key=str(first["cycle_key"]), status="INCOMPLETE_CAP"
    )
    assert capped["status"] == "INCOMPLETE_CAP"
    assert capped["completed_at"] is not None

    same_day = source_campaign.get_or_start_cycle(
        "linkedin", desired_cycle_key="2026-09-11"
    )
    assert same_day["cycle_key"] == "2026-09-11"
    assert same_day["status"] == "INCOMPLETE_CAP"

    next_day = source_campaign.get_or_start_cycle(
        "linkedin", desired_cycle_key="2026-09-12"
    )
    assert next_day["cycle_key"] == "2026-09-12"
    assert next_day["status"] == "PARTIAL"
    assert next_day["completed_at"] is None
