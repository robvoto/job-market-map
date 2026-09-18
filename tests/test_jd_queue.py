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


def test_two_failed_attempts_hold_pending_job_for_diagnosis(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    job_id = _job("hold", "2026-09-17T20:00:24+10:00")
    assert jd_queue.enqueue_job_for_jd(
        job_id, source="linkedin", queued_at="2026-09-17T20:00:24+10:00"
    )

    def fails(value, *, source=None):
        raise JDSourceFetchError("LinkedIn page did not expose a validated JD")

    monkeypatch.setattr(jd_batch, "get_or_enrich_job_jd", fails)
    first = jd_batch.enrich_pending_jds(source="linkedin")

    assert first.failed == 1
    assert jd_queue.pending_primary_ids(source="linkedin") == []
    with db.connect() as conn:
        row = conn.execute(
            "SELECT attempts,last_error FROM jd_enrichment_queue WHERE job_id=?", (job_id,)
        ).fetchone()
    assert row["attempts"] == 2
    assert "validated JD" in row["last_error"]


def test_source_specific_queue_attempts_do_not_consume_other_source_budget(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    with db.connect() as conn:
        primary_id = int(conn.execute(
            """
            INSERT INTO jobs(source,source_job_id,identity_key,canonical_url,title)
            VALUES('seek','s1','seek:id:s1','https://seek.test/s1','Role')
            """
        ).lastrowid)
        linkedin_id = int(conn.execute(
            """
            INSERT INTO jobs(source,source_job_id,identity_key,canonical_url,title,primary_job_id)
            VALUES('linkedin','li-1','linkedin:id:li-1','https://linkedin.test/1','Role',?)
            """,
            (primary_id,),
        ).lastrowid)
        for job_id in (primary_id, linkedin_id):
            conn.execute(
                "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at) VALUES(?,?,?)",
                (job_id, '2026-09-17T20:00:00+10:00', '2026-09-17T20:00:00+10:00'),
            )

    assert jd_queue.enqueue_primary_for_source(
        primary_id, source='seek', queued_at='2026-09-17T20:00:00+10:00'
    )
    assert jd_queue.enqueue_primary_for_source(
        primary_id, source='linkedin', queued_at='2026-09-17T20:00:00+10:00'
    )
    jd_queue.record_pending_attempt(primary_id, source='linkedin', error='linkedin failed')

    with db.connect() as conn:
        rows = {
            row['source']: row['attempts']
            for row in conn.execute(
                "SELECT source,attempts FROM jd_enrichment_queue WHERE job_id IN (?,?)",
                (primary_id, linkedin_id),
            )
        }
    assert rows == {'seek': 0, 'linkedin': 1}
    assert jd_queue.pending_primary_ids(source='seek') == [primary_id]
    assert jd_queue.pending_primary_ids(source='linkedin') == [primary_id]


def test_deferred_current_run_queue_is_visible_to_next_run_without_reviving_old_debt(
    tmp_path, monkeypatch
):
    _wire(tmp_path, monkeypatch)
    old = _job("old-debt", "2026-09-17T10:00:00+10:00")
    current = _job("current", "2026-09-18T14:00:00+10:00")
    assert jd_queue.enqueue_job_for_jd(
        old, source="linkedin", queued_at="2026-09-17T10:00:00+10:00"
    )
    assert jd_queue.enqueue_job_for_jd(
        current, source="linkedin", queued_at="2026-09-18T14:00:00+10:00"
    )

    assert jd_queue.mark_pending_deferred(
        source="linkedin",
        queued_since="2026-09-18T14:00:00+10:00",
        reason="deferred by collection runtime limit",
    ) == 1

    assert jd_queue.pending_primary_ids(
        source="linkedin", queued_since="2026-09-18T20:00:00+10:00"
    ) == [current]
    assert old not in jd_queue.pending_primary_ids(
        source="linkedin", queued_since="2026-09-18T20:00:00+10:00"
    )
