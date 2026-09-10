from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import quote

from collector.browser_broker import BrowserBrokerError, navigate, open_tab, snapshot
from collector.ingest import ingest_card
from collector.run_log import finish_run, start_run
from collector.settings import get_setting
from sources.seek import SeekParseError, parse_seek_snapshot


@dataclass(frozen=True)
class SeekRunResult:
    run_id: int
    status: str
    pages_requested: int
    pages_parsed: int
    cards_observed: int
    unique_new_jobs: int
    duplicate_observations: int
    elapsed_seconds: float


def _seek_slug(text: str) -> str:
    # SEEK accepts hyphenated search terms in the path. Preserve useful punctuation conservatively.
    words = [part for part in text.strip().split() if part]
    return "-".join(quote(word, safe="") for word in words)


def build_seek_url(query_text: str, location: str, page: int = 1, days: int = 7) -> str:
    location_slug = "-".join(quote(part, safe="") for part in location.strip().split())
    base = f"https://www.seek.com.au/{_seek_slug(query_text)}-jobs/in-{location_slug}"
    params = f"?daterange={days}&sortmode=ListedDate"
    if page > 1:
        params += f"&page={page}"
    return base + params


def _wait_for_seek_cards(
    page_id: int,
    *,
    query_text: str,
    location: str,
    page_number: int,
    timeout_seconds: float | None = None,
) -> list:
    timeout_seconds = float(
        timeout_seconds
        if timeout_seconds is not None
        else get_setting("collection.seek_parse_wait_seconds")
    )
    deadline = time.monotonic() + timeout_seconds
    last_error: SeekParseError | None = None
    last_state: tuple[str, str] = ("", "")
    while time.monotonic() < deadline:
        snap = snapshot(page_id, verbose=True).result or {}
        last_state = (str(snap.get("title") or ""), str(snap.get("url") or ""))
        text = str(snap.get("text") or "")
        lowered = text.casefold()
        if (
            "captcha" in lowered
            or "security check" in lowered
            or "verify you are human" in lowered
        ):
            raise BrowserBrokerError(
                f"SEEK browser challenge on page {page_number}: {last_state[1]}"
            )
        if (
            "no matching search results" in lowered
            or "we couldn't find anything that matched your search" in lowered
        ):
            return []
        try:
            return parse_seek_snapshot(
                snap,
                query_text=query_text,
                query_location=location,
                page_number=page_number,
            )
        except SeekParseError as exc:
            last_error = exc
            time.sleep(0.75)
    title, url = last_state
    raise SeekParseError(
        f"SEEK page {page_number} never reached parseable card state within {timeout_seconds:.0f}s; "
        f"title={title!r} url={url!r}; last_error={last_error}"
    )


def collect_seek_query(
    query_text: str,
    location: str = "Sydney NSW",
    *,
    days: int | None = None,
    page_load_seconds: float | None = None,
    safety_page_limit: int | None = None,
    page_id: int | None = None,
) -> SeekRunResult:
    days = int(
        days if days is not None else get_setting("collection.default_freshness_days")
    )
    page_load_seconds = float(
        page_load_seconds
        if page_load_seconds is not None
        else get_setting("collection.seek_page_load_seconds")
    )
    safety_page_limit = int(
        safety_page_limit
        if safety_page_limit is not None
        else get_setting("collection.seek_safety_page_limit")
    )
    """Exhaust one SEEK query by cards only; stop when a page yields no new source IDs."""
    started = time.perf_counter()
    run_id = start_run("seek", query_text, location)
    pages_requested = pages_parsed = cards_observed = unique_new = duplicates = 0
    seen_this_run: set[str] = set()
    status = "FAILED"
    error: str | None = None

    try:
        first_url = build_seek_url(query_text, location, 1, days)
        if page_id is None:
            opened = open_tab(first_url, active=False)
            page_id = int(opened.result["pageId"])
        else:
            navigate(page_id, first_url)

        for page_number in range(1, safety_page_limit + 1):
            pages_requested += 1
            if page_number > 1:
                navigate(
                    page_id, build_seek_url(query_text, location, page_number, days)
                )
            if page_load_seconds > 0:
                time.sleep(page_load_seconds)
            cards = _wait_for_seek_cards(
                page_id,
                query_text=query_text,
                location=location,
                page_number=page_number,
            )

            if not cards:
                status = "COMPLETE"
                break
            ids = [card.source_job_id for card in cards if card.source_job_id]
            new_to_run = [job_id for job_id in ids if job_id not in seen_this_run]
            if not new_to_run:
                status = "COMPLETE"
                break

            pages_parsed += 1
            for card in cards:
                if card.source_job_id in seen_this_run:
                    continue
                seen_this_run.add(card.source_job_id or card.canonical_url)
                result = ingest_card(card)
                cards_observed += 1
                unique_new += int(result.created)
                duplicates += int(not result.created)
        else:
            raise RuntimeError(
                f"SEEK safety page limit {safety_page_limit} reached before source saturation; run is incomplete"
            )

    except (BrowserBrokerError, SeekParseError, RuntimeError, ValueError) as exc:
        error = str(exc)
        status = "FAILED"
        raise
    finally:
        finish_run(
            run_id,
            status=status,
            pages_requested=pages_requested,
            pages_parsed=pages_parsed,
            cards_observed=cards_observed,
            unique_new_jobs=unique_new,
            duplicate_observations=duplicates,
            error=error,
            metadata={
                "days": days,
                "page_id": page_id,
                "safety_page_limit": safety_page_limit,
            },
        )

    return SeekRunResult(
        run_id=run_id,
        status=status,
        pages_requested=pages_requested,
        pages_parsed=pages_parsed,
        cards_observed=cards_observed,
        unique_new_jobs=unique_new,
        duplicate_observations=duplicates,
        elapsed_seconds=time.perf_counter() - started,
    )
