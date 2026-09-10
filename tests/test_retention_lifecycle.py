from datetime import UTC, datetime, timedelta

from collector import db


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import duplicates, ingest, retention, settings

    for module in (retention, ingest, settings, duplicates):
        monkeypatch.setattr(module, "connect", db.connect)
        monkeypatch.setattr(module, "init_db", db.init_db)
    return retention, ingest


def _insert_job(last_seen: str):
    with db.connect() as conn:
        job_id = conn.execute(
            """INSERT INTO jobs(
                source, source_job_id, canonical_url, title, employer, location,
                raw_card_text, teaser_text, core_fingerprint
            ) VALUES('seek','old1','https://seek.test/old1','Role','Acme','Sydney','raw','teaser','core')"""
        ).lastrowid
        conn.execute(
            "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at,archived) VALUES(?,?,?,0)",
            (job_id, last_seen, last_seen),
        )
        return job_id


def test_archive_then_remove_to_neutral_tombstone(tmp_path, monkeypatch):
    retention, _ = _wire(tmp_path, monkeypatch)
    db.init_db()
    now = datetime(2026, 9, 10, tzinfo=UTC)
    old = (now - timedelta(days=130)).isoformat(timespec="seconds")
    job_id = _insert_job(old)
    result = retention.apply_retention(
        raw_capture_days=30,
        archive_after_days=30,
        remove_archived_after_days=120,
        prune_raw_captures_enabled=True,
        archive_jobs_enabled=True,
        remove_archived_jobs_enabled=True,
        now=now,
    )
    assert result.jobs_archived == 1
    assert result.jobs_removed == 1
    assert result.tombstones_written == 1
    with db.connect() as conn:
        assert (
            conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is None
        )
        tomb = conn.execute(
            "SELECT * FROM job_tombstones WHERE source_job_id='old1'"
        ).fetchone()
    assert tomb["title"] == "Role"
    assert tomb["employer"] == "Acme"
    assert tomb["identity_key"] == "seek:id:old1"
    forbidden = {
        "shown_to_rob",
        "shown",
        "seen",
        "reviewed",
        "applied",
        "rejected",
        "dismissed",
    }
    assert forbidden.isdisjoint(tomb.keys())


def test_rediscovered_tombstone_is_resurrected_not_new(tmp_path, monkeypatch):
    retention, ingest = _wire(tmp_path, monkeypatch)
    from collector.models import CardObservation

    db.init_db()
    now = datetime(2026, 9, 10, tzinfo=UTC)
    old = (now - timedelta(days=130)).isoformat(timespec="seconds")
    _insert_job(old)
    retention.apply_retention(
        raw_capture_days=30,
        archive_after_days=30,
        remove_archived_after_days=120,
        prune_raw_captures_enabled=True,
        archive_jobs_enabled=True,
        remove_archived_jobs_enabled=True,
        now=now,
    )
    result = ingest.ingest_card(
        CardObservation(
            source="seek",
            source_job_id="old1",
            canonical_url="https://seek.test/old1",
            title="Role",
            employer="Acme",
            location="Sydney",
            raw_card_text="fresh",
            captured_at=now.isoformat(timespec="seconds"),
        )
    )
    assert result.created is False
    assert result.resurrected is True
    with db.connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (result.job_id,)).fetchone()
        state = conn.execute(
            "SELECT * FROM job_observation_state WHERE job_id=?", (result.job_id,)
        ).fetchone()
        tomb_count = conn.execute("SELECT COUNT(*) FROM job_tombstones").fetchone()[0]
    assert state["first_seen_at"] == old
    assert state["archived"] == 0
    assert job["identity_key"] == "seek:id:old1"
    assert tomb_count == 0
