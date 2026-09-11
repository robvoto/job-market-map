from fastapi.testclient import TestClient

from collector import db
from collector.ingest import ingest_card
from collector.models import CardObservation


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
        assert payload["schema_version"] == 8
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
                "source": "linkedin",
                "query_text": "admin only query",
                "location": "New South Wales, Australia",
            },
        )
        assert created.status_code == 200
        query_id = created.json()["id"]
        disabled = client.patch(f"/v3/admin/queries/{query_id}", json={"active": False})
        assert disabled.status_code == 200
        assert disabled.json()["active"] == 0


def test_admin_rejects_retired_seek_keyword_query(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        response = client.post(
            "/v3/admin/queries",
            json={
                "source": "seek",
                "query_text": "business analyst",
                "location": "Sydney NSW",
            },
        )
        assert response.status_code == 400
        assert "whole-state coverage" in response.json()["detail"]


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
        assert by_code["NSW"]["has_current_cycle"] is False
        assert by_code["NSW"]["previous"] is None


def test_seek_coverage_exposes_previous_archived_cycle(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO seek_coverage_history(
                    captured_at,geography_code,root_status,reported_results,
                    covered_unique_jobs,incomplete_partitions
                ) VALUES(?,?,?,?,?,?)
                """,
                ("2026-09-11T06:12:41+00:00", "NSW", "INCOMPLETE_CHILD_COVERAGE", 6958, 6905, 59),
            )
        coverage = client.get("/v3/coverage/seek").json()
        nsw = next(g for g in coverage["geographies"] if g["geography_code"] == "NSW")
        assert nsw["status"] == "NOT_RUN"
        assert nsw["has_current_cycle"] is False
        assert nsw["previous"] == {
            "captured_at": "2026-09-11T06:12:41+00:00",
            "root_status": "INCOMPLETE_CHILD_COVERAGE",
            "reported_results": 6958,
            "covered_unique_jobs": 6905,
            "incomplete_partitions": 59,
        }


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


def test_lookup_resolves_exact_source_and_source_job_id(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(3)
        response = client.get(
            "/v3/jobs/lookup", params={"source": "seek", "source_job_id": "2"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["job"]["id"] == 2
        assert payload["job"]["source"] == "seek"
        assert payload["job"]["source_job_id"] == "2"


def test_lookup_resolves_exact_identity_key(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(1)
        identity_key = client.get("/v3/jobs/1").json()["job"]["identity_key"]
        response = client.get("/v3/jobs/lookup", params={"identity_key": identity_key})
        assert response.status_code == 200
        assert response.json()["job"]["id"] == 1


def test_lookup_unknown_identity_returns_404_not_search_fallback(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(1)
        response = client.get(
            "/v3/jobs/lookup", params={"source": "seek", "source_job_id": "999"}
        )
        assert response.status_code == 404


def test_lookup_rejects_malformed_and_conflicting_inputs(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(1)
        identity_key = client.get("/v3/jobs/1").json()["job"]["identity_key"]
        # neither identity_key nor source pair supplied
        assert client.get("/v3/jobs/lookup").status_code == 400
        # only one half of the source pair supplied
        assert (
            client.get("/v3/jobs/lookup", params={"source": "seek"}).status_code == 400
        )
        assert (
            client.get(
                "/v3/jobs/lookup", params={"source_job_id": "1"}
            ).status_code
            == 400
        )
        # blank values are malformed rather than unknown identities
        assert client.get("/v3/jobs/lookup", params={"identity_key": "   "}).status_code == 400
        assert (
            client.get(
                "/v3/jobs/lookup", params={"source": "   ", "source_job_id": "1"}
            ).status_code
            == 400
        )
        assert (
            client.get(
                "/v3/jobs/lookup", params={"source": "seek", "source_job_id": "   "}
            ).status_code
            == 400
        )
        # identity_key combined with a conflicting source pair
        assert (
            client.get(
                "/v3/jobs/lookup",
                params={
                    "identity_key": identity_key,
                    "source": "seek",
                    "source_job_id": "1",
                },
            ).status_code
            == 400
        )


def test_lookup_keeps_duplicate_linked_source_jobs_independently_resolvable(
    tmp_path, monkeypatch
):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(2)
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO duplicate_links(job_id_a,job_id_b,confidence,match_type,reasons_json,detected_at)
                   VALUES(1,2,0.95,'title_employer','[]','2026-09-11T00:00:00+00:00')"""
            )
        first = client.get(
            "/v3/jobs/lookup", params={"source": "seek", "source_job_id": "1"}
        ).json()
        second = client.get(
            "/v3/jobs/lookup", params={"source": "seek", "source_job_id": "2"}
        ).json()
        assert first["job"]["id"] == 1
        assert second["job"]["id"] == 2
        assert [d["other_job_id"] for d in first["duplicates"]] == [2]
        assert [d["other_job_id"] for d in second["duplicates"]] == [1]


def test_same_vacancy_alias_is_lookupable_but_not_in_downstream_feed(
    tmp_path, monkeypatch
):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        fields = {
            "title": "Implementation Consultant",
            "employer": "Example Co",
            "location": "Sydney NSW",
            "salary_text": "$120k + super",
            "employment_type": "Full time",
            "workplace_type": "Hybrid",
            "classification_text": "Information & Communication Technology",
            "subclassification_text": "Consultants",
            "teaser_text": "Lead enterprise onboarding and API integration.",
        }
        primary = ingest_card(
            CardObservation(
                source="seek",
                source_job_id="123",
                canonical_url="https://seek.test/jobs/123",
                **fields,
            )
        )
        alias = ingest_card(
            CardObservation(
                source="linkedin",
                source_job_id="456",
                canonical_url="https://linkedin.test/jobs/456",
                **fields,
            )
        )

        feed = client.get("/v3/feed/jobs", params={"limit": 10}).json()
        assert [item["id"] for item in feed["items"]] == [primary.job_id]

        lookup = client.get(
            "/v3/jobs/lookup", params={"source": "linkedin", "source_job_id": "456"}
        )
        assert lookup.status_code == 200
        payload = lookup.json()
        assert payload["job"]["id"] == alias.observation_job_id
        assert payload["job"]["primary_job_id"] == primary.job_id
        assert payload["same_vacancy"][0]["primary_job_id"] == primary.job_id
        assert payload["same_vacancy"][0]["matching_signals"]


def test_lookup_never_resolves_by_similar_title_or_employer_text(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(1)
        response = client.get(
            "/v3/jobs/lookup", params={"source": "seek", "source_job_id": "Role 1"}
        )
        assert response.status_code == 404


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
        monkeypatch.setattr(api_main, "persistent_browser_ready", lambda: True)
        monkeypatch.setattr(
            api_main.PROCESS_MANAGER,
            "start",
            lambda trigger: {"started": True, "pid": 123, "trigger": trigger},
        )

        status = client.get("/v3/admin/service/status")
        assert status.status_code == 200
        assert status.json()["scheduler"]["daily_time_local"] == "02:00"
        assert status.json()["browser"] == {"active": True}
        assert status.json()["log_url"] == "/v3/admin/log"
        assert status.json()["backup_directory"].endswith("/backups")
        started = client.post("/v3/admin/collection/run")
        assert started.status_code == 200
        assert started.json() == {"started": True, "pid": 123, "trigger": "manual"}


def test_admin_log_returns_durable_collection_log_tail(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        from api import main as api_main

        monkeypatch.setattr(api_main, "read_collection_log_tail", lambda lines: f"tail:{lines}\n")
        response = client.get("/v3/admin/log?lines=25")
        assert response.status_code == 200
        assert response.text == "tail:25\n"
        assert response.headers["content-type"].startswith("text/plain")


def test_admin_seek_browser_focuses_persistent_seek_tab(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        from api import main as api_main

        monkeypatch.setattr(
            api_main,
            "focus_or_open_tab",
            lambda url, host_suffix: type(
                "Response",
                (),
                {"result": {"ok": True, "url": url, "reused": True}},
            )(),
        )
        response = client.post("/v3/admin/browser/seek")
        assert response.status_code == 200
        assert response.json()["reused"] is True
        assert response.json()["url"] == "https://au.seek.com/"


def test_admin_stats_exposes_current_bootstrap_and_latest(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        from api import main as api_main

        monkeypatch.setattr(api_main, "enabled_state_codes", lambda: ["NSW"])
        monkeypatch.setattr(
            api_main,
            "population_stats",
            lambda _codes: {
                "jobs": 200,
                "source_jobs": {"seek": 180, "linkedin": 20},
                "jd_markers": 100,
            },
        )
        bootstrap = {"id": 24, "run_kind": "bootstrap", "stats": {"jobs_total": 190}}
        latest = {"id": 25, "run_kind": "normal", "stats": {"jobs_total": 200}}
        monkeypatch.setattr(api_main, "bootstrap_market_run", lambda: bootstrap)
        monkeypatch.setattr(api_main, "latest_market_run", lambda: latest)
        linkedin_campaign = {
            "status": "PARTIAL",
            "cycle_key": "2026-09-11",
            "geographies_total": 3,
            "geographies_complete": 1,
            "geographies_capped": 0,
            "geographies_remaining": 2,
        }
        monkeypatch.setattr(
            api_main, "linkedin_campaign_progress", lambda: linkedin_campaign
        )

        response = client.get("/v3/admin/stats")
        assert response.status_code == 200
        assert response.json() == {
            "current": {
                "jobs": 200,
                "source_jobs": {"seek": 180, "linkedin": 20},
                "jd_markers": 100,
            },
            "bootstrap": bootstrap,
            "latest": latest,
            "linkedin_campaign": linkedin_campaign,
        }
