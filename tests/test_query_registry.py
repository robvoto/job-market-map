import json
from pathlib import Path

from collector.query_registry import expanded_runs, load_registry


def test_registry_keys_unique_and_expands_sources_locations():
    specs = load_registry()
    keys = [s.key for s in specs]
    assert len(keys) == len(set(keys))
    assert len(specs) >= 90
    runs = expanded_runs()
    assert len(runs) > len(specs)
    assert any(r["source"] == "apsjobs" for r in runs)
    assert any(r["registry_key"] == "learned-api-integration" for r in runs)


def test_registry_is_neutral_metadata_not_fit_labels():
    raw = json.loads(Path("queries/query_registry.json").read_text(encoding="utf-8"))
    forbidden = {"fit", "score", "verdict", "reject", "recommend"}
    for query in raw["queries"]:
        assert forbidden.isdisjoint(query)


def test_registry_sync_preserves_admin_disabled_query(tmp_path, monkeypatch):
    import collector.query_registry as registry
    from collector import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(registry, "connect", db.connect)
    monkeypatch.setattr(registry, "init_db", db.init_db)
    registry.sync_registry()
    with db.connect() as conn:
        conn.execute(
            "UPDATE queries SET active=0 WHERE registry_key='normal-business-analyst' AND source='seek'"
        )
    registry.sync_registry()
    with db.connect() as conn:
        active = conn.execute(
            "SELECT active FROM queries WHERE registry_key='normal-business-analyst' AND source='seek'"
        ).fetchone()[0]
    assert active == 0


def test_registry_expands_each_seed_query_across_nsw_act_qld():
    runs = expanded_runs()
    seek_ba = [
        r
        for r in runs
        if r["source"] == "seek" and r["registry_key"] == "normal-business-analyst"
    ]
    assert {r["geography_code"] for r in seek_ba} == {"NSW", "ACT", "QLD"}
    locations = {r["geography_code"]: r["location"] for r in seek_ba}
    assert locations == {
        "NSW": "New South Wales NSW",
        "ACT": "Australian Capital Territory ACT",
        "QLD": "Queensland QLD",
    }
