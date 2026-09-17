from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from collector.db import connect
from collector.jd_enrichment import JDSourceFetchError, JDSourceUnavailableError, get_or_enrich_job_jd
from collector.jd_queue import pending_primary_ids, record_pending_attempt
from collector.run_logging import collection_logger
from collector.source_status import source_status_is_active_sql


@dataclass(frozen=True)
class JDBatchResult:
    candidates: int
    stored: int
    cached: int
    failed: int
    unavailable: int


def _primary_ids(*, source: str | None = None, first_seen_since: str | None = None, last_seen_since: str | None = None, posted_since: str | None = None, require_missing_jd: bool = True) -> list[int]:
    clauses = [
        f"{source_status_is_active_sql('j.source_status')}",
        "COALESCE(s.archived,0)=0",
    ]
    if require_missing_jd:
        clauses.append("(p.full_description IS NULL OR trim(p.full_description)='')")
    params: list[object] = []
    if source:
        clauses.append("j.source=?")
        params.append(str(source).strip().casefold())
    if first_seen_since:
        clauses.append("datetime(s.first_seen_at) >= datetime(?)")
        params.append(first_seen_since)
    if last_seen_since:
        clauses.append("datetime(s.last_seen_at) >= datetime(?)")
        params.append(last_seen_since)
    if posted_since:
        cutoff = datetime.fromisoformat(str(posted_since))
        if source and str(source).strip().casefold() == "linkedin":
            # LinkedIn cards can carry a date-only YYYY-MM-DD value. Treat those
            # as the source's local calendar date, not as midnight UTC. Full
            # timestamps still compare as instants through SQLite datetime().
            clauses.append(
                "((length(trim(j.posted_at))=10 AND date(j.posted_at) >= ?) "
                "OR (length(trim(j.posted_at))>10 AND datetime(j.posted_at) >= datetime(?)))"
            )
            params.extend([cutoff.date().isoformat(), posted_since])
        else:
            clauses.append("datetime(j.posted_at) >= datetime(?)")
            params.append(posted_since)
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT COALESCE(j.primary_job_id,j.id) AS primary_id "
            "FROM jobs j "
            "JOIN job_observation_state s ON s.job_id=j.id "
            "JOIN jobs p ON p.id=COALESCE(j.primary_job_id,j.id) "
            "WHERE " + " AND ".join(clauses) + " ORDER BY primary_id",
            params,
        ).fetchall()
    return [int(row["primary_id"]) for row in rows]


def missing_jd_primary_ids(*, source: str | None = None, first_seen_since: str | None = None, last_seen_since: str | None = None, posted_since: str | None = None) -> list[int]:
    return _primary_ids(
        source=source, first_seen_since=first_seen_since, last_seen_since=last_seen_since,
        posted_since=posted_since, require_missing_jd=True,
    )


def recent_primary_ids(*, source: str | None = None, posted_since: str) -> list[int]:
    return _primary_ids(source=source, posted_since=posted_since, require_missing_jd=False)


def _enrich_ids(ids: list[int], *, source: str | None = None, record_queue_attempts: bool = False) -> JDBatchResult:
    log = collection_logger()
    stored = cached = failed = unavailable = 0
    for primary_id in ids:
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                result = (
                    get_or_enrich_job_jd(primary_id, source=source)
                    if source
                    else get_or_enrich_job_jd(primary_id)
                )
                if result.get("status") == "cached":
                    cached += 1
                else:
                    stored += 1
                if record_queue_attempts:
                    record_pending_attempt(primary_id, error=None)
                last_error = None
                break
            except JDSourceUnavailableError as exc:
                unavailable += 1
                if record_queue_attempts:
                    record_pending_attempt(primary_id, error=str(exc))
                log.info("JD unavailable primary_job_id=%s error=%s", primary_id, exc)
                last_error = None
                break
            except JDSourceFetchError as exc:
                last_error = exc
                message = str(exc)
                rate_limited = (
                    "429" in message
                    or "rate limit" in message.casefold()
                    or "rate-limit" in message.casefold()
                )
                if record_queue_attempts:
                    record_pending_attempt(primary_id, error=message)
                if rate_limited:
                    failed += 1
                    log.error(
                        "JD_FETCH_FAILED primary_job_id=%s source=%s error=%s",
                        primary_id, source or "any", message,
                    )
                    log.error(
                        "JD_RATE_LIMIT_ABORT source=%s primary_job_id=%s remaining_candidates=%s",
                        source or "any", primary_id, max(0, len(ids) - stored - cached - failed - unavailable),
                    )
                    return JDBatchResult(len(ids), stored, cached, failed, unavailable)
                if attempt == 1:
                    log.warning(
                        "JD_FETCH_RETRY primary_job_id=%s source=%s error=%s",
                        primary_id, source or "any", message,
                    )
                    continue
                failed += 1
                log.error(
                    "JD_FETCH_FAILED primary_job_id=%s source=%s error=%s",
                    primary_id, source or "any", message,
                )
        if last_error is not None:
            continue
    result = JDBatchResult(len(ids), stored, cached, failed, unavailable)
    log.info(
        "JD batch complete candidates=%s stored=%s cached=%s failed=%s unavailable=%s",
        result.candidates, result.stored, result.cached, result.failed, result.unavailable,
    )
    return result


def enrich_missing_jds(*, source: str | None = None, first_seen_since: str | None = None, last_seen_since: str | None = None, posted_since: str | None = None) -> JDBatchResult:
    ids = missing_jd_primary_ids(
        source=source, first_seen_since=first_seen_since, last_seen_since=last_seen_since,
        posted_since=posted_since,
    )
    return _enrich_ids(ids, source=source)


def enrich_pending_jds(*, source: str | None = None) -> JDBatchResult:
    ids = pending_primary_ids(source=source)
    return _enrich_ids(ids, source=source, record_queue_attempts=True)
