from collector import db


def test_status_update_records_event(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import status

    monkeypatch.setattr(status, "connect", db.connect)
    monkeypatch.setattr(status, "init_db", db.init_db)
    db.init_db()
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO jobs(source, source_job_id, canonical_url, first_seen_at, last_seen_at) VALUES('seek','1','https://seek.test/1','2026-09-10','2026-09-10')"
        )
        job_id = cur.lastrowid
    status.set_status(job_id, "applied", True, actor="test", note="submitted")
    with db.connect() as conn:
        job = conn.execute(
            "SELECT applied FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        event = conn.execute(
            "SELECT event_type, event_value, actor FROM job_status_events WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    assert job[0] == 1
    assert tuple(event) == ("applied", 1, "test")


def test_status_write_is_idempotent_for_consumer_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import status

    monkeypatch.setattr(status, "connect", db.connect)
    monkeypatch.setattr(status, "init_db", db.init_db)
    db.init_db()
    with db.connect() as conn:
        job_id = conn.execute(
            "INSERT INTO jobs(source,source_job_id,canonical_url,first_seen_at,last_seen_at) VALUES('seek','x','https://x','2026-09-10','2026-09-10')"
        ).lastrowid
    first = status.set_status(
        job_id, "shown_to_rob", True, actor="job-hunter", idempotency_key="show-1"
    )
    second = status.set_status(
        job_id, "shown_to_rob", True, actor="job-hunter", idempotency_key="show-1"
    )
    assert first.changed is True
    assert second.changed is False
    assert first.event_id == second.event_id
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM job_status_events").fetchone()[0] == 1
