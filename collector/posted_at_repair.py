"""Bounded, source-only repair for missing canonical posting dates."""

from __future__ import annotations

from dataclasses import dataclass

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

    A closed page may prove the source status but not expose its original date;
    that status is persisted so the same retired posting is not retried forever.
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
        elif detail.source_status:
            closed += 1
        else:
            without_exact_date += 1
    return PostedAtRepairResult(
        candidates=len(candidates),
        checked=checked,
        filled=filled,
        closed=closed,
        without_exact_date=without_exact_date,
        failures=failures,
    )
