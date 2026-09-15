"""Bounded, source-only repair for missing canonical posting dates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from collector.db import connect, update_job_source_facts
from collector.linkedin_detail import LinkedInDetailError, fetch_linkedin_detail


@dataclass(frozen=True)
class PostedAtRepairResult:
    candidates: int
    checked: int
    filled: int
    closed: int
    without_exact_date: int
    failures: int


def repair_linkedin_posted_at(*, limit: int) -> PostedAtRepairResult:
    """Fill missing LinkedIn dates from current exact source detail only.

    A source page may not expose its original date. The evidence outcome is
    persisted so the same page is not requested again indefinitely.
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    with connect() as conn:
        candidates = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, canonical_url
                FROM jobs
                WHERE source = 'linkedin'
                  AND posted_at IS NULL
                  AND source_status IS NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM posted_at_repair_attempts AS attempt
                      WHERE attempt.job_id = jobs.id
                  )
                ORDER BY id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]

    checked = filled = closed = without_exact_date = failures = 0
    for job in candidates:
        checked += 1
        try:
            detail = fetch_linkedin_detail(str(job["canonical_url"]))
        except LinkedInDetailError:
            failures += 1
            continue
        facts = detail.facts
        update_job_source_facts(int(job["id"]), **facts)
        if detail.posted_at:
            filled += 1
            outcome = "filled"
        elif detail.source_status:
            closed += 1
            outcome = "closed_without_exact_date"
        else:
            without_exact_date += 1
            outcome = "source_date_unavailable"
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO posted_at_repair_attempts(job_id, source, outcome, attempted_at)
                VALUES (?, 'linkedin', ?, ?)
                ON CONFLICT(job_id) DO NOTHING
                """,
                (
                    int(job["id"]),
                    outcome,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
    return PostedAtRepairResult(
        candidates=len(candidates),
        checked=checked,
        filled=filled,
        closed=closed,
        without_exact_date=without_exact_date,
        failures=failures,
    )
