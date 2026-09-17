from collector import db, jd_batch
from collector.jd_enrichment import JDSourceFetchError


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(jd_batch, "connect", db.connect)
    db.init_db()


def _insert_job(*, source: str, source_job_id: str, posted_at: str, first_seen_at: str):
    with db.connect() as conn:
        job_id = conn.execute(
            """
            INSERT INTO jobs(source,source_job_id,identity_key,canonical_url,title,posted_at)
            VALUES(?,?,?,?,?,?)
            """,
            (source, source_job_id, f"{source}:id:{source_job_id}", f"https://example.test/{source_job_id}", "Role", posted_at),
        ).lastrowid
        conn.execute(
            "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at) VALUES(?,?,?)",
            (job_id, first_seen_at, first_seen_at),
        )
    return int(job_id)


def test_missing_jd_selection_can_be_bounded_by_posted_date(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    recent = _insert_job(source="seek", source_job_id="recent", posted_at="2026-09-16T00:00:00+10:00", first_seen_at="2026-09-17T00:00:00+10:00")
    _insert_job(source="seek", source_job_id="old", posted_at="2026-09-15T23:59:59+10:00", first_seen_at="2026-09-17T00:00:00+10:00")
    assert jd_batch.missing_jd_primary_ids(source="seek", posted_since="2026-09-16T00:00:00+10:00") == [recent]


def test_batch_retries_one_technical_failure_then_succeeds(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    job_id = _insert_job(source="seek", source_job_id="1", posted_at="2026-09-17T00:00:00+10:00", first_seen_at="2026-09-17T00:00:00+10:00")
    calls = []

    def fake_enrich(value, *, source=None):
        calls.append((value, source))
        if len(calls) == 1:
            raise JDSourceFetchError("temporary")
        return {"status": "enriched"}

    monkeypatch.setattr(jd_batch, "get_or_enrich_job_jd", fake_enrich)
    result = jd_batch.enrich_missing_jds(source="seek", first_seen_since="2026-09-17T00:00:00+10:00")
    assert calls == [(job_id, "seek"), (job_id, "seek")]
    assert result.stored == 1
    assert result.failed == 0


def test_batch_logs_failure_after_exactly_one_retry(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    job_id = _insert_job(source="linkedin", source_job_id="2", posted_at="2026-09-17T00:00:00+10:00", first_seen_at="2026-09-17T00:00:00+10:00")
    calls = []

    def fail(value, *, source=None):
        calls.append((value, source))
        raise JDSourceFetchError("still broken")

    monkeypatch.setattr(jd_batch, "get_or_enrich_job_jd", fail)
    result = jd_batch.enrich_missing_jds(source="linkedin", first_seen_since="2026-09-17T00:00:00+10:00")
    assert calls == [(job_id, "linkedin"), (job_id, "linkedin")]
    assert result.failed == 1
    assert result.stored == 0


def test_batch_does_not_retry_rate_limit(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    job_id = _insert_job(
        source="linkedin",
        source_job_id="429",
        posted_at="2026-09-17T00:00:00+10:00",
        first_seen_at="2026-09-17T00:00:00+10:00",
    )
    calls = []

    def rate_limited(value, *, source=None):
        calls.append((value, source))
        raise JDSourceFetchError("LinkedIn JD fetch failed: LinkedIn vacancy HTTP 429")

    monkeypatch.setattr(jd_batch, "get_or_enrich_job_jd", rate_limited)
    result = jd_batch.enrich_missing_jds(
        source="linkedin",
        first_seen_since="2026-09-17T00:00:00+10:00",
    )

    assert calls == [(job_id, "linkedin")]
    assert result.failed == 1
    assert result.stored == 0


def test_linkedin_date_only_cutoff_uses_local_calendar_date(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    included = _insert_job(
        source="linkedin", source_job_id="li-16", posted_at="2026-09-16",
        first_seen_at="2026-09-17T00:00:00+10:00"
    )
    _insert_job(
        source="linkedin", source_job_id="li-15", posted_at="2026-09-15",
        first_seen_at="2026-09-17T00:00:00+10:00"
    )
    assert jd_batch.recent_primary_ids(
        source="linkedin", posted_since="2026-09-16T00:00:00+10:00"
    ) == [included]


def test_seek_cutoff_compares_absolute_timestamp(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    included = _insert_job(
        source="seek", source_job_id="seek-at", posted_at="2026-09-15T14:00:00Z",
        first_seen_at="2026-09-17T00:00:00+10:00"
    )
    _insert_job(
        source="seek", source_job_id="seek-before", posted_at="2026-09-15T13:59:59Z",
        first_seen_at="2026-09-17T00:00:00+10:00"
    )
    assert jd_batch.recent_primary_ids(
        source="seek", posted_since="2026-09-16T00:00:00+10:00"
    ) == [included]
