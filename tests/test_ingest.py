from collector import db
from collector.ingest import canonicalise_url
from collector.models import CardObservation


def _use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import ingest

    monkeypatch.setattr(ingest, "connect", db.connect)
    monkeypatch.setattr(ingest, "init_db", db.init_db)
    return ingest


def test_canonicalise_url_drops_tracking_but_keeps_meaningful_query():
    assert (
        canonicalise_url("https://EXAMPLE.com/jobs/1/?x=2&utm_source=a#frag")
        == "https://example.com/jobs/1?x=2"
    )


def test_same_source_id_upserts_neutral_market_row(tmp_path, monkeypatch):
    ingest = _use_tmp_db(tmp_path, monkeypatch)
    first = ingest.ingest_card(
        CardObservation(
            source="LinkedIn",
            source_job_id="123",
            canonical_url="https://linkedin.com/jobs/view/123?trackingId=x",
            title="Technical Integration Specialist",
            employer="Acme",
            raw_card_text="first card",
            query_text="technical integration",
            query_location="Sydney NSW",
            captured_at="2026-09-10T01:00:00+00:00",
        )
    )
    second = ingest.ingest_card(
        CardObservation(
            source="linkedin",
            source_job_id="123",
            canonical_url="https://linkedin.com/jobs/view/123",
            title="Technical Integration Specialist",
            employer="Acme",
            location="Sydney",
            raw_card_text="second card",
            query_text="solutions specialist",
            query_location="Sydney NSW",
            captured_at="2026-09-10T02:00:00+00:00",
        )
    )

    assert second.created is False
    assert second.job_id == first.job_id
    with db.connect() as conn:
        job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (first.job_id,)
        ).fetchone()
        captures = conn.execute(
            "SELECT COUNT(*) FROM card_captures WHERE job_id = ?", (first.job_id,)
        ).fetchone()[0]
        hits = conn.execute(
            "SELECT COUNT(*) FROM job_query_hits WHERE job_id = ?", (first.job_id,)
        ).fetchone()[0]
    assert job["capture_count"] == 2
    assert job["identity_key"] == "linkedin:id:123"
    assert job["location"] == "Sydney"
    forbidden = {
        "shown_to_rob",
        "shown",
        "seen",
        "reviewed",
        "applied",
        "rejected",
        "dismissed",
    }
    assert forbidden.isdisjoint(job.keys())
    assert captures == 2
    assert hits == 2
