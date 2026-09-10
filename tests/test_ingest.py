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
        state = conn.execute(
            "SELECT * FROM job_observation_state WHERE job_id = ?", (first.job_id,)
        ).fetchone()
        captures = conn.execute(
            "SELECT COUNT(*) FROM card_captures WHERE job_id = ?", (first.job_id,)
        ).fetchone()[0]
        hits = conn.execute(
            "SELECT COUNT(*) FROM job_query_hits WHERE job_id = ?", (first.job_id,)
        ).fetchone()[0]
    assert state["capture_count"] == 2
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
    assert {"first_seen_at", "last_seen_at", "capture_count", "archived", "compacted_at", "posted_text"}.isdisjoint(job.keys())
    assert captures == 2
    assert hits == 2


def test_card_text_and_relative_posted_label_never_become_canonical_jd_or_posted_date(tmp_path, monkeypatch):
    ingest = _use_tmp_db(tmp_path, monkeypatch)
    result = ingest.ingest_card(
        CardObservation(
            source="seek",
            source_job_id="94548673",
            canonical_url="https://au.seek.com/job/94548673",
            title="Release Manager (12 Month MTC)",
            teaser_text="Challenger is delivering a significant transformation program...",
            raw_card_text="Listed four hours ago\nRelease Manager (12 Month MTC)\nThis is a Full time job",
            posted_text="Listed four hours ago",
            employment_type="Full time",
            captured_at="2026-09-10T07:11:22+00:00",
        )
    )
    with db.connect() as conn:
        job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (result.job_id,)).fetchone())
        capture = conn.execute(
            "SELECT raw_json FROM card_captures WHERE job_id=?", (result.job_id,)
        ).fetchone()
    assert job["full_description"] is None
    assert job["posted_at"] is None
    assert "posted_text" not in job
    assert '"posted_text": "Listed four hours ago"' in capture[0]
