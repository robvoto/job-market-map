from collector import db


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import activity

    monkeypatch.setattr(activity, "connect", db.connect)
    monkeypatch.setattr(activity, "init_db", db.init_db)
    db.init_db()
    with db.connect() as conn:
        job_id = conn.execute(
            "INSERT INTO jobs(source,source_job_id,canonical_url,first_seen_at,last_seen_at) VALUES('seek','1','https://seek.test/1','2026-09-10','2026-09-10')"
        ).lastrowid
    return activity, job_id


def test_activity_is_per_user_and_not_on_job(tmp_path, monkeypatch):
    activity, job_id = _wire(tmp_path, monkeypatch)
    activity.record_activity(
        job_id, user_key="rob", activity_type="applied", actor="test"
    )
    with db.connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        events = conn.execute(
            "SELECT user_key,activity_type,activity_value FROM user_job_activity_events"
        ).fetchall()
    assert "applied" not in job
    assert [tuple(row) for row in events] == [("rob", "applied", 1)]
    assert activity.get_job_activity(job_id, user_key="alice")["current"] == []


def test_activity_write_is_idempotent_for_consumer_retry(tmp_path, monkeypatch):
    activity, job_id = _wire(tmp_path, monkeypatch)
    first = activity.record_activity(
        job_id,
        user_key="rob",
        activity_type="shown",
        actor="job-hunter",
        idempotency_key="show-1",
    )
    second = activity.record_activity(
        job_id,
        user_key="rob",
        activity_type="shown",
        actor="job-hunter",
        idempotency_key="show-1",
    )
    assert first.changed is True
    assert second.changed is False
    assert first.event_id == second.event_id
    with db.connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM user_job_activity_events").fetchone()[0]
            == 1
        )
