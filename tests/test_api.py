from fastapi.testclient import TestClient

from collector import db


def client_for_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from api.main import app

    return TestClient(app)


def insert_jobs(count=3):
    with db.connect() as conn:
        for i in range(1, count + 1):
            conn.execute(
                """INSERT INTO jobs(source,source_job_id,canonical_url,title,employer,first_seen_at,last_seen_at)
                   VALUES('seek',?,?,?,?,'2026-09-10T00:00:00+00:00','2026-09-10T00:00:00+00:00')""",
                (str(i), f"https://seek.test/{i}", f"Role {i}", "Acme"),
            )


def test_v1_feed_is_cursor_paginated_and_has_contract_metadata(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(3)
        first = client.get(
            "/v1/feed/jobs", params={"after_id": 0, "limit": 2, "include_raw": False}
        )
        assert first.status_code == 200
        payload = first.json()
        assert payload["api_version"] == "v1"
        assert payload["schema_version"] == 1
        assert len(payload["items"]) == 2
        assert payload["has_more"] is True
        assert "raw_card_text" not in payload["items"][0]
        second = client.get(
            "/v1/feed/jobs", params={"after_id": payload["next_cursor"], "limit": 2}
        )
        assert [item["title"] for item in second.json()["items"]] == ["Role 3"]


def test_admin_setting_api_returns_helper_text_and_updates_value(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        rows = client.get("/v1/admin/settings").json()["settings"]
        setting = next(
            row for row in rows if row["key"] == "retention.archive_after_days"
        )
        assert setting["help_text"]
        response = client.put(
            "/v1/admin/settings/retention.archive_after_days",
            json={"value": 40, "actor": "rob-test"},
        )
        assert response.status_code == 200
        assert response.json()["value"] == 40


def test_admin_query_toggle_changes_operational_state(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        created = client.post(
            "/v1/admin/queries",
            json={
                "source": "seek",
                "query_text": "admin only query",
                "location": "Sydney NSW",
            },
        )
        assert created.status_code == 200
        query_id = created.json()["id"]
        disabled = client.patch(f"/v1/admin/queries/{query_id}", json={"active": False})
        assert disabled.status_code == 200
        assert disabled.json()["active"] == 0


def test_status_api_retry_is_idempotent(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(1)
        job_id = client.get("/v1/feed/jobs", params={"limit": 1}).json()["items"][0][
            "id"
        ]
        body = {
            "field": "shown_to_rob",
            "value": True,
            "actor": "job-hunter",
            "idempotency_key": "show-job-1",
        }
        first = client.post(f"/v1/jobs/{job_id}/status", json=body).json()
        second = client.post(f"/v1/jobs/{job_id}/status", json=body).json()
        assert first["changed"] is True
        assert second["changed"] is False
        assert first["event_id"] == second["event_id"]


def test_feed_status_fields_are_json_booleans(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(1)
        item = client.get("/v1/feed/jobs", params={"limit": 1}).json()["items"][0]
        assert item["shown_to_rob"] is False
        assert item["archived"] is False


def test_geography_admin_and_seek_coverage_api(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        geos = client.get("/v1/admin/geographies").json()["geographies"]
        assert {g["code"] for g in geos} == {"NSW", "ACT", "QLD"}
        disabled = client.patch(
            "/v1/admin/geographies/QLD", json={"enabled": False, "actor": "rob-test"}
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] == 0
        coverage = client.get("/v1/coverage/seek").json()
        assert coverage["partition_threshold"] == 450
        by_code = {g["geography_code"]: g for g in coverage["geographies"]}
        assert by_code["QLD"]["enabled"] is False
        assert by_code["NSW"]["status"] == "NOT_RUN"


def test_feed_can_filter_by_normalized_geography(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        with db.connect() as conn:
            for i, geo in enumerate(("NSW", "ACT", "QLD"), start=1):
                conn.execute(
                    """INSERT INTO jobs(source,source_job_id,canonical_url,title,employer,geography_code,first_seen_at,last_seen_at) VALUES('seek',?,?,?,?,?,'2026-09-10','2026-09-10')""",
                    (str(i), f"https://x/{i}", f"Role {i}", "Acme", geo),
                )
        payload = client.get(
            "/v1/feed/jobs", params={"geography_code": "ACT", "limit": 10}
        ).json()
        assert [item["geography_code"] for item in payload["items"]] == ["ACT"]


def test_named_consumer_feed_uses_independent_api_checkpoint(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(3)
        first = client.get("/v1/consumers/job-hunter/feed", params={"limit": 2}).json()
        assert [x["id"] for x in first["items"]] == [1, 2]
        saved = client.post(
            "/v1/consumers/job-hunter/checkpoint",
            json={"last_job_id": first["next_cursor"]},
        )
        assert saved.status_code == 200
        second = client.get("/v1/consumers/job-hunter/feed", params={"limit": 2}).json()
        assert [x["id"] for x in second["items"]] == [3]
        planz = client.get("/v1/consumers/plan-z/feed", params={"limit": 2}).json()
        assert [x["id"] for x in planz["items"]] == [1, 2]
