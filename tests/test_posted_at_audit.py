from __future__ import annotations

from collector import db
from collector.ingest import ingest_card
from collector.models import CardObservation
from collector.posted_at_audit import audit_posted_at_completeness


def _job(*, source: str, source_job_id: str, posted_at: str | None = None) -> None:
    ingest_card(
        CardObservation(
            source=source,
            source_job_id=source_job_id,
            canonical_url=f"https://{source}.test/{source_job_id}",
            title="Role",
            posted_at=posted_at,
        )
    )


def test_audit_separates_date_bases_from_unsupported_or_retryable_rows(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    _job(source="seek", source_job_id="seek-date", posted_at="2026-09-15T01:02:03Z")
    _job(source="seek", source_job_id="seek-missing")
    _job(source="linkedin", source_job_id="linkedin-retryable")
    _job(source="linkedin", source_job_id="linkedin-unavailable")
    _job(source="apsjobs", source_job_id="aps-missing")

    with db.connect() as conn:
        linkedin_id = conn.execute(
            "SELECT id FROM jobs WHERE source='linkedin' AND source_job_id='linkedin-unavailable'"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO posted_at_repair_attempts(job_id, source, outcome, attempted_at)
            VALUES (?, 'linkedin', 'source_date_unavailable', '2026-09-15T00:00:00Z')
            """,
            (linkedin_id,),
        )

    audits = {audit.source: audit for audit in audit_posted_at_completeness()}
    assert audits["seek"].with_posted_at == 1
    assert audits["seek"].source_exact == 1
    assert audits["seek"].missing_posted_at == 1
    assert audits["seek"].retryable_repair_candidates == 0
    assert audits["seek"].missing_without_supported_repair_path == 1
    assert audits["linkedin"].retryable_repair_candidates == 1
    assert audits["linkedin"].attempted_without_usable_date == 1
    assert audits["linkedin"].missing_without_supported_repair_path == 1
    assert audits["apsjobs"].missing_without_supported_repair_path == 1
