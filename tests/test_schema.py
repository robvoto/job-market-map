import pytest

from collector.db import connect, init_db


def test_schema_is_neutral_and_keeps_consumer_checkpoints():
    init_db()
    with connect() as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        tomb_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(job_tombstones)")
        }
    assert {
        "jobs",
        "card_captures",
        "queries",
        "job_query_hits",
        "consumer_checkpoints",
    } <= tables
    assert "user_job_activity_events" not in tables
    assert "user_job_activity_current" not in tables
    assert "job_status_events" not in tables
    forbidden = {
        "shown_to_rob",
        "shown",
        "seen",
        "reviewed",
        "applied",
        "rejected",
        "dismissed",
    }
    assert forbidden.isdisjoint(job_columns)
    assert forbidden.isdisjoint(tomb_columns)
    assert "identity_key" in job_columns
    assert {"full_description", "jd_fetched_at", "jd_source"} <= job_columns
    assert "jd_snapshots" not in tables
    assert "job_description_snapshots" not in tables


def test_empty_obsolete_activity_scaffolding_is_removed(tmp_path, monkeypatch):
    from collector import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            "CREATE TABLE user_job_activity_events(id INTEGER PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "CREATE TABLE user_job_activity_current(id INTEGER PRIMARY KEY, value TEXT)"
        )
        db._remove_obsolete_personal_activity_scaffolding(conn)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "user_job_activity_events" not in tables
    assert "user_job_activity_current" not in tables


def test_nonempty_obsolete_activity_scaffolding_fails_closed(tmp_path, monkeypatch):
    from collector import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            "CREATE TABLE user_job_activity_events(id INTEGER PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT INTO user_job_activity_events(value) VALUES('do not lose me')"
        )
        with pytest.raises(RuntimeError, match="JH-305"):
            db._remove_obsolete_personal_activity_scaffolding(conn)
