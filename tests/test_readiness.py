from collector import db, readiness


def _insert_active_job(conn, source: str, source_job_id: str) -> int:
    job_id = conn.execute(
        """INSERT INTO jobs(source,source_job_id,canonical_url,title,employer)
           VALUES(?,?,?,?,?)""",
        (
            source,
            source_job_id,
            f"https://{source}.example/jobs/{source_job_id}",
            f"Role {source_job_id}",
            "Acme",
        ),
    ).lastrowid
    conn.execute(
        """INSERT INTO job_observation_state(
               job_id,first_seen_at,last_seen_at,capture_count,archived
           ) VALUES(?,?,?,1,0)""",
        (job_id, "2026-09-22T00:00:00+00:00", "2026-09-22T00:00:00+00:00"),
    )
    return int(job_id)


def test_readiness_partitions_active_jd_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    with db.connect() as conn:
        available = _insert_active_job(conn, "seek", "1")
        _insert_active_job(conn, "linkedin", "2")
        failed = _insert_active_job(conn, "seek", "3")
        conn.execute(
            """UPDATE jobs
                  SET full_description='Cached JD',
                      jd_fetched_at='2026-09-22T01:00:00+00:00',
                      jd_source='seek_job_page'
                WHERE id=?""",
            (available,),
        )
        conn.execute(
            """INSERT INTO jd_enrichment_queue(
                   job_id,source,queued_at,attempts,last_attempt_at,last_error
               ) VALUES(?,?,?,?,?,?)""",
            (
                failed,
                "seek",
                "2026-09-22T01:00:00+00:00",
                2,
                "2026-09-22T01:05:00+00:00",
                "detail fetch failed",
            ),
        )

    assert readiness._jd_coverage() == {
        "available": 1,
        "missing_not_cached": 1,
        "failed": 1,
    }


def test_consumer_readiness_reports_seek_and_linkedin_run_facts(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "latest_seek_market_run",
        lambda: {
            "status": "COMPLETE",
            "started_at": "2026-09-22T00:00:00+00:00",
            "finished_at": "2026-09-22T00:30:00+00:00",
        },
    )
    monkeypatch.setattr(
        readiness,
        "linkedin_campaign_progress",
        lambda: {
            "status": "PARTIAL_FAILURE",
            "started_at": "2026-09-22T02:00:00+00:00",
            "updated_at": "2026-09-22T02:20:00+00:00",
            "completed_at": "2026-09-22T02:20:00+00:00",
        },
    )
    monkeypatch.setattr(
        readiness,
        "_jd_coverage",
        lambda **_: {"available": 10, "missing_not_cached": 3, "failed": 1},
    )

    payload = readiness.consumer_readiness()

    assert payload["source_runs"]["seek"]["status"] == "COMPLETE"
    assert payload["source_runs"]["linkedin"]["status"] == "PARTIAL_FAILURE"
    assert payload["jd_coverage"] == {
        "available": 10,
        "missing_not_cached": 3,
        "failed": 1,
    }
    assert payload["jd_coverage_recent_3d"] == {
        "available": 10,
        "missing_not_cached": 3,
        "failed": 1,
    }


def test_readiness_api_exposes_consumer_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from fastapi.testclient import TestClient

    from api import main as api_main

    expected = {
        "source_runs": {
            "seek": {"status": "COMPLETE"},
            "linkedin": {"status": "COMPLETE"},
        },
        "jd_coverage": {"available": 5, "missing_not_cached": 2, "failed": 1},
    }
    monkeypatch.setattr(api_main, "consumer_readiness", lambda: expected)

    with TestClient(api_main.app) as client:
        response = client.get("/v3/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["api_version"] == "v3"
    assert payload["schema_version"] == api_main.SCHEMA_VERSION
    assert payload["source_runs"] == expected["source_runs"]
    assert payload["jd_coverage"] == expected["jd_coverage"]
