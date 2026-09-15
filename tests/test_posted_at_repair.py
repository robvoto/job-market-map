from __future__ import annotations

from collector import db, posted_at_repair
from collector.ingest import ingest_card
from collector.linkedin_detail import LinkedInDetailEvidence
from collector.models import CardObservation


def _detail(*, posted_at: str | None, source_status: str | None = None) -> LinkedInDetailEvidence:
    return LinkedInDetailEvidence(
        full_description=None,
        apply_url=None,
        apply_method="unknown",
        easy_apply=None,
        reposted=False,
        source_status=source_status,
        applicant_count=None,
        header_text="",
        posted_at=posted_at,
    )


def test_repair_linkedin_fills_exact_date_and_retires_closed_source(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    first = ingest_card(
        CardObservation(
            source="linkedin",
            source_job_id="li-1",
            canonical_url="https://www.linkedin.com/jobs/view/1",
            title="Business Analyst",
        )
    )
    second = ingest_card(
        CardObservation(
            source="linkedin",
            source_job_id="li-2",
            canonical_url="https://www.linkedin.com/jobs/view/2",
            title="Senior Business Analyst",
        )
    )
    third = ingest_card(
        CardObservation(
            source="linkedin",
            source_job_id="li-3",
            canonical_url="https://www.linkedin.com/jobs/view/3",
            title="Principal Business Analyst",
        )
    )

    def fetch(url: str) -> LinkedInDetailEvidence:
        if url.endswith("/1"):
            return _detail(posted_at="2026-09-15")
        if url.endswith("/2"):
            return _detail(posted_at=None, source_status="no_longer_accepting_applications")
        return _detail(posted_at=None)

    monkeypatch.setattr(posted_at_repair, "fetch_linkedin_detail", fetch)
    result = posted_at_repair.repair_linkedin_posted_at(limit=3)

    assert result.filled == 1
    assert result.closed == 1
    assert result.without_exact_date == 1
    assert result.failures == 0
    with db.connect() as conn:
        first_row = conn.execute("SELECT posted_at FROM jobs WHERE id=?", (first.job_id,)).fetchone()
        second_row = conn.execute("SELECT source_status FROM jobs WHERE id=?", (second.job_id,)).fetchone()
        third_row = conn.execute(
            "SELECT outcome FROM posted_at_repair_attempts WHERE job_id=?",
            (third.job_id,),
        ).fetchone()
    assert first_row[0] == "2026-09-15"
    assert second_row[0] == "no_longer_accepting_applications"
    assert third_row[0] == "source_date_unavailable"
    assert posted_at_repair.repair_linkedin_posted_at(limit=3).candidates == 0
