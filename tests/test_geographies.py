from collector import db


def test_catalog_has_only_current_enabled_state_scope():
    from collector.geographies import load_catalog

    rows = load_catalog()
    assert {r["code"] for r in rows} == {"NSW", "ACT", "QLD"}
    assert all(r["enabled"] for r in rows)


def test_admin_can_disable_state_without_code_change(tmp_path, monkeypatch):
    from collector import geographies

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(geographies, "connect", db.connect)
    monkeypatch.setattr(geographies, "init_db", db.init_db)
    geographies.seed_geographies()
    changed = geographies.set_geography_enabled("QLD", False, actor="rob-test")
    assert changed["enabled"] == 0
    assert {g["code"] for g in geographies.list_geographies(enabled_only=True)} == {
        "NSW",
        "ACT",
    }
