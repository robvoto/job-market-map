from datetime import UTC, datetime, timedelta

from collector import db


def test_compacts_old_unimportant_job_but_keeps_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import retention

    monkeypatch.setattr(retention, "connect", db.connect)
    monkeypatch.setattr(retention, "init_db", db.init_db)

    db.init_db()
    now = datetime(2026, 9, 10, tzinfo=UTC)
    old = (now - timedelta(days=31)).isoformat(timespec="seconds")
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO jobs
            (source, source_job_id, canonical_url, title, employer, teaser_text, raw_card_text,
             first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "linkedin",
                "1",
                "https://example/jobs/1",
                "Role",
                "Company",
                "teaser",
                "raw",
                old,
                old,
            ),
        )
        job_id = cur.lastrowid
        conn.execute(
            "INSERT INTO card_captures(job_id, captured_at, raw_card_text) VALUES (?, ?, ?)",
            (job_id, old, "raw capture"),
        )

    result = retention.compact_stale_data(days=30, now=now)
    assert result.captures_deleted == 1
    assert result.jobs_compacted == 1
    with db.connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        captures = conn.execute("SELECT COUNT(*) FROM card_captures").fetchone()[0]
    assert job["archived"] == 1
    assert job["raw_card_text"] is None
    assert job["title"] == "Role"
    assert job["employer"] == "Company"
    assert captures == 0


def test_does_not_compact_old_job_with_meaningful_status(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import retention

    monkeypatch.setattr(retention, "connect", db.connect)
    monkeypatch.setattr(retention, "init_db", db.init_db)

    db.init_db()
    now = datetime(2026, 9, 10, tzinfo=UTC)
    old = (now - timedelta(days=60)).isoformat(timespec="seconds")
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO jobs
            (source, source_job_id, canonical_url, title, employer, raw_card_text,
             first_seen_at, last_seen_at, shown_to_rob)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                "seek",
                "2",
                "https://example/jobs/2",
                "Edge Role",
                "Employer",
                "keep me",
                old,
                old,
            ),
        )
        job_id = cur.lastrowid

    result = retention.compact_stale_data(days=30, now=now)
    assert result.jobs_compacted == 0
    with db.connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job["archived"] == 0
    assert job["raw_card_text"] == "keep me"
