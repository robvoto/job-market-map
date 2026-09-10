from datetime import UTC, datetime, timedelta

from collector import db


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import duplicates, ingest, retention, settings

    for module in (retention, ingest, settings, duplicates):
        monkeypatch.setattr(module, "connect", db.connect)
        monkeypatch.setattr(module, "init_db", db.init_db)
    return retention, ingest


def _insert_job(last_seen: str, *, status: bool = False):
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO jobs(
                source, source_job_id, canonical_url, title, employer, location,
                raw_card_text, teaser_text, first_seen_at, last_seen_at,
                shown_to_rob, archived, core_fingerprint
            ) VALUES('seek','old1','https://seek.test/old1','Role','Acme','Sydney','raw','teaser',?,?,?,0,'core')""",
            (last_seen, last_seen, int(status)),
        )
        return cur.lastrowid


def test_archive_then_remove_to_tombstone(tmp_path, monkeypatch):
    retention, _ = _wire(tmp_path, monkeypatch)
    db.init_db()
    now = datetime(2026, 9, 10, tzinfo=UTC)
    old = (now - timedelta(days=130)).isoformat(timespec="seconds")
    job_id = _insert_job(old)
    result = retention.apply_retention(
        raw_capture_days=30,
        archive_after_days=30,
        remove_archived_after_days=120,
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


def test_status_job_is_preserved_by_default(tmp_path, monkeypatch):
    retention, _ = _wire(tmp_path, monkeypatch)
    db.init_db()
    now = datetime(2026, 9, 10, tzinfo=UTC)
    old = (now - timedelta(days=400)).isoformat(timespec="seconds")
    job_id = _insert_job(old, status=True)
    result = retention.apply_retention(
        raw_capture_days=30,
        archive_after_days=30,
        remove_archived_after_days=120,
        preserve_status_jobs=True,
        now=now,
    )
    assert result.jobs_archived == 0
    assert result.jobs_removed == 0
    with db.connect() as conn:
        assert conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()


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
        tomb_count = conn.execute("SELECT COUNT(*) FROM job_tombstones").fetchone()[0]
    assert job["first_seen_at"] == old
    assert job["archived"] == 0
    assert tomb_count == 0
