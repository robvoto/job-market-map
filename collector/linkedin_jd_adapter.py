from __future__ import annotations

from collector.jd_enrichment import (
    FetchedJD,
    JDSourceFetchError,
    JDSourcePostingUnavailableError,
)
from collector.linkedin_detail import (
    LinkedInDetailError,
    LinkedInDetailUnavailableError,
    fetch_linkedin_detail,
)
from collector.source_status import TERMINAL_SOURCE_STATUSES


def fetch_linkedin_jd_for_job(job: dict[str, object]) -> FetchedJD:
    canonical_url = str(job.get("canonical_url") or "").strip()
    if not canonical_url:
        raise JDSourceFetchError("LinkedIn job is missing canonical URL")
    try:
        detail = fetch_linkedin_detail(canonical_url)
    except LinkedInDetailUnavailableError as exc:
        raise JDSourcePostingUnavailableError(
            str(exc), source_status=exc.source_status
        ) from exc
    except LinkedInDetailError as exc:
        raise JDSourceFetchError(f"LinkedIn JD fetch failed: {exc}") from exc

    status = str(detail.source_status or "").strip().casefold()
    if status in TERMINAL_SOURCE_STATUSES:
        raise JDSourcePostingUnavailableError(
            f"LinkedIn vacancy is unavailable ({status})",
            source_status=status,
        )
    if not detail.full_description:
        raise JDSourceFetchError("LinkedIn job page did not expose a validated JD")
    return FetchedJD(
        full_description=detail.full_description,
        jd_source="linkedin_public_job_page",
        facts=detail.facts,
    )
