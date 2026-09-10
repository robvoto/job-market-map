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
