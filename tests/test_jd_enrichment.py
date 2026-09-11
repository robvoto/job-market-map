import pytest

from collector import db


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import jd_enrichment

    monkeypatch.setattr(jd_enrichment, "get_job_by_id", db.get_job_by_id)
    monkeypatch.setattr(jd_enrichment, "get_job_jd", db.get_job_jd)
    monkeypatch.setattr(jd_enrichment, "store_job_jd_once", db.store_job_jd_once)
    monkeypatch.setattr(
        jd_enrichment, "update_job_source_facts", db.update_job_source_facts
    )
    db.init_db()
    return jd_enrichment


def _insert_job(*, source="seek", source_job_id="123"):
    with db.connect() as conn:
        return conn.execute(
            """
            INSERT INTO jobs(source,source_job_id,canonical_url,title,employer)
            VALUES(?,?,?,?,?)
            """,
            (
                source,
                source_job_id,
                f"https://example.test/{source_job_id}",
                "Role",
                "Acme",
            ),
        ).lastrowid


def test_cached_jd_is_returned_without_source_fetch(tmp_path, monkeypatch):
    enrichment = _wire(tmp_path, monkeypatch)
    job_id = _insert_job()
    db.store_job_jd_once(
        job_id,
        full_description="Existing canonical JD",
        jd_fetched_at="2026-09-11T00:00:00+00:00",
        jd_source="seek_job_page",
    )

    def should_not_fetch(_job):
        raise AssertionError("cached JD must not refetch")

    monkeypatch.setitem(enrichment._SOURCE_FETCHERS, "seek", should_not_fetch)
    result = enrichment.get_or_enrich_job_jd(job_id)

    assert result["status"] == "cached"
    assert result["full_description"] == "Existing canonical JD"


def test_missing_seek_jd_fetches_stores_and_then_reuses(tmp_path, monkeypatch):
    enrichment = _wire(tmp_path, monkeypatch)
    job_id = _insert_job()
    calls = []

    def fetch(job):
        calls.append(job["id"])
        return enrichment.FetchedJD(
            full_description="A validated full SEEK job description long enough to be useful.",
            jd_source="seek_job_page",
            facts={"location": "Sydney NSW"},
        )

    monkeypatch.setitem(enrichment._SOURCE_FETCHERS, "seek", fetch)
    first = enrichment.get_or_enrich_job_jd(job_id)
    second = enrichment.get_or_enrich_job_jd(job_id)

    assert first["status"] == "enriched"
    assert second["status"] == "cached"
    assert calls == [job_id]
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        registry = conn.execute(
            "SELECT * FROM jd_fetch_registry WHERE identity_key=?",
            (row["identity_key"],),
        ).fetchone()
    assert row["full_description"] == first["full_description"]
    assert row["location"] == "Sydney NSW"
    assert registry is not None


def test_fetch_failure_creates_no_jd_or_success_marker(tmp_path, monkeypatch):
    enrichment = _wire(tmp_path, monkeypatch)
    job_id = _insert_job()

    def fail(_job):
        raise enrichment.JDSourceFetchError("source blocked")

    monkeypatch.setitem(enrichment._SOURCE_FETCHERS, "seek", fail)
    with pytest.raises(enrichment.JDSourceFetchError, match="source blocked"):
        enrichment.get_or_enrich_job_jd(job_id)

    assert db.get_job_jd(job_id) is None
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jd_fetch_registry").fetchone()[0] == 0


def test_unsupported_source_fails_explicitly(tmp_path, monkeypatch):
    enrichment = _wire(tmp_path, monkeypatch)
    job_id = _insert_job(source="linkedin", source_job_id="abc")

    with pytest.raises(enrichment.UnsupportedJDSourceError, match="linkedin"):
        enrichment.get_or_enrich_job_jd(job_id)
