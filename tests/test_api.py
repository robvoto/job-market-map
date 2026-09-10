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
