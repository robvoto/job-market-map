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
        "title": title,
        "company": "Acme",
        "url": url,
        "location": "Sydney NSW",
        "salary": "$900 - $1,000 p.d.",
        "work_type": "Contract",
        "work_mode": "Hybrid",
        "posted": "2d ago",
        "original_posted_date": "2026-09-08",
        "original_posted_date_status": "verified",
        "teaser": "Neutral card teaser",
        "is_reposted": False,
        "fit_score": 99,
        "decision": "KEEP",
        "is_hidden": True,
    }
    if jd:
        snapshot["full_description"] = jd
        snapshot["description_source"] = "seek_detail"
    return {
        "job_key": job_key,
        "source": job_key.split(":", 1)[0],
        "title": title,
        "company": "Acme",
        "url": url,
        "first_seen_at": "2026-09-08T01:00:00+00:00",
        "last_seen_at": "2026-09-10T01:00:00+00:00",
        "times_viewed": 12,
        "is_liked": True,
        "last_kept_at": "2026-09-10T01:00:00+00:00",
        "last_kept_snapshot": snapshot,
        "detail_evidence": {
            "details_text": jd or "",
            "fetched_at": "2026-09-09T12:00:00+00:00",
        },
        "source_metadata": {
            "schema_version": 1,
            "platform_job_id": job_key.split(":", 1)[1],
            "canonical_url": url,
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
    assert not jmm_db.exists()


def test_apply_imports_only_neutral_fields_copies_jd_and_is_idempotent(tmp_path, monkeypatch):
    jh_db = tmp_path / "jh.db"
    payload = _payload("seek:123", url="https://www.seek.com.au/job/123?utm_source=x", jd="Full neutral JD")
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
    assert job["location"] == "Sydney NSW"
    assert job["salary_text"] == "$900 - $1,000 p.d."
    assert job["employment_type"] == "Contract"
    assert job["workplace_type"] == "Hybrid"
    assert job["full_description"] == "Full neutral JD"
    capture = json.loads(captures[0][0])
    assert capture["origin"] == BOOTSTRAP_ORIGIN
    assert "private-user-id" not in captures[0][0]
    assert "fit_score" not in captures[0][0]
    assert "is_liked" not in captures[0][0]


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


def test_existing_url_only_jmm_identity_is_promoted_without_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs(source,source_job_id,canonical_url,title,first_seen_at,last_seen_at)
            VALUES('seek',NULL,'https://www.seek.com.au/job/123','Old title','2026-09-01','2026-09-09')
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
        rows = conn.execute("SELECT source_job_id,identity_key,title FROM jobs").fetchall()
    assert len(rows) == 1
    assert rows[0]["source_job_id"] == "123"
    assert rows[0]["identity_key"] == "seek:id:123"
    assert rows[0]["title"] == "New title"


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
