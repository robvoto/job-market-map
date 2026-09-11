from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock

from collector.db import (
    get_job_by_id,
    get_job_jd,
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


def _cached_result(job_id: int) -> dict[str, object] | None:
    jd = get_job_jd(job_id)
    if jd is None:
        return None
    return {"status": "cached", **jd}


def get_or_enrich_job_jd(job_id: int) -> dict[str, object]:
    """Return JMM's canonical JD, fetching it once from the source when absent."""
    cached = _cached_result(job_id)
    if cached is not None:
        return cached

    with _ENRICH_LOCK:
        cached = _cached_result(job_id)
        if cached is not None:
            return cached

        job = get_job_by_id(job_id)
        if job is None:
            raise KeyError(f"job {job_id} not found")

        source = str(job.get("source") or "").strip().casefold()
        fetcher = _SOURCE_FETCHERS.get(source)
        if fetcher is None:
            raise UnsupportedJDSourceError(
                f"JD enrichment is not supported for source {source!r}"
            )

        fetched = fetcher(job)
        if not fetched.full_description:
            raise JDSourceFetchError("source adapter returned an empty JD")
        if not fetched.jd_source:
            raise JDSourceFetchError("source adapter returned no JD provenance")

        if fetched.facts:
            update_job_source_facts(job_id, **fetched.facts)

        stored = store_job_jd_once(
            job_id,
            full_description=fetched.full_description,
            jd_fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
            jd_source=fetched.jd_source,
        )
        return {"status": "enriched", **stored}
