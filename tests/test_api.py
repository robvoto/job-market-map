from fastapi.testclient import TestClient

from collector import db


def client_for_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from api.main import app

    return TestClient(app)


def insert_jobs(count=3):
    with db.connect() as conn:
        for i in range(1, count + 1):
            job_id = conn.execute(
                """INSERT INTO jobs(source,source_job_id,canonical_url,title,employer)
                   VALUES('seek',?,?,?,?)""",
                (str(i), f"https://seek.test/{i}", f"Role {i}", "Acme"),
            ).lastrowid
            conn.execute(
                """INSERT INTO job_observation_state(
                    job_id,first_seen_at,last_seen_at,capture_count,archived
                ) VALUES(?,?,?,1,0)""",
                (job_id, "2026-09-10T00:00:00+00:00", "2026-09-10T00:00:00+00:00"),
            )


def test_v3_feed_is_cursor_paginated_and_has_contract_metadata(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(3)
        first = client.get(
            "/v3/feed/jobs", params={"after_id": 0, "limit": 2, "include_raw": False}
        )
        assert first.status_code == 200
        payload = first.json()
        assert payload["api_version"] == "v3"
        assert payload["schema_version"] == 6
        assert len(payload["items"]) == 2
        assert payload["has_more"] is True
        assert "raw_card_text" not in payload["items"][0]
        second = client.get(
            "/v3/feed/jobs", params={"after_id": payload["next_cursor"], "limit": 2}
        )
        assert [item["title"] for item in second.json()["items"]] == ["Role 3"]


def test_admin_setting_api_returns_helper_text_and_updates_value(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        rows = client.get("/v3/admin/settings").json()["settings"]
        setting = next(
            row for row in rows if row["key"] == "retention.archive_after_days"
        )
        assert setting["help_text"]
        response = client.put(
            "/v3/admin/settings/retention.archive_after_days",
            json={"value": 40, "actor": "rob-test"},
        )
        assert response.status_code == 200
        assert response.json()["value"] == 40


def test_admin_query_toggle_changes_operational_state(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        created = client.post(
            "/v3/admin/queries",
            json={
                "source": "seek",
                "query_text": "admin only query",
                "location": "Sydney NSW",
            },
        )
        assert created.status_code == 200
        query_id = created.json()["id"]
        disabled = client.patch(f"/v3/admin/queries/{query_id}", json={"active": False})
        assert disabled.status_code == 200
        assert disabled.json()["active"] == 0


def test_neutral_feed_contains_no_user_activity_fields(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(1)
        item = client.get("/v3/feed/jobs", params={"limit": 1}).json()["items"][0]
        for forbidden in (
            "shown_to_rob",
            "shown",
            "seen",
            "reviewed",
            "applied",
            "rejected",
            "dismissed",
        ):
            assert forbidden not in item
        assert item["archived"] is False
        assert item["identity_key"].startswith("seek:id:")


def test_geography_admin_and_seek_coverage_api(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        geos = client.get("/v3/admin/geographies").json()["geographies"]
        assert {g["code"] for g in geos} == {"NSW", "ACT", "QLD"}
        disabled = client.patch(
            "/v3/admin/geographies/QLD", json={"enabled": False, "actor": "rob-test"}
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] == 0
        coverage = client.get("/v3/coverage/seek").json()
        assert coverage["partition_threshold"] == 450
        by_code = {g["geography_code"]: g for g in coverage["geographies"]}
        assert by_code["QLD"]["enabled"] is False
        assert by_code["NSW"]["status"] == "NOT_RUN"


def test_feed_can_filter_by_normalized_geography(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        with db.connect() as conn:
            for i, geo in enumerate(("NSW", "ACT", "QLD"), start=1):
                job_id = conn.execute(
                    """INSERT INTO jobs(source,source_job_id,canonical_url,title,employer,geography_code)
                       VALUES('seek',?,?,?,?,?)""",
                    (str(i), f"https://x/{i}", f"Role {i}", "Acme", geo),
                ).lastrowid
                conn.execute(
                    "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at) VALUES(?,?,?)",
                    (job_id, "2026-09-10", "2026-09-10"),
                )
        payload = client.get(
            "/v3/feed/jobs", params={"geography_code": "ACT", "limit": 10}
        ).json()
        assert [item["geography_code"] for item in payload["items"]] == ["ACT"]


def test_named_consumer_feed_uses_independent_api_checkpoint(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(3)
        first = client.get("/v3/consumers/job-hunter/feed", params={"limit": 2}).json()
        assert [x["id"] for x in first["items"]] == [1, 2]
        saved = client.post(
            "/v3/consumers/job-hunter/checkpoint",
            json={"last_job_id": first["next_cursor"]},
        )
        assert saved.status_code == 200
        second = client.get("/v3/consumers/job-hunter/feed", params={"limit": 2}).json()
        assert [x["id"] for x in second["items"]] == [3]
        planz = client.get("/v3/consumers/plan-z/feed", params={"limit": 2}).json()
        assert [x["id"] for x in planz["items"]] == [1, 2]


def test_v2_activity_contract_is_retired(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        assert client.get("/v2/feed/jobs").status_code == 404
        assert client.get("/v2/users/rob/jobs/1/activity").status_code == 404


def test_v3_stats_is_neutral_and_does_not_require_activity_tables(
    tmp_path, monkeypatch
):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(2)
        payload = client.get("/v3/stats")
        assert payload.status_code == 200
        body = payload.json()
        assert body["jobs"] == 2
        assert body["api_version"] == "v3"
        assert "user_activity_events" not in body
        assert "activity_users" not in body


def test_v3_job_jd_endpoint_returns_get_or_enrich_result(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(1)
        from api import main as api_main

        monkeypatch.setattr(
            api_main,
            "get_or_enrich_job_jd",
            lambda job_id: {
                "status": "cached",
                "id": job_id,
                "full_description": "Canonical JD",
                "jd_fetched_at": "2026-09-11T00:00:00+00:00",
                "jd_source": "seek_job_page",
            },
        )
        response = client.post("/v3/jobs/1/jd")

        assert response.status_code == 200
        assert response.json()["status"] == "cached"
        assert response.json()["full_description"] == "Canonical JD"


def test_admin_service_status_and_manual_run_contract(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        from api import main as api_main

        monkeypatch.setattr(
            api_main.PROCESS_MANAGER,
            "status",
            lambda: {"active": False, "pid": None, "latest_run": None, "lock": {"active": False}},
        )
        monkeypatch.setattr(
            api_main.SCHEDULER,
            "status",
            lambda: {"service_active": True, "enabled": True, "daily_time_local": "02:00"},
        )
        monkeypatch.setattr(api_main, "list_backups", lambda limit=20: [])
        monkeypatch.setattr(
            api_main.PROCESS_MANAGER,
            "start",
            lambda trigger: {"started": True, "pid": 123, "trigger": trigger},
        )

        status = client.get("/v3/admin/service/status")
        assert status.status_code == 200
        assert status.json()["scheduler"]["daily_time_local"] == "02:00"
        started = client.post("/v3/admin/collection/run")
        assert started.status_code == 200
        assert started.json() == {"started": True, "pid": 123, "trigger": "manual"}
