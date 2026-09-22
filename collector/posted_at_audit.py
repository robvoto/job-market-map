"""Read-only completeness metrics for source-published posting dates."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from collector import db
from collector.posting_dates import normalize_posting_date


@dataclass(frozen=True)
class PostedAtSourceAudit:
    """Completeness and safe-repair state for one source."""

    source: str
    total: int
    with_posted_at: int
    missing_posted_at: int
    source_exact: int
    source_relative: int
    search_window_bound: int
    legacy_basis_unknown: int
    seek_backfill_candidates: int
    retryable_repair_candidates: int
    attempted_without_usable_date: int
    attempted_closed_without_usable_date: int
    missing_without_supported_repair_path: int

    def as_dict(self) -> dict[str, int | str]:
        return asdict(self)


def audit_posted_at_completeness() -> list[PostedAtSourceAudit]:
    """Return source-level date metrics without changing the runtime database.

    ``missing_without_supported_repair_path`` deliberately does not claim that
    the source can never provide a date. It identifies rows for which JMM has
    no bounded repair adapter or has already recorded that the exact source
    date was unavailable. Retained source-relative labels paired with their
    capture timestamp count as repair evidence; observation timestamps alone
    do not.
    """

    uri = f"{Path(db.DB_PATH).resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)")
        }
        basis_expr = "j.posted_at_basis" if "posted_at_basis" in columns else "NULL"
        conn = connection
        rows = conn.execute(
            f"""
            SELECT
                j.source,
                COUNT(*) AS total,
                SUM(CASE WHEN j.posted_at IS NOT NULL
                          AND trim(j.posted_at) <> '' THEN 1 ELSE 0 END) AS with_posted_at,
                SUM(CASE WHEN j.posted_at IS NULL
                          OR trim(j.posted_at) = '' THEN 1 ELSE 0 END) AS missing_posted_at,
                SUM(CASE WHEN {basis_expr}='source_exact' THEN 1 ELSE 0 END) AS source_exact,
                SUM(CASE WHEN {basis_expr}='source_relative' THEN 1 ELSE 0 END) AS source_relative,
                SUM(CASE WHEN {basis_expr}='search_window_bound' THEN 1 ELSE 0 END) AS search_window_bound,
                SUM(CASE WHEN j.posted_at IS NOT NULL AND trim(j.posted_at)<>''
                              AND {basis_expr} IS NULL THEN 1 ELSE 0 END) AS legacy_basis_unknown,
                0 AS seek_backfill_candidates,
                SUM(CASE WHEN ((j.posted_at IS NULL OR trim(j.posted_at)='')
                          OR EXISTS (SELECT 1 FROM job_field_states lf WHERE lf.job_id=j.id
                                     AND lf.field_name='posted_at' AND lf.state='not_present'))
                          AND j.source='linkedin'
                          AND j.source_status IS NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM posted_at_repair_attempts AS a
                               WHERE a.job_id = j.id
                          )
                         THEN 1 ELSE 0 END) AS retryable_repair_candidates,
                SUM(CASE WHEN (j.posted_at IS NULL OR trim(j.posted_at) = '')
                          AND a.outcome = 'source_date_unavailable'
                         THEN 1 ELSE 0 END) AS attempted_without_usable_date,
                SUM(CASE WHEN (j.posted_at IS NULL OR trim(j.posted_at) = '')
                          AND a.outcome IN ('closed_without_exact_date','closed_without_usable_date')
                         THEN 1 ELSE 0 END) AS attempted_closed_without_usable_date
            FROM jobs AS j
            LEFT JOIN posted_at_repair_attempts AS a ON a.job_id = j.id
            GROUP BY j.source
            ORDER BY j.source
            """
        ).fetchall()

        seek_ids = {
            int(row[0])
            for row in conn.execute(
                """SELECT j.id FROM jobs j
                   LEFT JOIN job_field_states fs
                     ON fs.job_id=j.id AND fs.field_name='posted_at'
                   WHERE j.source='seek'
                     AND ((j.posted_at IS NULL OR trim(j.posted_at)=''
                           OR julianday(j.posted_at) IS NULL)
                          OR fs.state='not_present')"""
            )
        }
        seek_candidates: set[int] = set()
        if seek_ids:
            for capture in conn.execute(
                "SELECT job_id,captured_at,raw_json FROM card_captures"
            ):
                job_id = int(capture["job_id"])
                if job_id not in seek_ids or not capture["captured_at"]:
                    continue
                try:
                    evidence = json.loads(capture["raw_json"] or "{}")
                except json.JSONDecodeError:
                    continue
                if not isinstance(evidence, dict):
                    continue
                if normalize_posting_date(
                    posted_at=evidence.get("posted_at_source"),
                    posted_text=evidence.get("posted_text"),
                    captured_at=str(capture["captured_at"]),
                ):
                    seek_candidates.add(job_id)

    audits: list[PostedAtSourceAudit] = []
    for row in rows:
        missing = int(row["missing_posted_at"] or 0)
        retryable = int(row["retryable_repair_candidates"] or 0)
        attempted_without = int(row["attempted_without_usable_date"] or 0)
        attempted_closed = int(row["attempted_closed_without_usable_date"] or 0)
        audits.append(
            PostedAtSourceAudit(
                source=str(row["source"]),
                total=int(row["total"] or 0),
                with_posted_at=int(row["with_posted_at"] or 0),
                missing_posted_at=missing,
                source_exact=int(row["source_exact"] or 0),
                source_relative=int(row["source_relative"] or 0),
                search_window_bound=int(row["search_window_bound"] or 0),
                legacy_basis_unknown=int(row["legacy_basis_unknown"] or 0),
                seek_backfill_candidates=(
                    len(seek_candidates) if str(row["source"]) == "seek" else 0
                ),
                retryable_repair_candidates=retryable,
                attempted_without_usable_date=attempted_without,
                attempted_closed_without_usable_date=attempted_closed,
                missing_without_supported_repair_path=max(
                    0,
                    missing
                    - retryable
                    - (
                        len(seek_candidates)
                        if str(row["source"]) == "seek"
                        else 0
                    ),
                ),
            )
        )
    return audits
