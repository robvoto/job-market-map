from datetime import UTC, datetime, timedelta

from collector import db


def test_compacts_old_neutral_job_but_keeps_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import retention

    monkeypatch.setattr(retention, "connect", db.connect)
    monkeypatch.setattr(retention, "init_db", db.init_db)

    db.init_db()
    now = datetime(2026, 9, 10, tzinfo=UTC)
    old = (now - timedelta(days=31)).isoformat(timespec="seconds")
    with db.connect() as conn:
        job_id = conn.execute(
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
        ).lastrowid
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
    assert job["identity_key"] == "linkedin:id:1"
    assert captures == 0


def test_default_retention_preserves_historical_evidence(tmp_path, monkeypatch):
    """The default policy must never silently destroy useful market evidence."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import retention, settings

    for module in (retention, settings):
        monkeypatch.setattr(module, "connect", db.connect)
        monkeypatch.setattr(module, "init_db", db.init_db)

    db.init_db()
    now = datetime(2026, 9, 10, tzinfo=UTC)
    old = (now - timedelta(days=400)).isoformat(timespec="seconds")
    with db.connect() as conn:
        job_id = conn.execute(
            """INSERT INTO jobs
            (source, source_job_id, canonical_url, title, employer, teaser_text, raw_card_text,
             first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("seek", "safe1", "https://seek.test/safe1", "Role", "Company",
             "possible scam wording", "original raw evidence", old, old),
        ).lastrowid
        conn.execute(
            "INSERT INTO card_captures(job_id, captured_at, raw_card_text) VALUES (?, ?, ?)",
            (job_id, old, "original capture evidence"),
        )

    result = retention.apply_retention(now=now)

    assert result.captures_deleted == 0
    assert result.jobs_archived == 0
    assert result.jobs_removed == 0
    assert result.tombstones_written == 0
    with db.connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        capture = conn.execute(
            "SELECT raw_card_text FROM card_captures WHERE job_id=?", (job_id,)
        ).fetchone()
    assert job["archived"] == 0
    assert job["teaser_text"] == "possible scam wording"
    assert job["raw_card_text"] == "original raw evidence"
    assert capture[0] == "original capture evidence"
