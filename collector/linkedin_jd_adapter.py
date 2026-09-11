from __future__ import annotations

from collector.jd_enrichment import FetchedJD, JDSourceFetchError
from collector.linkedin_detail import LinkedInDetailError, fetch_linkedin_detail


def fetch_linkedin_jd_for_job(job: dict[str, object]) -> FetchedJD:
    canonical_url = str(job.get("canonical_url") or "").strip()
    if not canonical_url:
        raise JDSourceFetchError("LinkedIn job is missing canonical URL")
    try:
        detail = fetch_linkedin_detail(canonical_url)
    except LinkedInDetailError as exc:
        raise JDSourceFetchError(f"LinkedIn JD fetch failed: {exc}") from exc
    if not detail.full_description:
        raise JDSourceFetchError("LinkedIn job page did not expose a validated JD")
    return FetchedJD(
        full_description=detail.full_description,
        jd_source="linkedin_public_job_page",
        facts=detail.facts,
    )
