import json

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


def _rich_observation(*, source: str, source_job_id: str, teaser_text: str):
    return CardObservation(
        source=source,
        source_job_id=source_job_id,
        canonical_url=f"https://{source}.test/jobs/{source_job_id}",
        title="Implementation Consultant",
        employer="Example Co",
        location="Sydney NSW",
        salary_text="$120k + super",
        employment_type="Full time",
        workplace_type="Hybrid",
        classification_text="Information & Communication Technology",
        subclassification_text="Consultants",
        teaser_text=teaser_text,
    )


def test_strong_cross_source_repost_keeps_source_rows_but_reuses_primary(tmp_path, monkeypatch):
    ingest = _use_tmp_db(tmp_path, monkeypatch)
    first = ingest.ingest_card(
        _rich_observation(
            source="seek", source_job_id="123", teaser_text="Lead enterprise onboarding and API integration."
        )
    )
    db.store_job_jd_once(
        first.job_id,
        full_description="One neutral canonical JD",
        jd_fetched_at="2026-09-11T00:00:00+00:00",
        jd_source="seek_job_page",
    )
    second = ingest.ingest_card(
        _rich_observation(
            source="linkedin", source_job_id="456", teaser_text="Lead enterprise onboarding and API integration."
        )
    )

    assert first.created is True
    assert second.created is False
    assert second.job_id == first.job_id
    assert second.observation_job_id != second.job_id
    assert db.get_job_jd(second.observation_job_id) == {
        "id": first.job_id,
        "full_description": "One neutral canonical JD",
        "jd_fetched_at": "2026-09-11T00:00:00+00:00",
        "jd_source": "seek_job_page",
    }
    with db.connect() as conn:
        alias = conn.execute(
            "SELECT primary_job_id FROM jobs WHERE source='linkedin' AND source_job_id='456'"
        ).fetchone()
        link = conn.execute("SELECT * FROM same_vacancy_links").fetchone()
    assert alias[0] == first.job_id
    assert link["job_id"] == second.observation_job_id
    assert link["primary_job_id"] == first.job_id
    assert link["match_type"] == "exact_rich_card"
    assert json.loads(link["matching_signals_json"])


def test_seek_linkedin_identical_intro_reuses_primary_when_other_fields_missing(
    tmp_path, monkeypatch
):
    ingest = _use_tmp_db(tmp_path, monkeypatch)
    teaser = (
        "Lead enterprise customer onboarding, API integration, testing and "
        "launch activities for strategic clients."
    )
    first = ingest.ingest_card(
        CardObservation(
            source="seek",
            source_job_id="123",
            canonical_url="https://seek.test/jobs/123",
            title="Implementation Consultant",
            employer="Example Co",
            teaser_text=teaser,
        )
    )
    second = ingest.ingest_card(
        CardObservation(
            source="linkedin",
            source_job_id="456",
            canonical_url="https://linkedin.test/jobs/456",
            title="Implementation Consultant",
            employer="Example Co",
            teaser_text=teaser,
        )
    )

    assert second.created is False
    assert second.job_id == first.job_id
    assert second.observation_job_id != first.job_id
    with db.connect() as conn:
        link = conn.execute(
            "SELECT * FROM same_vacancy_links WHERE job_id=?",
            (second.observation_job_id,),
        ).fetchone()
    assert link["primary_job_id"] == first.job_id
    assert link["match_type"] == "strong_teaser"
    assert "substantial teaser similarity 1.00" in json.loads(
        link["matching_signals_json"]
    )


def test_same_source_new_posting_id_reuses_oldest_primary(tmp_path, monkeypatch):
    ingest = _use_tmp_db(tmp_path, monkeypatch)
    first = ingest.ingest_card(
        _rich_observation(
            source="seek", source_job_id="123", teaser_text="Lead enterprise onboarding and API integration."
        )
    )
    second = ingest.ingest_card(
        _rich_observation(
            source="seek", source_job_id="456", teaser_text="Lead enterprise onboarding and API integration."
        )
    )

    assert second.created is False
    assert second.job_id == first.job_id
    assert second.observation_job_id != first.job_id
    with db.connect() as conn:
        source_rows = conn.execute(
            "SELECT source_job_id, primary_job_id FROM jobs WHERE source='seek' ORDER BY id"
        ).fetchall()
    assert [(row[0], row[1]) for row in source_rows] == [("123", None), ("456", first.job_id)]


def test_ambiguous_repost_candidate_remains_separate(tmp_path, monkeypatch):
    ingest = _use_tmp_db(tmp_path, monkeypatch)
    ingest.ingest_card(
        CardObservation(
            source="seek",
            source_job_id="123",
            canonical_url="https://seek.test/jobs/123",
            title="Project Manager",
            employer="Example Co",
            location="Sydney NSW",
        )
    )
    second = ingest.ingest_card(
        CardObservation(
            source="linkedin",
            source_job_id="456",
            canonical_url="https://linkedin.test/jobs/456",
            title="Project Manager",
            employer="Example Co",
            location="Melbourne VIC",
        )
    )

    assert second.created is True
    assert second.job_id == second.observation_job_id
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM same_vacancy_links").fetchone()[0] == 0
