import json

from collector import db
from collector.ingest import ingest_card
from collector.models import CardObservation
from collector.posting_date_backfill import (
    backfill_linkedin_posting_dates,
    backfill_seek_posting_dates,
)


def test_seek_backfill_uses_original_capture_time_and_updates_field_state(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    created = ingest_card(
        CardObservation(
            source="seek",
            source_job_id="backfill-1",
            canonical_url="https://au.seek.com/job/94548673",
            title="Business Analyst",
        )
    )
    with db.connect() as conn:
        conn.execute("DELETE FROM card_captures WHERE job_id=?", (created.job_id,))
        conn.execute(
            """INSERT INTO card_captures(job_id,captured_at,raw_json) VALUES(?,?,?)""",
            (
                created.job_id,
                "2026-09-10T07:11:22+00:00",
                json.dumps({"posted_text": "Listed four hours ago"}),
            ),
        )
        conn.execute("UPDATE jobs SET posted_at=NULL,posted_at_basis=NULL WHERE id=?", (created.job_id,))

    preview = backfill_seek_posting_dates(apply=False)
    assert preview.derivable == 1
    assert preview.updated == 0
    with db.connect() as conn:
        assert conn.execute("SELECT posted_at FROM jobs WHERE id=?", (created.job_id,)).fetchone()[0] is None

    applied = backfill_seek_posting_dates(apply=True)
    assert applied.updated == 1
    with db.connect() as conn:
        job = conn.execute("SELECT posted_at,posted_at_basis FROM jobs WHERE id=?", (created.job_id,)).fetchone()
        state = conn.execute(
            "SELECT state FROM job_field_states WHERE job_id=? AND field_name='posted_at'",
            (created.job_id,),
        ).fetchone()[0]
    assert tuple(job) == ("2026-09-10T03:11:22+00:00", "source_relative")
    assert state == "known"


def test_seek_backfill_prefers_exact_capture_over_relative_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    created = ingest_card(
        CardObservation(
            source="seek",
            source_job_id="exact-beats-relative",
            canonical_url="https://au.seek.com/job/94548677",
            title="Role",
        )
    )
    with db.connect() as conn:
        conn.execute("DELETE FROM card_captures WHERE job_id=?", (created.job_id,))
        conn.executemany(
            "INSERT INTO card_captures(job_id,captured_at,raw_json) VALUES(?,?,?)",
            [
                (created.job_id, "2026-09-10T07:11:22+00:00", json.dumps({"posted_text": "1 day ago"})),
                (created.job_id, "2026-09-10T07:11:22+00:00", json.dumps({"posted_at_source": "2026-09-09"})),
            ],
        )

    assert backfill_seek_posting_dates(apply=True).updated == 1
    with db.connect() as conn:
        job = conn.execute("SELECT posted_at,posted_at_basis FROM jobs WHERE id=?", (created.job_id,)).fetchone()
    assert tuple(job) == ("2026-09-09", "source_exact")


def test_linkedin_backfill_uses_unique_bounded_run_for_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    created = ingest_card(
        CardObservation(
            source="linkedin",
            source_job_id="window-bound",
            canonical_url="https://www.linkedin.com/jobs/view/94548679",
            title="Role",
        )
    )
    with db.connect() as conn:
        conn.execute("DELETE FROM card_captures WHERE job_id=?", (created.job_id,))
        conn.execute(
            "INSERT INTO collection_runs(source,query_text,started_at,finished_at,status,metadata_json) "
            "VALUES('linkedin','analyst','2026-09-10T06:00:00+00:00','2026-09-10T07:00:00+00:00','OK',?)",
            (json.dumps({"hours_old": 24, "start_offset": 0, "next_offset": 10}),),
        )
        conn.execute(
            "INSERT INTO card_captures(job_id,captured_at,raw_json) VALUES(?,?,?)",
            (created.job_id, "2026-09-10T06:30:00+00:00", json.dumps({"offset": 0, "jobspy": {}})),
        )
        conn.execute("UPDATE jobs SET posted_at=NULL,posted_at_basis=NULL WHERE id=?", (created.job_id,))

    preview = backfill_linkedin_posting_dates()
    assert (preview.eligible, preview.bounded, preview.updated) == (1, 1, 0)
    applied = backfill_linkedin_posting_dates(apply=True)
    assert applied.updated == 1
    with db.connect() as conn:
        job = conn.execute("SELECT posted_at,posted_at_basis FROM jobs WHERE id=?", (created.job_id,)).fetchone()
    assert tuple(job) == ("2026-09-09T06:30:00+00:00", "search_window_bound")


def test_seek_backfill_clears_stale_not_present_value_when_no_capture_evidence_exists(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    created = ingest_card(
        CardObservation(
            source="seek",
            source_job_id="stale-no-evidence",
            canonical_url="https://au.seek.com/job/94548675",
            title="Role",
            posted_at="2026-09-01",
        )
    )
    with db.connect() as conn:
        conn.execute("DELETE FROM card_captures WHERE job_id=?", (created.job_id,))
        conn.execute("UPDATE job_field_states SET state='not_present' WHERE job_id=? AND field_name='posted_at'", (created.job_id,))
    result = backfill_seek_posting_dates(apply=True)
    assert result.cleared_stale == 1
    with db.connect() as conn:
        job = conn.execute("SELECT posted_at FROM jobs WHERE id=?", (created.job_id,)).fetchone()
        state = conn.execute(
            "SELECT state FROM job_field_states WHERE job_id=? AND field_name='posted_at'",
            (created.job_id,),
        ).fetchone()[0]
        evidence = conn.execute(
            "SELECT raw_json FROM card_captures WHERE job_id=?", (created.job_id,)
        ).fetchone()[0]
    assert job[0] is None
    assert state == "unknown"
    assert '"previous_posted_at": "2026-09-01"' in evidence
