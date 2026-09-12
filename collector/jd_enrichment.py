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


class JDEnrichmentError(RuntimeError):
    """Base error for supported on-demand JD enrichment failures."""


class UnsupportedJDSourceError(JDEnrichmentError):
    """The canonical job source does not yet have a JMM JD adapter."""


class JDSourceFetchError(JDEnrichmentError):
    """The source adapter could not obtain a validated JD."""


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
    """Return linked source postings in cheapest-supported-source order."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE id=? OR primary_job_id=?",
            (primary_job_id, primary_job_id),
        ).fetchall()
    jobs = [dict(row) for row in rows]
    return sorted(
        jobs,
        key=lambda job: (
            _SOURCE_PRIORITY.get(str(job.get("source") or "").casefold(), 99),
            int(job["id"]),
        ),
    )


def _cached_result(job_id: int) -> dict[str, object] | None:
    jd = get_job_jd(job_id)
    if jd is None:
        return None
    return {"status": "cached", **jd}


def get_or_enrich_job_jd(job_id: int) -> dict[str, object]:
    """Return JMM's canonical JD, fetching it once from the source when absent."""
    primary_job_id = get_primary_job_id(job_id)
    cached = _cached_result(primary_job_id)
    if cached is not None:
        return cached

    with _ENRICH_LOCK:
        primary_job_id = get_primary_job_id(job_id)
        cached = _cached_result(primary_job_id)
        if cached is not None:
            return cached

        primary = get_job_by_id(primary_job_id)
        if primary is None:
            raise KeyError(f"job {job_id} not found")

        candidates = _vacancy_source_jobs(primary_job_id)
        supported = [
            candidate
            for candidate in candidates
            if str(candidate.get("source") or "").strip().casefold() in _SOURCE_FETCHERS
        ]
        if not supported:
            sources = sorted(
                {
                    str(candidate.get("source") or "").strip().casefold()
                    for candidate in candidates
                    if str(candidate.get("source") or "").strip()
                }
            )
            raise UnsupportedJDSourceError(
                "JD enrichment is not supported for vacancy sources " + repr(sources)
            )

        failures: list[str] = []
        for candidate in supported:
            source = str(candidate.get("source") or "").strip().casefold()
            fetcher = _SOURCE_FETCHERS[source]
            try:
                fetched = fetcher(candidate)
                if not fetched.full_description:
                    raise JDSourceFetchError("source adapter returned an empty JD")
                if not fetched.jd_source:
                    raise JDSourceFetchError("source adapter returned no JD provenance")
            except JDSourceFetchError as exc:
                failures.append(f"{source}: {exc}")
                continue

            # Source-specific detail facts belong to the posting that supplied them.
            # The neutral JD itself belongs to the canonical vacancy primary.
            if fetched.facts:
                update_job_source_facts(int(candidate["id"]), **fetched.facts)

            stored = store_job_jd_once(
                primary_job_id,
                full_description=fetched.full_description,
                jd_fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
                jd_source=fetched.jd_source,
            )
            return {"status": "enriched", **stored}

        raise JDSourceFetchError("all linked JD sources failed: " + " | ".join(failures))
