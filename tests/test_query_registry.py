import json
from pathlib import Path

from collector.query_registry import expanded_runs, load_registry


def test_registry_keys_unique_and_expands_sources_locations():
    specs = load_registry()
    keys = [s.key for s in specs]
    assert len(keys) == len(set(keys))
    assert len(specs) == 10
    runs = expanded_runs()
    assert len(runs) == 30
    assert any(r["source"] == "apsjobs" for r in runs)
    assert not any(r["source"] == "linkedin" for r in runs)
    assert any(r["registry_key"] == "normal-business-analyst" for r in runs)


def test_registry_is_neutral_metadata_not_fit_labels():
    raw = json.loads(Path("queries/query_registry.json").read_text(encoding="utf-8"))
    forbidden = {"fit", "score", "verdict", "reject", "recommend"}
    for query in raw["queries"]:
        assert forbidden.isdisjoint(query)


def test_registry_sync_deactivates_retired_linkedin_keyword_rows(tmp_path, monkeypatch):
    import collector.query_registry as registry
    from collector import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(registry, "connect", db.connect)
    monkeypatch.setattr(registry, "init_db", db.init_db)
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO queries(
                source, query_text, location, geography_code, active, created_at,
                registry_key, origins_json
            ) VALUES ('linkedin', 'business analyst', 'New South Wales, Australia', 'NSW', 1,
                      datetime('now'), 'normal-business-analyst', '[]')
            """
        )
    registry.sync_registry()
    with db.connect() as conn:
        active = conn.execute(
            "SELECT active FROM queries WHERE registry_key='normal-business-analyst' AND source='linkedin'"
        ).fetchone()[0]
    assert active == 0


def test_registry_sync_deactivates_removed_seeded_query_key(tmp_path, monkeypatch):
    import collector.query_registry as registry
    from collector import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(registry, "connect", db.connect)
    monkeypatch.setattr(registry, "init_db", db.init_db)
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO queries(
                source, query_text, location, geography_code, active, created_at,
                registry_key, origins_json
            ) VALUES ('linkedin', 'retired role', 'Queensland, Australia', 'QLD', 1,
                      datetime('now'), 'retired-linkedin-only-key', '[]')
            """
        )
    registry.sync_registry()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT active FROM queries WHERE registry_key='retired-linkedin-only-key'"
        ).fetchone()
    assert row is not None
    assert row["active"] == 0


def test_registry_does_not_seed_seek_or_linkedin_keyword_runs():
    runs = expanded_runs()
    assert not any(run["source"] == "seek" for run in runs)
    assert not any(run["source"] == "linkedin" for run in runs)
    assert any(run["source"] == "apsjobs" for run in runs)


def test_registry_sync_deactivates_retired_seek_rows_without_deleting_history(
    tmp_path, monkeypatch
):
    import collector.query_registry as registry
    from collector import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(registry, "connect", db.connect)
    monkeypatch.setattr(registry, "init_db", db.init_db)
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO queries(
                source, query_text, location, geography_code, active, created_at,
                registry_key, origins_json
            ) VALUES ('seek', 'business analyst', 'New South Wales NSW', 'NSW', 1,
                      datetime('now'), 'normal-business-analyst', '[]')
            """
        )
    registry.sync_registry()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT active FROM queries WHERE source='seek' AND registry_key='normal-business-analyst'"
        ).fetchone()
    assert row is not None
    assert row["active"] == 0
