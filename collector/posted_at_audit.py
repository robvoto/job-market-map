"""Read-only completeness metrics for source-published posting dates."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from collector.db import connect, init_db


@dataclass(frozen=True)
class PostedAtSourceAudit:
    """Completeness and safe-repair state for one source."""

    source: str
    total: int
    with_posted_at: int
    missing_posted_at: int
    retryable_repair_candidates: int
    attempted_without_exact_date: int
    attempted_closed_without_exact_date: int
    missing_without_supported_repair_path: int

    def as_dict(self) -> dict[str, int | str]:
        return asdict(self)


def audit_posted_at_completeness() -> list[PostedAtSourceAudit]:
    """Return source-level date metrics without changing the runtime database.

    ``missing_without_supported_repair_path`` deliberately does not claim that
    the source can never provide a date. It identifies rows for which JMM has
    no bounded repair adapter or has already recorded that the exact source
    date was unavailable. Relative card labels and observation timestamps are
    never considered date evidence.
    """

    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                j.source,
                COUNT(*) AS total,
                SUM(CASE WHEN j.posted_at IS NOT NULL
                          AND trim(j.posted_at) <> '' THEN 1 ELSE 0 END) AS with_posted_at,
                SUM(CASE WHEN j.posted_at IS NULL
                          OR trim(j.posted_at) = '' THEN 1 ELSE 0 END) AS missing_posted_at,
                SUM(CASE WHEN (j.posted_at IS NULL OR trim(j.posted_at) = '')
                          AND j.source = 'linkedin'
                          AND j.source_status IS NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM posted_at_repair_attempts AS a
                               WHERE a.job_id = j.id
                          )
                         THEN 1 ELSE 0 END) AS retryable_repair_candidates,
                SUM(CASE WHEN (j.posted_at IS NULL OR trim(j.posted_at) = '')
                          AND a.outcome = 'source_date_unavailable'
                         THEN 1 ELSE 0 END) AS attempted_without_exact_date,
                SUM(CASE WHEN (j.posted_at IS NULL OR trim(j.posted_at) = '')
                          AND a.outcome = 'closed_without_exact_date'
                         THEN 1 ELSE 0 END) AS attempted_closed_without_exact_date
            FROM jobs AS j
            LEFT JOIN posted_at_repair_attempts AS a ON a.job_id = j.id
            GROUP BY j.source
            ORDER BY j.source
            """
        ).fetchall()

    audits: list[PostedAtSourceAudit] = []
    for row in rows:
        missing = int(row["missing_posted_at"] or 0)
        retryable = int(row["retryable_repair_candidates"] or 0)
        attempted_without = int(row["attempted_without_exact_date"] or 0)
        attempted_closed = int(row["attempted_closed_without_exact_date"] or 0)
        audits.append(
            PostedAtSourceAudit(
                source=str(row["source"]),
                total=int(row["total"] or 0),
                with_posted_at=int(row["with_posted_at"] or 0),
                missing_posted_at=missing,
                retryable_repair_candidates=retryable,
                attempted_without_exact_date=attempted_without,
                attempted_closed_without_exact_date=attempted_closed,
                missing_without_supported_repair_path=max(0, missing - retryable),
            )
        )
    return audits
