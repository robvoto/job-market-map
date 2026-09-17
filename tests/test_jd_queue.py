from collector import db, jd_batch, jd_queue
from collector.jd_enrichment import JDSourceFetchError


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(jd_queue, "connect", db.connect)
    monkeypatch.setattr(jd_batch, "connect", db.connect)
    db.init_db()


def _job(source_job_id: str, first_seen: str):
    with db.connect() as conn:
        job_id = int(conn.execute(
            """
            INSERT INTO jobs(source,source_job_id,identity_key,canonical_url,title)
            VALUES('linkedin',?,?,?,?)
            """,
            (source_job_id, f"linkedin:id:{source_job_id}", f"https://example.test/{source_job_id}", "Role"),
        ).lastrowid)
        conn.execute(
            "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at) VALUES(?,?,?)",
            (job_id, first_seen, first_seen),
        )
    return job_id


def test_seed_only_queues_new_missing_jobs(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    old = _job("old", "2026-09-17T19:59:59+10:00")
    new = _job("new", "2026-09-17T20:00:24+10:00")

    added = jd_queue.seed_new_missing_since(
        source="linkedin", first_seen_since="2026-09-17T20:00:24+10:00"
    )

    assert added == 1
    assert jd_queue.pending_primary_ids(source="linkedin") == [new]
    assert old not in jd_queue.pending_primary_ids(source="linkedin")


def test_rate_limit_keeps_pending_job_for_later_success(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    job_id = _job("new", "2026-09-17T20:00:24+10:00")
    assert jd_queue.enqueue_job_for_jd(
        job_id, source="linkedin", queued_at="2026-09-17T20:00:24+10:00"
    )
    calls = []

    def rate_limited(value, *, source=None):
        calls.append((value, source))
        raise JDSourceFetchError("LinkedIn JD fetch failed: LinkedIn vacancy HTTP 429")

    monkeypatch.setattr(jd_batch, "get_or_enrich_job_jd", rate_limited)
    first = jd_batch.enrich_pending_jds(source="linkedin")
    assert first.failed == 1
    assert calls == [(job_id, "linkedin")]
    assert jd_queue.pending_primary_ids(source="linkedin") == [job_id]

    def succeeds(value, *, source=None):
        db.store_job_jd_once(
            value,
            full_description="A sufficiently complete job description",
            jd_fetched_at="2026-09-17T11:00:00+00:00",
            jd_source="linkedin_public_job_page",
        )
        return {"status": "enriched"}

    monkeypatch.setattr(jd_batch, "get_or_enrich_job_jd", succeeds)
    second = jd_batch.enrich_pending_jds(source="linkedin")
    assert second.stored == 1
    assert jd_queue.pending_primary_ids(source="linkedin") == []
