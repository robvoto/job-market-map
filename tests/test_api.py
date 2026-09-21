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
        assert payload["schema_version"] == 10
        assert payload["snapshot_max_id"] == 3
        assert len(payload["items"]) == 2
        assert payload["has_more"] is True
        assert "raw_card_text" not in payload["items"][0]
        second = client.get(
            "/v3/feed/jobs", params={"after_id": payload["next_cursor"], "limit": 2}
        )
        assert [item["title"] for item in second.json()["items"]] == ["Role 3"]


def test_v3_feed_fixed_high_water_excludes_jobs_arriving_mid_scan(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(2)
        first = client.get("/v3/feed/jobs", params={"after_id": 0, "limit": 1}).json()
        assert first["snapshot_max_id"] == 2
        assert [item["id"] for item in first["items"]] == [1]

        with db.connect() as conn:
            newest = conn.execute(
                """INSERT INTO jobs(source,source_job_id,canonical_url,title,employer)
                   VALUES('seek','3','https://seek.test/3','Role 3','Acme')"""
            ).lastrowid
            conn.execute(
                """INSERT INTO job_observation_state(
                    job_id,first_seen_at,last_seen_at,capture_count,archived
                ) VALUES(?,?,?,1,0)""",
                (newest, "2026-09-10T00:00:00+00:00", "2026-09-10T00:00:00+00:00"),
            )
        assert newest == 3

        second = client.get(
            "/v3/feed/jobs",
            params={
                "after_id": first["next_cursor"],
                "through_id": first["snapshot_max_id"],
                "limit": 10,
            },
        ).json()
        assert second["snapshot_max_id"] == 2
        assert [item["id"] for item in second["items"]] == [2]
        assert second["has_more"] is False


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


def test_feed_excludes_terminal_source_postings(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(2)
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET source_status='not_found' WHERE id=1")

        payload = client.get("/v3/feed/jobs", params={"limit": 10}).json()
        assert [item["id"] for item in payload["items"]] == [2]


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
            history_id = conn.execute(
                """
                INSERT INTO seek_coverage_history(
                    captured_at,geography_code,root_status,reported_results,
                    covered_unique_jobs,incomplete_partitions
                ) VALUES(?,?,?,?,?,?)
                """,
                ("2026-09-11T06:12:41+00:00", "NSW", "INCOMPLETE_CHILD_COVERAGE", 6958, 6905, 59),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO seek_coverage_diagnostics(
                    coverage_history_id,geography_code,hierarchy_label,url,status,
                    reported_results,collected_unique_jobs,last_error
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (history_id, "NSW", "NSW > ICT", "https://seek.test/nsw/ict", "FAILED", 100, 94, "page parse failed"),
            )
        coverage = client.get("/v3/coverage/seek").json()
        nsw = next(g for g in coverage["geographies"] if g["geography_code"] == "NSW")
        assert nsw["status"] == "NOT_RUN"
        assert nsw["has_current_cycle"] is False
        assert nsw["previous"] == {
            "history_id": history_id,
            "captured_at": "2026-09-11T06:12:41+00:00",
            "root_status": "INCOMPLETE_CHILD_COVERAGE",
            "reported_results": 6958,
            "covered_unique_jobs": 6905,
            "incomplete_partitions": 59,
            "diagnostics": [
                {
                    "hierarchy_label": "NSW > ICT",
                    "url": "https://seek.test/nsw/ict",
                    "status": "FAILED",
                    "reported_results": 100,
                    "collected_unique_jobs": 94,
                    "last_error": "page parse failed",
                }
            ],
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


def test_search_matches_filters_on_any_active_linked_source_and_returns_primary_once(
    tmp_path, monkeypatch
):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        with db.connect() as conn:
            primary_id = conn.execute(
                """INSERT INTO jobs(
                       source,source_job_id,canonical_url,title,employer,geography_code,posted_at
                   ) VALUES('seek','seek-1','https://seek.test/1','Old title','Acme','NSW',?)""",
                ("2026-09-01T00:00:00+00:00",),
            ).lastrowid
            linkedin_id = conn.execute(
                """INSERT INTO jobs(
                       source,source_job_id,canonical_url,title,employer,geography_code,posted_at,
                       primary_job_id
                   ) VALUES('linkedin','linkedin-1','https://linkedin.test/1',
                            'Business Analyst','Acme','ACT',?,?)""",
                ("2026-09-12T00:00:00+00:00", primary_id),
            ).lastrowid
            conn.execute(
                """INSERT INTO job_observation_state(
                       job_id,first_seen_at,last_seen_at,capture_count,archived
                   ) VALUES(?,?,?,1,1)""",
                (primary_id, "2026-09-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"),
            )
            conn.execute(
                """INSERT INTO job_observation_state(
                       job_id,first_seen_at,last_seen_at,capture_count,archived
                   ) VALUES(?,?,?,1,0)""",
                (linkedin_id, "2026-09-12T00:00:00+00:00", "2026-09-12T00:00:00+00:00"),
            )

        response = client.get(
            "/v3/jobs/search",
            params=[
                ("q", "Business Analyst"),
                ("source", "linkedin"),
                ("geography_code", "ACT"),
                ("posted_after", "2026-09-11T00:00:00+00:00"),
                ("limit", "10"),
            ],
        )

        assert response.status_code == 200
        payload = response.json()
        assert [item["id"] for item in payload["items"]] == [primary_id]
        assert payload["total"] == 1
        assert payload["has_more"] is False
        assert payload["items"][0]["source"] == "seek"

        second_page = client.get(
            "/v3/jobs/search",
            params=[
                ("q", "Business Analyst"),
                ("source", "linkedin"),
                ("geography_code", "ACT"),
                ("posted_after", "2026-09-11T00:00:00+00:00"),
                ("after_id", str(primary_id)),
                ("through_id", str(payload["snapshot_max_id"])),
                ("limit", "10"),
            ],
        )
        assert second_page.status_code == 200
        assert second_page.json()["items"] == []
        assert second_page.json()["total"] == 1


def test_search_is_bounded_and_supports_neutral_filters(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        with db.connect() as conn:
            for index, title in enumerate(("Delivery Manager", "Project Manager", "Analyst"), start=1):
                job_id = conn.execute(
                    """INSERT INTO jobs(
                           source,source_job_id,canonical_url,title,employer,location,
                           geography_code,classification_text,subclassification_text,
                           employment_type,workplace_type,apply_method,teaser_text
                       ) VALUES('seek',?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(index), f"https://seek.test/{index}", title, "Acme", "Sydney NSW", "NSW",
                     "Information & Communication Technology", "Project Management", "Full time",
                     "Hybrid", "quick_apply", f"Neutral teaser for {title}"),
                ).lastrowid
                conn.execute(
                    "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at,archived) VALUES(?,?,?,0)",
                    (job_id, "2026-09-10", "2026-09-10"),
                )

        first = client.get("/v3/jobs/search", params={"limit": 2}).json()
        assert first["total"] == 3
        assert len(first["items"]) == 2
        assert first["has_more"] is True

        filtered = client.get(
            "/v3/jobs/search",
            params={"classification": "Information & Communication Technology", "workplace_type": "Hybrid",
                    "company": "acme", "limit": 10},
        ).json()
        assert filtered["total"] == 3

        expression = client.get(
            "/v3/jobs/search",
            params={"q": 'title:"Delivery Manager" OR title:Analyst', "limit": 10},
        ).json()
        assert [item["title"] for item in expression["items"]] == ["Delivery Manager", "Analyst"]


def test_salary_search_requires_and_respects_period_and_currency(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        with db.connect() as conn:
            rows = (
                ("annual", "AUD", "year", 100000, 120000, "known"),
                ("hourly", "AUD", "hour", 100, 120, "known"),
                ("unknown-currency", None, "year", 100000, 120000, "known"),
                ("stale-state", "AUD", "year", 100000, 120000, "not_present"),
                ("unknown-state", "AUD", "year", 100000, 120000, "unknown"),
                ("not-applicable-state", "AUD", "year", 100000, 120000, "not_applicable"),
                ("unknown-period", "AUD", None, 100000, 120000, "known"),
            )
            for index, (title, currency, period, minimum, maximum, field_state) in enumerate(rows, start=1):
                job_id = conn.execute(
                    """INSERT INTO jobs(source,source_job_id,canonical_url,title,
                               salary_normalized_state,salary_min_amount,salary_max_amount,
                               salary_period,salary_currency)
                       VALUES('seek',?,?,?,?,?,?,?,?)""",
                    (str(index), f"https://seek.test/salary/{index}", title, "known", minimum, maximum, period, currency),
                ).lastrowid
                conn.execute(
                    "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at,archived) VALUES(?,?,?,0)",
                    (job_id, "2026-09-10", "2026-09-10"),
                )
                conn.execute(
                    "INSERT INTO job_field_states(job_id,field_name,state,checked_at) VALUES(?,?,?,?)",
                    (job_id, "salary", field_state, "2026-09-10"),
                )

        assert client.get("/v3/jobs/search", params={"salary_min": 100000}).status_code == 400
        assert client.get(
            "/v3/jobs/search",
            params={"salary_min": "nan", "salary_period": "year", "salary_currency": "AUD"},
        ).status_code == 422
        assert client.get(
            "/v3/jobs/search",
            params={"salary_min": 120000, "salary_max": 100000, "salary_period": "year", "salary_currency": "AUD"},
        ).status_code == 400
        assert client.get(
            "/v3/jobs/search",
            params={"salary_min": 100000, "salary_period": "fortnight", "salary_currency": "AUD"},
        ).status_code == 400
        assert client.get(
            "/v3/jobs/search",
            params={"salary_min": 100000, "salary_period": "year", "salary_currency": "money"},
        ).status_code == 400
        result = client.get(
            "/v3/jobs/search",
            params={"salary_min": 110000, "salary_max": 115000, "salary_period": "year", "salary_currency": "AUD", "limit": 10},
        )
        assert result.status_code == 200
        assert [item["title"] for item in result.json()["items"]] == ["annual"]

        upper_bound_only = client.get(
            "/v3/jobs/search",
            params={"salary_max": 105000, "salary_period": "year", "salary_currency": "AUD", "limit": 10},
        ).json()
        assert [item["title"] for item in upper_bound_only["items"]] == ["annual"]

        hourly_period = client.get(
            "/v3/jobs/search",
            params={"salary_min": 110, "salary_period": "hour", "salary_currency": "AUD", "limit": 10},
        ).json()
        assert [item["title"] for item in hourly_period["items"]] == ["hourly"]


def test_salary_search_linked_source_does_not_hide_matching_salary_evidence(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        with db.connect() as conn:
            primary_id = conn.execute(
                """INSERT INTO jobs(
                       source,source_job_id,canonical_url,title
                   ) VALUES('seek','seek-primary','https://seek.test/primary','Canonical role')"""
            ).lastrowid
            alias_id = conn.execute(
                """INSERT INTO jobs(
                       source,source_job_id,canonical_url,title,primary_job_id,
                       salary_normalized_state,salary_min_amount,salary_max_amount,
                       salary_period,salary_currency
                   ) VALUES('apsjobs','aps-alias','https://aps.test/alias','APS role',?,
                            'known',100000,120000,'year','AUD')""",
                (primary_id,),
            ).lastrowid
            for job_id in (primary_id, alias_id):
                conn.execute(
                    "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at,archived) VALUES(?,?,?,0)",
                    (job_id, "2026-09-10", "2026-09-10"),
                )
            conn.execute(
                "INSERT INTO job_field_states(job_id,field_name,state,checked_at) VALUES(?,?,?,?)",
                (alias_id, "salary", "known", "2026-09-10"),
            )

        response = client.get(
            "/v3/jobs/search",
            params={
                "salary_min": 110000,
                "salary_period": "year",
                "salary_currency": "AUD",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert [item["id"] for item in payload["items"]] == [primary_id]
        assert payload["items"][0]["salary_normalized"]["state"] == "unknown"
        assert payload["items"][0]["matched_sources"] == [
            {"id": alias_id, "source": "apsjobs", "source_job_id": "aps-alias"}
        ]


def test_salary_search_pagination_keeps_snapshot_and_filter(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        with db.connect() as conn:
            for index in range(1, 4):
                job_id = conn.execute(
                    """INSERT INTO jobs(
                           source,source_job_id,canonical_url,title,
                           salary_normalized_state,salary_min_amount,salary_max_amount,
                           salary_period,salary_currency
                       ) VALUES('seek',?,?,?,?,?,?,?,?)""",
                    (str(index), f"https://seek.test/{index}", f"Salary role {index}",
                     "known", 100000, 120000, "year", "AUD"),
                ).lastrowid
                conn.execute(
                    "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at,archived) VALUES(?,?,?,0)",
                    (job_id, "2026-09-10", "2026-09-10"),
                )
                conn.execute(
                    "INSERT INTO job_field_states(job_id,field_name,state,checked_at) VALUES(?,?,?,?)",
                    (job_id, "salary", "known", "2026-09-10"),
                )

        params = {"salary_min": 110000, "salary_period": "year", "salary_currency": "AUD", "limit": 2}
        first = client.get("/v3/jobs/search", params=params).json()
        assert first["total"] == 3
        assert first["has_more"] is True
        assert [item["title"] for item in first["items"]] == ["Salary role 1", "Salary role 2"]
        assert all(item["matched_sources"] for item in first["items"])

        second = client.get(
            "/v3/jobs/search",
            params={**params, "after_id": first["next_cursor"], "through_id": first["snapshot_max_id"]},
        ).json()
        assert second["total"] == 3
        assert second["has_more"] is False
        assert [item["title"] for item in second["items"]] == ["Salary role 3"]


def test_field_scoped_q_preserves_unknown_and_not_applicable_source_rows(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        with db.connect() as conn:
            states = (("seek", "known"), ("apsjobs", "unknown"), ("future-board", "not_applicable"), ("linkedin", "not_present"))
            for index, (source, state) in enumerate(states, start=1):
                job_id = conn.execute(
                    """INSERT INTO jobs(source,source_job_id,canonical_url,title,workplace_type)
                       VALUES(?,?,?,?,?)""",
                    (source, str(index), f"https://{source}.test/{index}", f"Role {index}", "Hybrid" if state in {"known", "not_present"} else None),
                ).lastrowid
                conn.execute(
                    "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at,archived) VALUES(?,?,?,0)",
                    (job_id, "2026-09-10", "2026-09-10"),
                )
                conn.execute(
                    "INSERT INTO job_field_states(job_id,field_name,state,checked_at) VALUES(?,?,?,?)",
                    (job_id, "workplace_type", state, "2026-09-10"),
                )

        structured = client.get(
            "/v3/jobs/search", params={"workplace_type": "Hybrid", "limit": 10}
        ).json()
        scoped = client.get(
            "/v3/jobs/search", params={"q": "workplace_type:Hybrid", "limit": 10}
        ).json()
        assert [item["title"] for item in structured["items"]] == ["Role 1", "Role 2", "Role 3"]
        assert [item["title"] for item in scoped["items"]] == ["Role 1", "Role 2", "Role 3"]


def test_not_present_state_overrides_a_stale_stored_value(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        result = ingest_card(
            CardObservation(
                source="seek", source_job_id="stale-1", canonical_url="https://seek.test/stale-1",
                title="Stale workplace state", workplace_type="Hybrid",
            )
        )
        ingest_card(
            CardObservation(
                source="seek", source_job_id="stale-1", canonical_url="https://seek.test/stale-1",
                title="Stale workplace state", field_states={"workplace_type": "not_present"},
            )
        )
        with db.connect() as conn:
            stored = conn.execute(
                "SELECT workplace_type FROM jobs WHERE id=?", (result.observation_job_id,)
            ).fetchone()["workplace_type"]
        assert stored == "Hybrid"
        assert db.get_job_field_states(result.observation_job_id)["workplace_type"] == "not_present"

        structured = client.get(
            "/v3/jobs/search", params={"workplace_type": "Hybrid", "limit": 10}
        ).json()
        scoped = client.get(
            "/v3/jobs/search", params={"q": "workplace_type:Hybrid", "limit": 10}
        ).json()
        assert structured["items"] == []
        assert scoped["items"] == []


def test_search_rejects_pathological_query_and_limit(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        too_many_terms = " OR ".join(f"term{i}" for i in range(17))
        assert client.get("/v3/jobs/search", params={"q": too_many_terms}).status_code == 400
        assert client.get("/v3/jobs/search", params={"limit": 5001}).status_code == 400



def test_feed_derives_vacancy_freshness_across_linked_sources(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        with db.connect() as conn:
            primary_id = conn.execute(
                """INSERT INTO jobs(source,source_job_id,canonical_url,title,employer,geography_code)
                   VALUES('seek','seek-1','https://seek.test/1','Analyst','Acme','NSW')"""
            ).lastrowid
            alias_id = conn.execute(
                """INSERT INTO jobs(source,source_job_id,canonical_url,title,employer,geography_code,primary_job_id)
                   VALUES('linkedin','li-1','https://linkedin.test/1','Analyst','Acme','NSW',?)""",
                (primary_id,),
            ).lastrowid
            conn.execute(
                "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at,archived) VALUES(?,?,?,1)",
                (primary_id, "2026-09-10T00:00:00+00:00", "2026-09-10T01:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO job_observation_state(job_id,first_seen_at,last_seen_at,archived) VALUES(?,?,?,0)",
                (alias_id, "2026-09-12T00:00:00+00:00", "2026-09-12T02:00:00+00:00"),
            )
            conn.execute(
                """INSERT INTO same_vacancy_links(job_id,primary_job_id,confidence,match_type,matching_signals_json,detected_at)
                   VALUES(?,?,0.96,'cross_source_locality','[]','2026-09-12T02:00:00+00:00')""",
                (alias_id, primary_id),
            )

        payload = client.get("/v3/feed/jobs", params={"limit": 10}).json()
        assert [item["id"] for item in payload["items"]] == [primary_id]
        item = payload["items"][0]
        assert item["source"] == "seek"
        assert item["last_seen_at"] == "2026-09-10T01:00:00+00:00"
        assert item["archived"] is True
        assert item["vacancy_last_seen_at"] == "2026-09-12T02:00:00+00:00"
        assert item["vacancy_archived"] is False


def test_named_consumer_feed_end_to_end_keeps_one_run_high_water_across_checkpoints(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(2)
        first = client.get(
            "/v3/consumers/job-hunter/feed", params={"limit": 1}
        ).json()
        assert first["snapshot_max_id"] == 2
        assert [item["id"] for item in first["items"]] == [1]

        with db.connect() as conn:
            newest = conn.execute(
                """INSERT INTO jobs(source,source_job_id,canonical_url,title,employer)
                   VALUES('seek','3','https://seek.test/3','Role 3','Acme')"""
            ).lastrowid
            conn.execute(
                """INSERT INTO job_observation_state(
                    job_id,first_seen_at,last_seen_at,capture_count,archived
                ) VALUES(?,?,?,1,0)""",
                (newest, "2026-09-10T00:00:00+00:00", "2026-09-10T00:00:00+00:00"),
            )
        assert newest == 3

        saved = client.post(
            "/v3/consumers/job-hunter/checkpoint",
            json={"last_job_id": first["next_cursor"]},
        )
        assert saved.status_code == 200

        second = client.get(
            "/v3/consumers/job-hunter/feed",
            params={"limit": 10, "through_id": first["snapshot_max_id"]},
        ).json()
        assert second["snapshot_max_id"] == 2
        assert [item["id"] for item in second["items"]] == [2]
        assert second["has_more"] is False

        client.post(
            "/v3/consumers/job-hunter/checkpoint",
            json={"last_job_id": second["next_cursor"]},
        )
        next_run = client.get(
            "/v3/consumers/job-hunter/feed", params={"limit": 10}
        ).json()
        assert next_run["snapshot_max_id"] == 3
        assert [item["id"] for item in next_run["items"]] == [3]


def test_consumer_state_reports_exact_pending_active_primary_snapshot(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(6)
        with db.connect() as conn:
            conn.execute("UPDATE job_observation_state SET archived=1 WHERE job_id=3")
            conn.execute("UPDATE jobs SET source_status='not_found' WHERE id=4")
            conn.execute("UPDATE jobs SET primary_job_id=2 WHERE id=5")

        saved = client.post(
            "/v3/consumers/job-hunter/checkpoint",
            json={"last_job_id": 1},
        )
        assert saved.status_code == 200

        state = client.get("/v3/consumers/job-hunter/state").json()
        assert state["last_job_id"] == 1
        assert state["snapshot_max_id"] == 6
        assert state["pending_active_primary_count"] == 2

        feed = client.get(
            "/v3/consumers/job-hunter/feed",
            params={"through_id": state["snapshot_max_id"], "limit": 10},
        ).json()
        assert [item["id"] for item in feed["items"]] == [2, 6]
        assert len(feed["items"]) == state["pending_active_primary_count"]


def test_consumer_state_snapshot_excludes_new_arrivals_from_that_run(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(2)
        state = client.get("/v3/consumers/job-hunter/state").json()
        assert state["snapshot_max_id"] == 2
        assert state["pending_active_primary_count"] == 2

        with db.connect() as conn:
            newest = conn.execute(
                """INSERT INTO jobs(source,source_job_id,canonical_url,title,employer)
                   VALUES('seek','3','https://seek.test/3','Role 3','Acme')"""
            ).lastrowid
            conn.execute(
                """INSERT INTO job_observation_state(
                    job_id,first_seen_at,last_seen_at,capture_count,archived
                ) VALUES(?,?,?,1,0)""",
                (newest, "2026-09-10T00:00:00+00:00", "2026-09-10T00:00:00+00:00"),
            )
        assert newest == 3
        feed = client.get(
            "/v3/consumers/job-hunter/feed",
            params={"through_id": state["snapshot_max_id"], "limit": 10},
        ).json()
        assert feed["snapshot_max_id"] == 2
        assert [item["id"] for item in feed["items"]] == [1, 2]


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


def test_v3_job_jd_returns_gone_when_all_sources_are_terminal(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(1)
        from api import main as api_main
        from collector.jd_enrichment import JDSourceUnavailableError

        def unavailable(_job_id):
            raise JDSourceUnavailableError("all linked JD sources are unavailable")

        monkeypatch.setattr(api_main, "get_or_enrich_job_jd", unavailable)
        response = client.post("/v3/jobs/1/jd")

        assert response.status_code == 410
        assert response.json()["detail"] == "all linked JD sources are unavailable"


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
        monkeypatch.setattr(
            api_main.PROCESS_MANAGER,
            "start_linkedin",
            lambda hours_old, cycle_key: {
                "started": True,
                "pid": 124,
                "trigger": "linkedin-scheduled",
                "hours_old": hours_old,
                "cycle_key": cycle_key,
            },
        )
        monkeypatch.setattr(
            api_main.SchedulerService,
            "linkedin_cycle_key",
            staticmethod(lambda: "linkedin:test-slot"),
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

        linkedin = client.post("/v3/admin/linkedin/run")
        assert linkedin.status_code == 200
        assert linkedin.json() == {
            "started": True,
            "pid": 124,
            "trigger": "linkedin-scheduled",
            "hours_old": 5,
            "cycle_key": "linkedin:test-slot",
        }


def test_admin_source_scheduler_controls_are_independent(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        from api import main as api_main
        from collector.settings import get_setting

        monkeypatch.setattr(api_main.SCHEDULER, "status", dict)
        assert get_setting("scheduler.enabled") is True
        assert get_setting("scheduler.seek_enabled") is True
        assert get_setting("scheduler.linkedin_enabled") is True

        assert client.post("/v3/admin/scheduler/seek/pause").status_code == 200
        assert get_setting("scheduler.seek_enabled") is False
        assert get_setting("scheduler.linkedin_enabled") is True
        assert get_setting("scheduler.enabled") is True

        assert client.post("/v3/admin/scheduler/linkedin/pause").status_code == 200
        assert get_setting("scheduler.seek_enabled") is False
        assert get_setting("scheduler.linkedin_enabled") is False

        assert client.post("/v3/admin/scheduler/seek/resume").status_code == 200
        assert get_setting("scheduler.seek_enabled") is True
        assert get_setting("scheduler.linkedin_enabled") is False

        assert client.post("/v3/admin/scheduler/linkedin/resume").status_code == 200
        assert get_setting("scheduler.linkedin_enabled") is True


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
        monkeypatch.setattr(api_main, "latest_seek_market_run", lambda: latest)
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
            "latest_seek": latest,
            "linkedin_campaign": linkedin_campaign,
        }


def test_v3_cached_job_jd_get_never_enriches_and_returns_cached_value(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(1)
        from api import main as api_main
        from collector import db

        db.store_job_jd_once(
            1,
            full_description="Cached canonical JD",
            jd_fetched_at="2026-09-17T13:00:00+00:00",
            jd_source="seek_job_page",
        )
        monkeypatch.setattr(
            api_main,
            "get_or_enrich_job_jd",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("GET must not enrich")),
        )

        response = client.get("/v3/jobs/1/jd")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "cached"
        assert payload["full_description"] == "Cached canonical JD"
        assert payload["jd_source"] == "seek_job_page"


def test_v3_cached_job_jd_get_returns_conflict_when_not_cached(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        insert_jobs(1)
        from api import main as api_main

        monkeypatch.setattr(
            api_main,
            "get_or_enrich_job_jd",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("GET must not enrich")),
        )
        response = client.get("/v3/jobs/1/jd")

        assert response.status_code == 409
        assert response.json()["detail"] == "JD not cached yet"


def test_v3_cached_job_jd_get_returns_gone_for_tombstoned_identity(tmp_path, monkeypatch):
    with client_for_tmp_db(tmp_path, monkeypatch) as client:
        from collector import db

        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO job_tombstones(
                    source,source_job_id,identity_key,canonical_url,title,
                    first_seen_at,last_seen_at,removed_at
                ) VALUES('linkedin','li-123','linkedin:id:li-123',
                         'https://www.linkedin.com/jobs/view/123','Role',
                         '2026-09-17T12:00:00+00:00','2026-09-17T12:30:00+00:00',
                         '2026-09-17T13:00:00+00:00')
                """
            )
        response = client.get(
            "/v3/jobs/999/jd", params={"identity_key": "linkedin:id:li-123"}
        )

        assert response.status_code == 410
        assert response.json()["detail"] == "job was terminal-retired"
