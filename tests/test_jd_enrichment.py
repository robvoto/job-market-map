import pytest

from collector import db


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import jd_enrichment

    monkeypatch.setattr(jd_enrichment, "connect", db.connect)
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
    job_id = _insert_job(source="apsjobs", source_job_id="abc")

    with pytest.raises(enrichment.UnsupportedJDSourceError, match="apsjobs"):
        enrichment.get_or_enrich_job_jd(job_id)


def test_linked_vacancy_prefers_linkedin_http_then_falls_back_to_seek(tmp_path, monkeypatch):
    enrichment = _wire(tmp_path, monkeypatch)
    seek_id = _insert_job(source="seek", source_job_id="seek-1")
    linkedin_id = _insert_job(source="linkedin", source_job_id="li-1")
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET primary_job_id=? WHERE id=?", (seek_id, linkedin_id))

    calls = []

    def linkedin_fail(job):
        calls.append(("linkedin", job["id"]))
        raise enrichment.JDSourceFetchError("public page unavailable")

    def seek_fetch(job):
        calls.append(("seek", job["id"]))
        return enrichment.FetchedJD(
            full_description="Validated SEEK fallback JD for the linked vacancy.",
            jd_source="seek_job_page",
            facts={"location": "Sydney NSW"},
        )

    monkeypatch.setitem(enrichment._SOURCE_FETCHERS, "linkedin", linkedin_fail)
    monkeypatch.setitem(enrichment._SOURCE_FETCHERS, "seek", seek_fetch)

    result = enrichment.get_or_enrich_job_jd(seek_id)
    assert result["status"] == "enriched"
    assert calls == [("linkedin", linkedin_id), ("seek", seek_id)]
    with db.connect() as conn:
        primary = conn.execute("SELECT * FROM jobs WHERE id=?", (seek_id,)).fetchone()
        alias = conn.execute("SELECT * FROM jobs WHERE id=?", (linkedin_id,)).fetchone()
    assert primary["full_description"] == result["full_description"]
    assert primary["location"] == "Sydney NSW"
    assert alias["full_description"] is None


def test_linked_vacancy_updates_facts_on_source_that_supplied_jd(tmp_path, monkeypatch):
    enrichment = _wire(tmp_path, monkeypatch)
    seek_id = _insert_job(source="seek", source_job_id="seek-1")
    linkedin_id = _insert_job(source="linkedin", source_job_id="li-1")
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET primary_job_id=? WHERE id=?", (seek_id, linkedin_id))

    def linkedin_fetch(job):
        return enrichment.FetchedJD(
            full_description="Validated LinkedIn JD for the linked vacancy.",
            jd_source="linkedin_public_job_page",
            facts={"applicant_count": 17},
        )

    monkeypatch.setitem(enrichment._SOURCE_FETCHERS, "linkedin", linkedin_fetch)
    monkeypatch.setitem(
        enrichment._SOURCE_FETCHERS,
        "seek",
        lambda _job: (_ for _ in ()).throw(AssertionError("SEEK fallback must not run")),
    )

    enrichment.get_or_enrich_job_jd(seek_id)
    with db.connect() as conn:
        primary = conn.execute("SELECT * FROM jobs WHERE id=?", (seek_id,)).fetchone()
        alias = conn.execute("SELECT * FROM jobs WHERE id=?", (linkedin_id,)).fetchone()
    assert primary["applicant_count"] is None
    assert alias["applicant_count"] == 17
