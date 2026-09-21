from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock

from collector.db import (
    connect,
    get_job_by_id,
    get_job_jd,
    get_primary_job_id,
    store_job_jd_once,
    update_job_source_facts,
)
from collector.job_retirement import (
    retire_known_terminal_family,
    retire_terminal_source_job,
)
from collector.run_logging import collection_logger
from collector.source_status import (
    TERMINAL_SOURCE_STATUSES,
    source_status_is_active_sql,
)


class JDEnrichmentError(RuntimeError):
    """Base error for supported JD enrichment failures."""


class UnsupportedJDSourceError(JDEnrichmentError):
    """The vacancy source does not yet have a JMM JD adapter."""


class JDSourceFetchError(JDEnrichmentError):
    """A source adapter could not obtain a validated JD."""


class JDSourcePostingUnavailableError(JDSourceFetchError):
    """One source posting is conclusively closed, gone or unavailable."""

    def __init__(self, message: str, *, source_status: str) -> None:
        super().__init__(message)
        self.source_status = str(source_status or "").strip().casefold()


class JDSourceUnavailableError(JDSourceFetchError):
    """No active supported source posting can provide the vacancy JD."""


class FetchedJD:
    def __init__(
        self,
        *,
        full_description: str,
        jd_source: str,
        facts: dict[str, object] | None = None,
    ) -> None:
        self.full_description = str(full_description or "").strip()
        self.jd_source = str(jd_source or "").strip()
        self.facts = dict(facts or {})


def _fetch_seek(job: dict[str, object]) -> FetchedJD:
    from collector.seek_jd_adapter import fetch_seek_jd_for_job

    return fetch_seek_jd_for_job(job)


def _fetch_linkedin(job: dict[str, object]) -> FetchedJD:
    from collector.linkedin_jd_adapter import fetch_linkedin_jd_for_job

    return fetch_linkedin_jd_for_job(job)


_SOURCE_FETCHERS: dict[str, Callable[[dict[str, object]], FetchedJD]] = {
    "seek": _fetch_seek,
    "linkedin": _fetch_linkedin,
}
_ENRICH_LOCK = Lock()
_SOURCE_PRIORITY = {"linkedin": 0, "seek": 1}


def _vacancy_source_jobs(primary_job_id: int) -> list[dict[str, object]]:
    """Return active linked source postings, newest first within source priority."""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT j.*,s.last_seen_at AS observation_last_seen_at
              FROM jobs j
              LEFT JOIN job_observation_state s ON s.job_id=j.id
             WHERE (j.id=? OR j.primary_job_id=?)
               AND COALESCE(s.archived,0)=0
               AND {source_status_is_active_sql('j.source_status')}
             ORDER BY datetime(s.last_seen_at) DESC, datetime(j.posted_at) DESC, j.id DESC
            """,
            (primary_job_id, primary_job_id),
        ).fetchall()
    jobs = [dict(row) for row in rows]
    return sorted(
        jobs,
        key=lambda job: (
            _SOURCE_PRIORITY.get(str(job.get("source") or "").casefold(), 99),
            -int(job["id"]),
        ),
    )


def _cached_result(job_id: int) -> dict[str, object] | None:
    jd = get_job_jd(job_id)
    if jd is None:
        return None
    return {"status": "cached", **jd}


def _terminal_status_from_facts(facts: dict[str, object]) -> str | None:
    status = str(facts.get("source_status") or "").strip().casefold()
    return status if status in TERMINAL_SOURCE_STATUSES else None


def get_or_enrich_job_jd(job_id: int, *, source: str | None = None) -> dict[str, object]:
    """Return the canonical JD, enriching it from an active source posting when absent."""
    requested_source = str(source or "").strip().casefold()

    with _ENRICH_LOCK:
        primary_job_id = get_primary_job_id(job_id)

        surviving_primary = retire_known_terminal_family(primary_job_id)
        if surviving_primary is None:
            raise JDSourceUnavailableError(
                f"vacancy {job_id} has no active source posting after terminal cleanup"
            )
        primary_job_id = surviving_primary
        primary = get_job_by_id(primary_job_id)
        if primary is None:
            raise JDSourceUnavailableError(
                f"vacancy {job_id} has no active canonical posting"
            )

        candidates = _vacancy_source_jobs(primary_job_id)
        supported = [
            candidate
            for candidate in candidates
            if str(candidate.get("source") or "").strip().casefold() in _SOURCE_FETCHERS
            and (
                not requested_source
                or str(candidate.get("source") or "").strip().casefold() == requested_source
            )
        ]
        if not supported:
            active_sources = sorted(
                {
                    str(candidate.get("source") or "").strip().casefold()
                    for candidate in candidates
                    if str(candidate.get("source") or "").strip()
                }
            )
            if requested_source and requested_source in _SOURCE_FETCHERS:
                raise JDSourceUnavailableError(
                    f"no active {requested_source} posting remains for vacancy {primary_job_id}"
                )
            raise UnsupportedJDSourceError(
                "JD enrichment is not supported for active vacancy sources "
                + repr(active_sources)
            )

        cached = _cached_result(primary_job_id)
        if cached is not None:
            return cached

        failures: list[str] = []
        unavailable_failures = 0
        log = collection_logger()
        for candidate in supported:
            candidate_id = int(candidate["id"])
            candidate_source = str(candidate.get("source") or "").strip().casefold()
            fetcher = _SOURCE_FETCHERS[candidate_source]
            try:
                fetched = fetcher(candidate)
                if not fetched.full_description:
                    raise JDSourceFetchError("source adapter returned an empty JD")
                if not fetched.jd_source:
                    raise JDSourceFetchError("source adapter returned no JD provenance")
                terminal_status = _terminal_status_from_facts(fetched.facts)
                if terminal_status:
                    raise JDSourcePostingUnavailableError(
                        f"{candidate_source} posting is terminal ({terminal_status})",
                        source_status=terminal_status,
                    )
            except JDSourcePostingUnavailableError as exc:
                log.info(
                    "JD_SOURCE_TERMINAL job_id=%s source=%s source_job_id=%s status=%s "
                    "title=%r url=%s error=%s",
                    candidate_id,
                    candidate_source,
                    candidate.get("source_job_id"),
                    exc.source_status,
                    candidate.get("title"),
                    candidate.get("canonical_url"),
                    exc,
                )
                retire_terminal_source_job(
                    candidate_id,
                    source_status=exc.source_status,
                    reason=str(exc),
                )
                unavailable_failures += 1
                failures.append(f"{candidate_source}: {exc}")
                continue
            except JDSourceFetchError as exc:
                log.warning(
                    "JD_SOURCE_FETCH_FAILED job_id=%s source=%s source_job_id=%s title=%r "
                    "url=%s error=%s",
                    candidate_id,
                    candidate_source,
                    candidate.get("source_job_id"),
                    candidate.get("title"),
                    candidate.get("canonical_url"),
                    exc,
                )
                failures.append(f"{candidate_source}: {exc}")
                continue

            if fetched.facts:
                update_job_source_facts(candidate_id, **fetched.facts)

            storage_primary_id = get_primary_job_id(candidate_id)
            stored = store_job_jd_once(
                storage_primary_id,
                full_description=fetched.full_description,
                jd_fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
                jd_source=fetched.jd_source,
            )
            return {"status": "enriched", **stored}

        message = "all eligible JD source postings failed: " + " | ".join(failures)
        if unavailable_failures == len(supported):
            raise JDSourceUnavailableError(message)
        raise JDSourceFetchError(message)
