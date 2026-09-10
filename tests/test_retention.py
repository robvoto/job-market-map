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
            (source, source_job_id, canonical_url, title, employer, teaser_text, raw_card_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("linkedin", "1", "https://example/jobs/1", "Role", "Company", "teaser", "raw"),
        ).lastrowid
        conn.execute(
            "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at) VALUES(?,?,?)",
            (job_id, old, old),
        )
        conn.execute(
            "INSERT INTO card_captures(job_id, captured_at, raw_card_text) VALUES (?, ?, ?)",
            (job_id, old, "raw capture"),
        )

    result = retention.compact_stale_data(days=30, now=now)
    assert result.captures_deleted == 1
    assert result.jobs_compacted == 1
    with db.connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        state = conn.execute(
            "SELECT * FROM job_observation_state WHERE job_id=?", (job_id,)
        ).fetchone()
        captures = conn.execute("SELECT COUNT(*) FROM card_captures").fetchone()[0]
    assert state["archived"] == 1
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
            (source, source_job_id, canonical_url, title, employer, teaser_text, raw_card_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("seek", "safe1", "https://seek.test/safe1", "Role", "Company",
             "possible scam wording", "original raw evidence"),
        ).lastrowid
        conn.execute(
            "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at) VALUES(?,?,?)",
            (job_id, old, old),
        )
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
        state = conn.execute(
            "SELECT * FROM job_observation_state WHERE job_id=?", (job_id,)
        ).fetchone()
        capture = conn.execute(
            "SELECT raw_card_text FROM card_captures WHERE job_id=?", (job_id,)
        ).fetchone()
    assert state["archived"] == 0
    assert job["teaser_text"] == "possible scam wording"
    assert job["raw_card_text"] == "original raw evidence"
    assert capture[0] == "original capture evidence"


def test_permanent_jd_fetch_registry_survives_job_removal(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import retention

    monkeypatch.setattr(retention, "connect", db.connect)
    monkeypatch.setattr(retention, "init_db", db.init_db)
    db.init_db()
    now = datetime(2026, 9, 10, tzinfo=UTC)
    old = (now - timedelta(days=200)).isoformat(timespec="seconds")
    with db.connect() as conn:
        job_id = conn.execute(
            """INSERT INTO jobs(source,source_job_id,canonical_url,title)
               VALUES('seek','perm1','https://seek.test/perm1','Role')"""
        ).lastrowid
        conn.execute(
            "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at) VALUES(?,?,?)",
            (job_id, old, old),
        )

    db.store_job_jd_once(
        job_id,
        full_description="Permanent fetch memory must outlive the bulky JD record.",
        jd_fetched_at=old,
        jd_source="seek_job_page",
    )
    result = retention.apply_retention(
        archive_after_days=30,
        remove_archived_after_days=120,
        archive_jobs_enabled=True,
        remove_archived_jobs_enabled=True,
        now=now,
    )
    assert result.jobs_removed == 1
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        marker = conn.execute(
            "SELECT identity_key FROM jd_fetch_registry WHERE identity_key='seek:id:perm1'"
        ).fetchone()
    assert marker is not None
