from collector import db
from collector.ingest import ingest_card
from collector.models import CardObservation


def test_jd_is_stored_once_and_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs(
                source, source_job_id, canonical_url, title, first_seen_at, last_seen_at
            ) VALUES('seek', '123', 'https://seek.test/123', 'BA', 'x', 'x')
            """
        )

    assert db.get_job_jd(1) is None

    first = db.store_job_jd_once(
        1,
        full_description="Original neutral JD text",
        jd_fetched_at="2026-09-10T12:00:00+00:00",
        jd_source="seek",
    )
    assert first == {
        "id": 1,
        "full_description": "Original neutral JD text",
        "jd_fetched_at": "2026-09-10T12:00:00+00:00",
        "jd_source": "seek",
    }

    second = db.store_job_jd_once(
        1,
        full_description="Different text must not replace canonical JD",
        jd_fetched_at="2026-09-11T12:00:00+00:00",
        jd_source="other",
    )
    assert second == first
    assert db.get_job_jd(1) == first

    ingest_card(
        CardObservation(
            source="seek",
            source_job_id="123",
            canonical_url="https://seek.test/123",
            title="Updated card title",
            captured_at="2026-09-12T12:00:00+00:00",
        )
    )
    assert db.get_job_jd(1) == first


def test_successful_jd_fetch_creates_permanent_registry_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO jobs(source,source_job_id,canonical_url,title,first_seen_at,last_seen_at)
               VALUES('seek','999','https://seek.test/999','BA','x','x')"""
        )

    db.store_job_jd_once(
        1,
        full_description="A complete neutral job description long enough for storage.",
        jd_fetched_at="2026-09-10T12:00:00+00:00",
        jd_source="seek_job_page",
    )

    assert db.job_jd_fetch_completed(1) is True
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jd_fetch_registry").fetchone()
    assert row["identity_key"] == "seek:id:999"
    assert row["source_job_id"] == "999"
    assert row["fetched_at"] == "2026-09-10T12:00:00+00:00"


def test_existing_jd_is_backfilled_into_permanent_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO jobs(
                source,source_job_id,canonical_url,title,full_description,jd_fetched_at,jd_source,first_seen_at,last_seen_at
            ) VALUES('seek','888','https://seek.test/888','BA','Existing JD','2026-09-10T11:00:00+00:00','job_hunter_detail_evidence','x','x')"""
        )
        conn.execute("DELETE FROM jd_fetch_registry")

    db.init_db()
    assert db.job_jd_fetch_completed(1) is True
