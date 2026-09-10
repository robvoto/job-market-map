import json
import sqlite3
from pathlib import Path

import pytest

from collector import db
from collector.bootstrap_jh import (
    BOOTSTRAP_ORIGIN,
    apply_bootstrap,
    plan_bootstrap,
    require_matching_dry_run,
    write_report,
)


def _create_jh_db(path: Path, rows: list[tuple]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE job_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                job_key TEXT NOT NULL,
                source TEXT NOT NULL,
                platform_id TEXT NOT NULL,
                title TEXT,
                company TEXT,
                state TEXT NOT NULL DEFAULT 'seen',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                data TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO job_history(
                user_id,job_key,source,platform_id,title,company,first_seen,last_seen,data
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )


def _payload(job_key: str, *, url: str, title: str = "Business Analyst", jd: str | None = None):
    snapshot = {
        "job_key": job_key,
        "source": job_key.split(":", 1)[0],
        "title": "PERSONAL SNAPSHOT TITLE MUST NOT IMPORT",
        "company": "PERSONAL SNAPSHOT COMPANY MUST NOT IMPORT",
        "url": "https://example.invalid/personal-snapshot",
        "location": "PERSONAL SNAPSHOT LOCATION MUST NOT IMPORT",
        "salary": "PERSONAL SNAPSHOT SALARY MUST NOT IMPORT",
        "work_type": "PERSONAL SNAPSHOT WORK TYPE MUST NOT IMPORT",
        "work_mode": "PERSONAL SNAPSHOT WORK MODE MUST NOT IMPORT",
        "posted": "PERSONAL SNAPSHOT POSTED MUST NOT IMPORT",
        "teaser": "PERSONAL SNAPSHOT TEASER MUST NOT IMPORT",
        "fit_score": 99,
        "decision": "KEEP",
        "is_hidden": True,
    }
    if jd:
        snapshot["full_description"] = "PERSONAL SNAPSHOT JD MUST NOT IMPORT"
        snapshot["description_source"] = "seek_detail"
    return {
        "job_key": job_key,
        "source": job_key.split(":", 1)[0],
        "title": title,
        "company": "Acme",
        "url": url,
        "location": "UNVERIFIED HISTORY LOCATION MUST NOT IMPORT",
        "salary": "UNVERIFIED HISTORY SALARY MUST NOT IMPORT",
        "work_type": "UNVERIFIED HISTORY WORK TYPE MUST NOT IMPORT",
        "work_mode": "UNVERIFIED HISTORY WORK MODE MUST NOT IMPORT",
        "posted": "UNVERIFIED HISTORY POSTED MUST NOT IMPORT",
        "original_posted_date": "2026-09-08",
        "original_posted_date_status": "verified",
        "teaser": "UNVERIFIED HISTORY TEASER MUST NOT IMPORT",
        "full_description": "UNVERIFIED HISTORY JD MUST NOT IMPORT",
        "first_seen_at": "2026-09-08T01:00:00+00:00",
        "last_seen_at": "2026-09-10T01:00:00+00:00",
        "times_viewed": 12,
        "is_liked": True,
        "last_kept_at": "2026-09-10T01:00:00+00:00",
        "last_kept_snapshot": snapshot,
        "detail_evidence": {
            "details_text": jd or "",
            "fetched_at": "2026-09-09T12:00:00+00:00",
            "description_source": "seek_detail",
            "source_metadata": {
                "schema_version": 1,
                "platform": job_key.split(":", 1)[0],
                "platform_job_id": job_key.split(":", 1)[1],
                "canonical_url": url,
            },
        },
    }


def test_dry_run_is_read_only_and_reports_invalid_unmapped_and_unique_jobs(tmp_path):
    jh_db = tmp_path / "jh.db"
    valid = json.dumps(_payload("seek:123", url="https://www.seek.com.au/job/123", jd="Full JD"))
    duplicate = json.dumps(_payload("seek:123", url="https://www.seek.com.au/job/123", jd="Full JD"))
    invalid = json.dumps({"job_key": "seek:999", "source": "seek", "url": "not-a-url"})
    _create_jh_db(
        jh_db,
        [
            ("u1", "seek:123", "seek", "123", "BA", "Acme", "2026-09-08", "2026-09-10", valid),
            ("u2", "seek:123", "seek", "123", "BA", "Acme", "2026-09-08", "2026-09-10", duplicate),
            ("u1", "seek:999", "seek", "999", "Bad", "Nope", "2026-09-08", "2026-09-10", invalid),
            ("u1", "seek:888", "seek", "888", "No payload", "Nope", "2026-09-08", "2026-09-10", None),
        ],
    )
    jmm_db = tmp_path / "market.db"

    plan = plan_bootstrap(jh_db, jmm_db_path=jmm_db)

    assert plan.report.records_checked == 4
    assert plan.report.valid_source_records == 2
    assert plan.report.valid_importable_jobs == 1
    assert plan.report.skipped_invalid_records == 2
    assert plan.report.unmapped_records == 1
    assert plan.report.jobs_with_jds == 1
    assert plan.report.jobs_without_jds == 0
    assert plan.report.existing_jmm_jobs == 0
    assert plan.report.new_jmm_jobs == 1
    assert not jmm_db.exists()


def test_apply_imports_only_neutral_fields_copies_jd_and_is_idempotent(tmp_path, monkeypatch):
    jh_db = tmp_path / "jh.db"
    source_jd = "\nFull neutral JD\n\nSecond paragraph keeps source formatting.\n"
    payload = _payload("seek:123", url="https://www.seek.com.au/job/123?utm_source=x", jd=source_jd)
    _create_jh_db(
        jh_db,
        [
            (
                "private-user-id",
                "seek:123",
                "seek",
                "123",
                "Business Analyst",
                "Acme",
                "2026-09-08 01:00:00",
                "2026-09-10 01:00:00",
                json.dumps(payload),
            )
        ],
    )
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")

    plan = plan_bootstrap(jh_db, jmm_db_path=db.DB_PATH)
    first = apply_bootstrap(plan)
    second = apply_bootstrap(plan_bootstrap(jh_db, jmm_db_path=db.DB_PATH))

    assert first.jobs_created == 1
    assert first.jds_stored == 1
    assert second.jobs_already_imported == 1
    with db.connect() as conn:
        jobs = conn.execute("SELECT * FROM jobs").fetchall()
        captures = conn.execute("SELECT raw_json FROM card_captures").fetchall()
    assert len(jobs) == 1
    assert len(captures) == 1
    job = dict(jobs[0])
    assert job["source_job_id"] == "123"
    assert job["canonical_url"] == "https://www.seek.com.au/job/123"
    assert job["title"] == "Business Analyst"
    assert job["employer"] == "Acme"
    assert job["location"] is None
    assert job["salary_text"] is None
    assert job["employment_type"] is None
    assert job["workplace_type"] is None
    assert job["posted_text"] is None
    assert job["posted_at"] is None
    assert job["teaser_text"] is None
    assert job["full_description"] == source_jd
    assert job["jd_fetched_at"] == "2026-09-09T12:00:00+00:00"
    assert job["jd_source"] == "job_hunter_detail_evidence:seek_detail"
    capture = json.loads(captures[0][0])
    assert capture["origin"] == BOOTSTRAP_ORIGIN
    assert "private-user-id" not in captures[0][0]
    assert "fit_score" not in captures[0][0]
    assert "is_liked" not in captures[0][0]
    assert "PERSONAL SNAPSHOT" not in str(job)


def test_jd_without_neutral_fetch_timestamp_is_not_imported(tmp_path, monkeypatch):
    payload = _payload("seek:123", url="https://www.seek.com.au/job/123", jd="Full neutral JD")
    payload["detail_evidence"].pop("fetched_at")
    jh_db = tmp_path / "jh.db"
    _create_jh_db(
        jh_db,
        [("u1", "seek:123", "seek", "123", "BA", "Acme", "2026-09-08", "2026-09-10", json.dumps(payload))],
    )
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")

    report = apply_bootstrap(plan_bootstrap(jh_db, jmm_db_path=db.DB_PATH))

    assert report.jobs_created == 1
    assert report.jds_stored == 0
    with db.connect() as conn:
        job = conn.execute("SELECT full_description,jd_fetched_at,jd_source FROM jobs").fetchone()
    assert dict(job) == {"full_description": None, "jd_fetched_at": None, "jd_source": None}


def test_jd_without_source_backed_detail_provenance_is_not_imported(tmp_path, monkeypatch):
    payload = _payload("seek:123", url="https://www.seek.com.au/job/123", jd="Full neutral JD")
    payload["detail_evidence"].pop("source_metadata")
    jh_db = tmp_path / "jh.db"
    _create_jh_db(
        jh_db,
        [("u1", "seek:123", "seek", "123", "BA", "Acme", "2026-09-08", "2026-09-10", json.dumps(payload))],
    )
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")

    report = apply_bootstrap(plan_bootstrap(jh_db, jmm_db_path=db.DB_PATH))

    assert report.jobs_created == 1
    assert report.jds_stored == 0
    with db.connect() as conn:
        job = conn.execute("SELECT full_description,jd_fetched_at,jd_source FROM jobs").fetchone()
    assert dict(job) == {"full_description": None, "jd_fetched_at": None, "jd_source": None}


def test_valid_detail_metadata_url_is_authoritative_over_history_tracking_url(tmp_path):
    payload = _payload(
        "seek:123",
        url="https://www.seek.com.au/job/123?type=standard&origin=cardTitle",
        jd="Full neutral JD",
    )
    payload["detail_evidence"]["source_metadata"]["canonical_url"] = (
        "https://www.seek.com.au/job/123"
    )
    jh_db = tmp_path / "jh.db"
    _create_jh_db(
        jh_db,
        [("u1", "seek:123", "seek", "123", "BA", "Acme", "2026-09-08", "2026-09-10", json.dumps(payload))],
    )

    plan = plan_bootstrap(jh_db, jmm_db_path=tmp_path / "market.db")

    assert plan.report.valid_importable_jobs == 1
    assert plan.report.jobs_with_jds == 1
    assert plan.report.conflicts == 0
    assert plan.candidates[0].canonical_url == "https://www.seek.com.au/job/123"


def test_conflicting_source_ids_for_same_url_are_blocked(tmp_path):
    jh_db = tmp_path / "jh.db"
    url = "https://www.seek.com.au/job/shared"
    _create_jh_db(
        jh_db,
        [
            ("u1", "seek:1", "seek", "1", "A", "Acme", "2026-09-08", "2026-09-10", json.dumps(_payload("seek:1", url=url))),
            ("u1", "seek:2", "seek", "2", "B", "Acme", "2026-09-08", "2026-09-10", json.dumps(_payload("seek:2", url=url))),
        ],
    )

    plan = plan_bootstrap(jh_db, jmm_db_path=tmp_path / "market.db")

    assert plan.report.valid_importable_jobs == 0
    assert plan.report.identity_conflicts == 2
    assert plan.report.conflicts == 2


def test_existing_url_only_identity_is_promoted_without_overwriting_jmm_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs(source,source_job_id,canonical_url,title,employer,location,first_seen_at,last_seen_at)
            VALUES('seek',NULL,'https://www.seek.com.au/job/123','Existing JMM title','Existing JMM employer','Sydney NSW','2026-09-01','2026-09-09')
            """
        )

    jh_db = tmp_path / "jh.db"
    _create_jh_db(
        jh_db,
        [
            ("u1", "seek:123", "seek", "123", "New title", "Acme", "2026-09-08", "2026-09-10", json.dumps(_payload("seek:123", url="https://www.seek.com.au/job/123", title="New title"))),
        ],
    )

    report = apply_bootstrap(plan_bootstrap(jh_db, jmm_db_path=db.DB_PATH))

    assert report.jobs_updated == 1
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT source_job_id,identity_key,canonical_url,title,employer,location FROM jobs"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["source_job_id"] == "123"
    assert rows[0]["identity_key"] == "seek:id:123"
    assert rows[0]["canonical_url"] == "https://www.seek.com.au/job/123"
    assert rows[0]["title"] == "Existing JMM title"
    assert rows[0]["employer"] == "Existing JMM employer"
    assert rows[0]["location"] == "Sydney NSW"


def test_apply_requires_matching_dry_run_report(tmp_path):
    jh_db = tmp_path / "jh.db"
    jh_db.touch()
    marker = tmp_path / "dry-run.json"
    with pytest.raises(RuntimeError, match="run the dry-run first"):
        require_matching_dry_run(marker, jh_db)

    from collector.bootstrap_jh import BootstrapReport

    write_report(marker, BootstrapReport(mode="dry-run", source_db=str(jh_db.resolve())))
    payload = require_matching_dry_run(marker, jh_db)
    assert payload["mode"] == "dry-run"
