from __future__ import annotations

from collector.browser_broker import BrowserBrokerError, close_tab, open_tab
from collector.jd_enrichment import FetchedJD, JDSourceFetchError
from collector.run_logging import collection_logger
from collector.seek_jd import SeekJDFetchError, fetch_seek_detail_with_navigation_retry


def fetch_seek_jd_for_job(job: dict[str, object]) -> FetchedJD:
    """Fetch one SEEK JD using JMM's existing browser and proven SEEK parser."""
    source_job_id = str(job.get("source_job_id") or "").strip()
    canonical_url = str(job.get("canonical_url") or "").strip()
    if not source_job_id or not canonical_url:
        raise JDSourceFetchError("SEEK job is missing source identity or canonical URL")

    page_id: int | None = None
    try:
        page_id = int(open_tab("about:blank", active=False).result["pageId"])
        detail = fetch_seek_detail_with_navigation_retry(
            page_id,
            canonical_url,
            expected_source_job_id=source_job_id,
            job_id=int(job["id"]),
        )
    except (BrowserBrokerError, SeekJDFetchError, KeyError, TypeError, ValueError) as exc:
        raise JDSourceFetchError(f"SEEK JD fetch failed: {exc}") from exc
    finally:
        if page_id is not None:
            try:
                close_tab(page_id)
            except BrowserBrokerError as exc:
                collection_logger().warning(
                    "Could not close on-demand SEEK JD tab page_id=%s error=%s",
                    page_id,
                    exc,
                )

    return FetchedJD(
        full_description=detail.full_description,
        jd_source="seek_job_page",
        facts={
            key: value
            for key, value in detail.facts.items()
            if key != "source_job_id"
        },
    )
