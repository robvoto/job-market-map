from collector.db import connect, init_db


def test_schema_initialises_with_neutral_jobs_and_user_activity_ledger():
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
        "user_job_activity_events",
        "user_job_activity_current",
    } <= tables
    assert "job_status_events" not in tables
    forbidden = {"shown_to_rob", "reviewed", "applied", "rejected", "dismissed"}
    assert forbidden.isdisjoint(job_columns)
    assert forbidden.isdisjoint(tomb_columns)
    assert "identity_key" in job_columns


def test_legacy_global_status_migrates_to_rob_activity_and_is_removed(
    tmp_path, monkeypatch
):
    from collector import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN shown_to_rob INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            """CREATE TABLE job_status_events(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_value INTEGER NOT NULL,
                occurred_at TEXT NOT NULL,
                actor TEXT,
                note TEXT,
                idempotency_key TEXT
            )"""
        )
        job_id = conn.execute(
            """INSERT INTO jobs(source,source_job_id,canonical_url,first_seen_at,last_seen_at,shown_to_rob)
               VALUES('seek','legacy-1','https://seek.test/legacy-1','2026-09-01','2026-09-01',1)"""
        ).lastrowid
        conn.execute(
            """INSERT INTO job_status_events(job_id,event_type,event_value,occurred_at,actor,note)
               VALUES(?, 'shown_to_rob', 1, '2026-09-02', 'reset-edge', 'legacy event')""",
            (job_id,),
        )
        db._migrate_legacy_activity(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        events = conn.execute(
            "SELECT user_key,activity_type,activity_value,actor FROM user_job_activity_events"
        ).fetchall()
        current = conn.execute(
            "SELECT user_key,activity_type,active FROM user_job_activity_current"
        ).fetchall()
    assert "shown_to_rob" not in cols
    assert "job_status_events" not in tables
    assert [tuple(row) for row in events] == [("rob", "shown", 1, "reset-edge")]
    assert [tuple(row) for row in current] == [("rob", "shown", 1)]
