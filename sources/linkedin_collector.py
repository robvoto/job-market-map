from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import quote_plus

from collector.browser_broker import BrowserBrokerError, navigate, open_tab, snapshot
from collector.cursors import get_cursor, save_cursor
from collector.ingest import ingest_card
from collector.settings import get_setting
from sources.linkedin import LinkedInParseError, parse_linkedin_snapshot


@dataclass(frozen=True)
class LinkedInChunkResult:
    status: str
    start_offset: int
    next_offset: int
    offsets_scanned: int
    cards_observed: int
    unique_new_jobs: int
    duplicate_observations: int
    total_results_hint: int | None
    elapsed_seconds: float


def build_linkedin_url(
    query_text: str, location: str, *, offset: int = 0, days: int = 7
) -> str:
    seconds = days * 86400
    url = (
        "https://www.linkedin.com/jobs/search/?keywords="
        + quote_plus(query_text)
        + "&location="
        + quote_plus(location)
        + f"&f_TPR=r{seconds}&sortBy=DD"
    )
    if offset:
        url += f"&start={offset}"
    return url


def _wait_for_cards(
    page_id: int,
    *,
    query_text: str,
    location: str,
    offset: int,
    timeout_seconds: float | None = None,
    geography_code: str | None = None,
):
    timeout_seconds = float(
        timeout_seconds
        if timeout_seconds is not None
        else get_setting("collection.linkedin_parse_wait_seconds")
    )
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        snap = snapshot(page_id, verbose=True).result or {}
        text = str(snap.get("text") or "")
        lowered = text.casefold()
        if (
            "security verification" in lowered
            or "verify you are human" in lowered
            or "captcha" in lowered
        ):
            raise BrowserBrokerError(f"LinkedIn browser challenge at offset {offset}")
        try:
            return parse_linkedin_snapshot(
                snap, query_text=query_text, query_location=location, offset=offset
            )
        except LinkedInParseError as exc:
            last_error = exc
            # Beyond the result boundary LinkedIn may leave a valid search shell with no cards.
            if "no matching jobs" in lowered or "0 results" in lowered:
                return [], None
            time.sleep(0.75)
    raise LinkedInParseError(
        f"LinkedIn offset {offset} never reached parseable card state: {last_error}"
    )


def collect_linkedin_chunk(
    query_text: str,
    location: str = "Sydney NSW",
    *,
    days: int | None = None,
    max_offsets: int | None = None,
    initial_wait_seconds: float | None = None,
    reset: bool = False,
    page_id: int | None = None,
    geography_code: str | None = None,
) -> LinkedInChunkResult:
    days = int(
        days if days is not None else get_setting("collection.default_freshness_days")
    )
    max_offsets = int(
        max_offsets
        if max_offsets is not None
        else get_setting("collection.linkedin_chunk_offsets")
    )
    initial_wait_seconds = float(
        initial_wait_seconds
        if initial_wait_seconds is not None
        else get_setting("collection.linkedin_initial_wait_seconds")
    )
    """Collect a bounded, resumable LinkedIn card-only chunk using arbitrary start offsets."""
    started = time.perf_counter()
    cursor = (
        {"cursor_value": 0, "status": "PENDING"}
        if reset
        else get_cursor("linkedin", query_text, location)
    )
    start_offset = int(cursor.get("cursor_value") or 0)
    if not reset and cursor.get("status") == "COMPLETE":
        return LinkedInChunkResult(
            "COMPLETE",
            start_offset,
            start_offset,
            0,
            0,
            0,
            0,
            cursor.get("total_results_hint"),
            time.perf_counter() - started,
        )

    first_url = build_linkedin_url(query_text, location, offset=start_offset, days=days)
    if page_id is None:
        page_id = int(open_tab(first_url, active=False).result["pageId"])
    else:
        navigate(page_id, first_url)
    offset = start_offset
    offsets_scanned = cards_observed = unique_new = duplicates = 0
    seen_chunk: set[str] = set()
    total_hint = cursor.get("total_results_hint")
    status = "RUNNING"
    last_id = cursor.get("last_job_id")

    for index in range(max_offsets):
        if index > 0:
            navigate(
                page_id,
                build_linkedin_url(query_text, location, offset=offset, days=days),
            )
        if initial_wait_seconds > 0:
            time.sleep(initial_wait_seconds)
        cards, hint = _wait_for_cards(
            page_id,
            query_text=query_text,
            location=location,
            offset=offset,
            geography_code=geography_code,
        )
        if hint is not None:
            total_hint = hint
        if not cards:
            status = "COMPLETE"
            break

        ids = [c.source_job_id for c in cards if c.source_job_id]
        new_ids = [job_id for job_id in ids if job_id not in seen_chunk]
        if not new_ids:
            status = "COMPLETE"
            break
        offsets_scanned += 1
        for card in cards:
            if card.source_job_id in seen_chunk:
                continue
            seen_chunk.add(card.source_job_id or card.canonical_url)
            result = ingest_card(card)
            cards_observed += 1
            unique_new += int(result.created)
            duplicates += int(not result.created)

        offset += len(cards)
        last_id = ids[-1] if ids else None
        if total_hint is not None and offset >= total_hint:
            status = "COMPLETE"
            save_cursor(
                "linkedin",
                query_text,
                location,
                offset,
                status=status,
                last_job_id=last_id,
                total_results_hint=total_hint,
            )
            break
        save_cursor(
            "linkedin",
            query_text,
            location,
            offset,
            status="RUNNING",
            last_job_id=last_id,
            total_results_hint=total_hint,
        )
    else:
        status = "PARTIAL"

    save_cursor(
        "linkedin",
        query_text,
        location,
        offset,
        status=status,
        last_job_id=last_id,
        total_results_hint=total_hint,
    )
    return LinkedInChunkResult(
        status=status,
        start_offset=start_offset,
        next_offset=offset,
        offsets_scanned=offsets_scanned,
        cards_observed=cards_observed,
        unique_new_jobs=unique_new,
        duplicate_observations=duplicates,
        total_results_hint=total_hint,
        elapsed_seconds=time.perf_counter() - started,
    )
