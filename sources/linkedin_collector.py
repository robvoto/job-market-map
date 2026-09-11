from __future__ import annotations

import logging
import multiprocessing
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from collector.cursors import get_cursor, save_cursor
from collector.db import connect, store_job_jd_once
from collector.ingest import ingest_card
from collector.linkedin_detail import LinkedInDetailError, fetch_linkedin_detail
from collector.run_log import finish_run, start_run
from collector.run_logging import collection_logger
from collector.settings import get_setting
from sources.linkedin import (
    linkedin_identity_aliases,
    observation_from_jobspy_row,
)


class LinkedInCollectionError(RuntimeError):
    pass


LINKEDIN_PAGE_SIZE = 10
LINKEDIN_RESULT_CAP = 1000
LINKEDIN_PAGE_RETRY_ATTEMPTS = 3
LINKEDIN_TERMINAL_PROBE_PAGES = 3
LINKEDIN_TERMINAL_CURSOR_STATUSES = {"COMPLETE", "INCOMPLETE_CAP"}


@dataclass(frozen=True)
class LinkedInChunkResult:
    status: str
    cycle_key: str
    start_offset: int
    next_offset: int
    cards_observed: int
    unique_new_jobs: int
    duplicate_observations: int
    detail_attempted: int
    detail_stored: int
    detail_failed: int
    elapsed_seconds: float


class _JobSpyNoticeHandler(logging.Handler):
    def __init__(self, sink: list[tuple[int, str]]) -> None:
        super().__init__(level=logging.WARNING)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append((record.levelno, self.format(record)))


class _JobSpyProgressSession:
    def __init__(self, session, progress_send_conn) -> None:
        self._session = session
        self._progress_send_conn = progress_send_conn
        self._last_start = object()

    def __getattr__(self, name):
        return getattr(self._session, name)

    def get(self, *args, **kwargs):
        params = kwargs.get("params")
        start = params.get("start") if isinstance(params, dict) else None
        if start != self._last_start:
            try:
                self._progress_send_conn.send(start)
            except (BrokenPipeError, EOFError, OSError):
                pass
            self._last_start = start
        return self._session.get(*args, **kwargs)


def _jobpost_to_row(job) -> dict:
    compensation = getattr(job, "compensation", None)
    interval = getattr(compensation, "interval", None) if compensation else None
    job_types = getattr(job, "job_type", None) or []
    location = getattr(job, "location", None)
    return {
        "id": getattr(job, "id", None),
        "title": getattr(job, "title", None),
        "company": getattr(job, "company_name", None),
        "location": location.display_location() if location else None,
        "job_url": getattr(job, "job_url", None),
        "date_posted": getattr(job, "date_posted", None),
        "is_remote": getattr(job, "is_remote", None),
        "job_type": ", ".join(item.value[0] for item in job_types) if job_types else None,
        "min_amount": getattr(compensation, "min_amount", None) if compensation else None,
        "max_amount": getattr(compensation, "max_amount", None) if compensation else None,
        "currency": getattr(compensation, "currency", None) if compensation else None,
        "interval": getattr(interval, "value", None),
    }


def _fetch_exact_linkedin_page(search_params: dict, progress_send_conn) -> list[dict]:
    from bs4 import BeautifulSoup
    from jobspy.linkedin import LinkedIn

    scraper = LinkedIn()
    start = int(search_params.get("offset") or 0)
    try:
        progress_send_conn.send(start)
    except (BrokenPipeError, EOFError, OSError):
        pass
    params = {
        "keywords": search_params.get("search_term") or "",
        "location": search_params.get("location") or "",
        "distance": search_params.get("distance", 50),
        "pageNum": 0,
        "start": start,
        "f_TPR": f"r{int(search_params['hours_old']) * 3600}",
    }
    response = scraper.session.get(
        f"{scraper.base_url}/jobs-guest/jobs/api/seeMoreJobPostings/search?",
        params={key: value for key, value in params.items() if value is not None},
        timeout=10,
    )
    if response.status_code not in range(200, 400):
        raise LinkedInCollectionError(
            f"LinkedIn response status code {response.status_code} at offset {start}"
        )
    soup = BeautifulSoup(response.text, "html.parser")
    rows: list[dict] = []
    seen_ids: set[str] = set()
    for card in soup.find_all("div", class_="base-search-card"):
        href_tag = card.find("a", class_="base-card__full-link")
        href = str(href_tag.get("href") or "") if href_tag else ""
        job_id = href.split("?")[0].split("-")[-1] if href else ""
        if not job_id or job_id in seen_ids:
            continue
        seen_ids.add(job_id)
        job = scraper._process_job(card, job_id, False)
        if job is not None:
            rows.append(_jobpost_to_row(job))
    return rows


def _jobspy_worker(search_params: dict, send_conn, progress_send_conn) -> None:
    notices: list[tuple[int, str]] = []
    jobspy_logger = logging.getLogger("JobSpy:LinkedIn")
    handler = _JobSpyNoticeHandler(notices)
    jobspy_logger.addHandler(handler)
    original_create_session = None
    jobspy_linkedin = None
    try:
        if search_params.pop("_jmm_exact_page", False):
            send_conn.send(
                ("ok", _fetch_exact_linkedin_page(search_params, progress_send_conn), notices)
            )
            return
        import jobspy.linkedin as jobspy_linkedin
        from jobspy import scrape_jobs

        original_create_session = jobspy_linkedin.create_session

        def create_progress_session(*args, **kwargs):
            return _JobSpyProgressSession(
                original_create_session(*args, **kwargs), progress_send_conn
            )

        jobspy_linkedin.create_session = create_progress_session
        send_conn.send(("ok", scrape_jobs(**search_params), notices))
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - child boundary
        send_conn.send(("error", (type(exc).__name__, str(exc)), notices))
    finally:
        if jobspy_linkedin is not None and original_create_session is not None:
            jobspy_linkedin.create_session = original_create_session
        jobspy_logger.removeHandler(handler)
        progress_send_conn.close()
        send_conn.close()


def _fetch_jobspy_isolated(
    search_params: dict,
    *,
    should_stop: Callable[[], bool],
):
    ctx = multiprocessing.get_context("spawn")
    recv_conn, send_conn = ctx.Pipe(duplex=False)
    progress_recv_conn, progress_send_conn = ctx.Pipe(duplex=False)
    worker = ctx.Process(
        target=_jobspy_worker,
        args=(search_params, send_conn, progress_send_conn),
        daemon=True,
    )
    worker.start()
    send_conn.close()
    progress_send_conn.close()
    stall_seconds = float(get_setting("collection.linkedin_jobspy_stall_timeout_seconds"))
    last_progress = time.monotonic()
    progress_open = True
    while worker.is_alive():
        while progress_open and progress_recv_conn.poll():
            try:
                progress_recv_conn.recv()
            except EOFError:
                progress_open = False
                break
            last_progress = time.monotonic()
        if should_stop():
            worker.terminate()
            worker.join(1.0)
            progress_recv_conn.close()
            recv_conn.close()
            raise InterruptedError("LinkedIn JobSpy fetch stopped")
        if time.monotonic() - last_progress > stall_seconds:
            worker.terminate()
            worker.join(1.0)
            progress_recv_conn.close()
            recv_conn.close()
            raise TimeoutError(
                f"LinkedIn JobSpy made no pagination progress for {int(stall_seconds)}s"
            )
        worker.join(0.1)
    progress_recv_conn.close()
    if not recv_conn.poll(1.0):
        recv_conn.close()
        raise LinkedInCollectionError(
            f"LinkedIn JobSpy worker exited without results (exitcode={worker.exitcode})"
        )
    status, payload, notices = recv_conn.recv()
    recv_conn.close()
    log = collection_logger()
    fatal: list[str] = []
    for levelno, message in notices:
        log.log(max(logging.WARNING, int(levelno)), "[LinkedIn JobSpy] %s", message)
        if int(levelno) >= logging.ERROR:
            fatal.append(str(message))
    if status == "ok" and fatal:
        raise LinkedInCollectionError("LinkedIn JobSpy reported: " + " | ".join(fatal))
    if status == "ok":
        return payload
    error_type, error_message = payload
    suffix = f": {error_message}" if str(error_message).strip() else ""
    raise LinkedInCollectionError(f"{error_type}{suffix}")


def _fetch_exact_page_resilient(
    *,
    location: str,
    offset: int,
    hours_old: int,
    should_stop: Callable[[], bool],
    attempts: int = LINKEDIN_PAGE_RETRY_ATTEMPTS,
) -> tuple[list[dict], bool, int]:
    """Fetch one exact LinkedIn result page, retrying transient short responses.

    Returns the union of observed rows, whether any attempt produced a full source
    page, and the number of HTTP attempts made.
    """
    by_id: dict[str, dict] = {}
    full_page_seen = False
    attempts_made = 0
    for attempt in range(max(1, int(attempts))):
        rows = _fetch_jobspy_isolated(
            {
                "_jmm_exact_page": True,
                "search_term": "",
                "location": location,
                "hours_old": max(1, int(hours_old)),
                "offset": int(offset),
                "distance": 50,
            },
            should_stop=should_stop,
        )
        attempts_made += 1
        for row in rows or []:
            key = str(row.get("id") or row.get("job_url") or "").strip()
            if key:
                by_id.setdefault(key, row)
        if len(rows or []) >= LINKEDIN_PAGE_SIZE:
            full_page_seen = True
            break
        if attempt + 1 < attempts:
            time.sleep(0.5)
    return list(by_id.values()), full_page_seen, attempts_made


def collect_linkedin_geography_page(
    location: str,
    *,
    geography_code: str,
    cycle_key: str,
    hours_old: int,
    should_stop: Callable[[], bool],
    max_results: int = LINKEDIN_RESULT_CAP,
) -> LinkedInChunkResult:
    """Collect one exact LinkedIn geography page under JMM-owned pagination.

    LinkedIn's guest endpoint can transiently return short/empty pages before later
    offsets. JMM therefore retries the current page and probes subsequent offsets
    before declaring a geography exhausted.
    """
    started = time.perf_counter()
    cursor = get_cursor("linkedin", "", location)
    same_cycle = str(cursor.get("cycle_key") or "") == cycle_key
    start_offset = int(cursor.get("cursor_value") or 0) if same_cycle else 0
    cursor_status = str(cursor.get("status") or "")
    if same_cycle and cursor_status in LINKEDIN_TERMINAL_CURSOR_STATUSES:
        return LinkedInChunkResult(
            status=cursor_status,
            cycle_key=cycle_key,
            start_offset=start_offset,
            next_offset=start_offset,
            cards_observed=0,
            unique_new_jobs=0,
            duplicate_observations=0,
            detail_attempted=0,
            detail_stored=0,
            detail_failed=0,
            elapsed_seconds=time.perf_counter() - started,
        )
    if start_offset >= int(max_results):
        save_cursor(
            "linkedin",
            "",
            location,
            start_offset,
            status="INCOMPLETE_CAP",
            last_job_id=cursor.get("last_job_id") if same_cycle else None,
            cycle_key=cycle_key,
        )
        return LinkedInChunkResult(
            status="INCOMPLETE_CAP",
            cycle_key=cycle_key,
            start_offset=start_offset,
            next_offset=start_offset,
            cards_observed=0,
            unique_new_jobs=0,
            duplicate_observations=0,
            detail_attempted=0,
            detail_stored=0,
            detail_failed=0,
            elapsed_seconds=time.perf_counter() - started,
        )

    run_id = start_run("linkedin", "", location)
    observed = unique_new = duplicates = 0
    last_job_id = cursor.get("last_job_id") if same_cycle else None
    http_attempts = 0
    try:
        rows, full_page_seen, attempts = _fetch_exact_page_resilient(
            location=location,
            offset=start_offset,
            hours_old=hours_old,
            should_stop=should_stop,
        )
        http_attempts += attempts

        later_rows_seen = False
        if not full_page_seen:
            for page_delta in range(1, LINKEDIN_TERMINAL_PROBE_PAGES + 1):
                probe_offset = start_offset + page_delta * LINKEDIN_PAGE_SIZE
                if probe_offset >= int(max_results):
                    break
                probe_rows, _, probe_attempts = _fetch_exact_page_resilient(
                    location=location,
                    offset=probe_offset,
                    hours_old=hours_old,
                    should_stop=should_stop,
                    attempts=2,
                )
                http_attempts += probe_attempts
                if probe_rows:
                    later_rows_seen = True
                    break

        for row_index, row in enumerate(rows, start=1):
            if should_stop():
                raise InterruptedError("LinkedIn geography fetch stopped")
            observation = observation_from_jobspy_row(
                row,
                query_text="",
                query_location=location,
                geography_code=geography_code,
                rank=start_offset + row_index,
                offset=start_offset,
                page_size=LINKEDIN_PAGE_SIZE,
            )
            existing = _existing_job(observation.source_job_id or "")
            if existing is not None:
                observation.source_job_id = str(existing["source_job_id"])
            ingest_result = ingest_card(observation)
            observed += 1
            unique_new += int(ingest_result.created)
            duplicates += int(not ingest_result.created)
            last_job_id = observation.source_job_id

        next_offset = min(int(max_results), start_offset + LINKEDIN_PAGE_SIZE)
        if full_page_seen or later_rows_seen:
            status = "INCOMPLETE_CAP" if next_offset >= int(max_results) else "PARTIAL"
        else:
            status = "COMPLETE"
        save_cursor(
            "linkedin",
            "",
            location,
            next_offset,
            status=status,
            last_job_id=last_job_id,
            cycle_key=cycle_key,
        )
        finish_run(
            run_id,
            status=status,
            pages_requested=http_attempts,
            pages_parsed=1 if rows else 0,
            cards_observed=observed,
            unique_new_jobs=unique_new,
            duplicate_observations=duplicates,
            metadata={
                "cycle_key": cycle_key,
                "start_offset": start_offset,
                "next_offset": next_offset,
                "hours_old": hours_old,
                "exact_page_size": LINKEDIN_PAGE_SIZE,
                "full_page_seen": full_page_seen,
                "later_rows_seen": later_rows_seen,
                "http_attempts": http_attempts,
                "fetch_details": False,
            },
        )
        return LinkedInChunkResult(
            status=status,
            cycle_key=cycle_key,
            start_offset=start_offset,
            next_offset=next_offset,
            cards_observed=observed,
            unique_new_jobs=unique_new,
            duplicate_observations=duplicates,
            detail_attempted=0,
            detail_stored=0,
            detail_failed=0,
            elapsed_seconds=time.perf_counter() - started,
        )
    except InterruptedError:
        finish_run(
            run_id,
            status="STOPPED",
            pages_requested=http_attempts,
            pages_parsed=0,
            cards_observed=observed,
            unique_new_jobs=unique_new,
            duplicate_observations=duplicates,
            metadata={
                "cycle_key": cycle_key,
                "start_offset": start_offset,
                "hours_old": hours_old,
                "fetch_details": False,
            },
        )
        raise
    except Exception as exc:
        finish_run(
            run_id,
            status="FAILED",
            pages_requested=http_attempts,
            pages_parsed=0,
            cards_observed=observed,
            unique_new_jobs=unique_new,
            duplicate_observations=duplicates,
            error=str(exc),
            metadata={
                "cycle_key": cycle_key,
                "start_offset": start_offset,
                "hours_old": hours_old,
                "fetch_details": False,
            },
        )
        raise


def _existing_job(source_job_id: str) -> dict | None:
    aliases = linkedin_identity_aliases(source_job_id)
    if not aliases:
        return None
    placeholders = ",".join("?" for _ in aliases)
    with connect() as conn:
        row = conn.execute(
            f"SELECT * FROM jobs WHERE source='linkedin' AND source_job_id IN ({placeholders}) LIMIT 1",
            aliases,
        ).fetchone()
    return dict(row) if row else None


def collect_linkedin_chunk(
    query_text: str,
    location: str,
    *,
    geography_code: str | None,
    cycle_key: str,
    days: int,
    should_stop: Callable[[], bool],
    detail_attempted_ids: set[str] | None = None,
    results_wanted: int | None = None,
    fetch_details: bool = True,
    max_results: int | None = None,
) -> LinkedInChunkResult:
    started = time.perf_counter()
    detail_attempted_ids = detail_attempted_ids if detail_attempted_ids is not None else set()
    results_wanted = int(results_wanted if results_wanted is not None else LINKEDIN_PAGE_SIZE)
    cursor = get_cursor("linkedin", query_text, location)
    same_cycle = str(cursor.get("cycle_key") or "") == cycle_key
    start_offset = int(cursor.get("cursor_value") or 0) if same_cycle else 0
    cursor_status = str(cursor.get("status") or "")
    if same_cycle and cursor_status in LINKEDIN_TERMINAL_CURSOR_STATUSES:
        return LinkedInChunkResult(
            status=cursor_status,
            cycle_key=cycle_key,
            start_offset=start_offset,
            next_offset=start_offset,
            cards_observed=0,
            unique_new_jobs=0,
            duplicate_observations=0,
            detail_attempted=0,
            detail_stored=0,
            detail_failed=0,
            elapsed_seconds=time.perf_counter() - started,
        )
    if max_results is not None and start_offset >= int(max_results):
        save_cursor(
            "linkedin",
            query_text,
            location,
            start_offset,
            status="INCOMPLETE_CAP",
            last_job_id=cursor.get("last_job_id") if same_cycle else None,
            cycle_key=cycle_key,
        )
        return LinkedInChunkResult(
            status="INCOMPLETE_CAP",
            cycle_key=cycle_key,
            start_offset=start_offset,
            next_offset=start_offset,
            cards_observed=0,
            unique_new_jobs=0,
            duplicate_observations=0,
            detail_attempted=0,
            detail_stored=0,
            detail_failed=0,
            elapsed_seconds=time.perf_counter() - started,
        )

    run_id = start_run("linkedin", query_text, location)
    observed = unique_new = duplicates = 0
    detail_attempted = detail_stored = detail_failed = 0
    processed_rows = 0
    last_job_id = cursor.get("last_job_id") if same_cycle else None
    status = "PARTIAL"
    try:
        rows = _fetch_jobspy_isolated(
            {
                "site_name": ["linkedin"],
                "search_term": query_text,
                "location": location,
                "results_wanted": results_wanted,
                "hours_old": max(1, int(days)) * 24,
                "country_indeed": "Australia",
                "linkedin_fetch_description": False,
                "offset": start_offset,
                "verbose": 0,
            },
            should_stop=should_stop,
        )
        total_rows = 0 if rows is None else len(rows)
        if total_rows == 0:
            status = "COMPLETE"
        else:
            for row_index, (_, row) in enumerate(rows.iterrows(), start=1):
                if should_stop():
                    status = "STOPPED"
                    break
                observation = observation_from_jobspy_row(
                    row,
                    query_text=query_text,
                    query_location=location,
                    geography_code=geography_code,
                    rank=start_offset + row_index,
                    offset=start_offset,
                    page_size=results_wanted,
                )
                existing = _existing_job(observation.source_job_id or "")
                if existing is not None:
                    observation.source_job_id = str(existing["source_job_id"])

                detail = None
                if fetch_details:
                    native_key = next(
                        iter(linkedin_identity_aliases(observation.source_job_id or "")),
                        observation.source_job_id or observation.canonical_url,
                    )
                    needs_detail = existing is None or not str(existing.get("full_description") or "").strip()
                    if needs_detail and native_key not in detail_attempted_ids:
                        detail_attempted_ids.add(native_key)
                        detail_attempted += 1
                        try:
                            detail = fetch_linkedin_detail(observation.canonical_url)
                            observation.source_status = detail.source_status
                            observation.apply_method = detail.apply_method
                            observation.reposted = detail.reposted
                            observation.applicant_count = detail.applicant_count
                            observation.easy_apply = detail.easy_apply
                            if not detail.full_description:
                                detail_failed += 1
                        except LinkedInDetailError as exc:
                            detail_failed += 1
                            collection_logger().warning(
                                "LinkedIn detail fetch failed source_job_id=%s error=%s",
                                observation.source_job_id,
                                exc,
                            )

                ingest_result = ingest_card(observation)
                observed += 1
                unique_new += int(ingest_result.created)
                duplicates += int(not ingest_result.created)
                processed_rows += 1
                last_job_id = observation.source_job_id

                if detail is not None and detail.full_description:
                    store_job_jd_once(
                        ingest_result.job_id,
                        full_description=detail.full_description,
                        jd_fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
                        jd_source="linkedin_public_job_page",
                    )
                    detail_stored += 1

        next_offset = start_offset + processed_rows
        if status != "STOPPED":
            if total_rows < results_wanted:
                status = "COMPLETE"
            elif max_results is not None and next_offset >= int(max_results):
                status = "INCOMPLETE_CAP"
            else:
                status = "PARTIAL"
        save_cursor(
            "linkedin",
            query_text,
            location,
            next_offset,
            status=status,
            last_job_id=last_job_id,
            cycle_key=cycle_key,
        )
        finish_run(
            run_id,
            status=status,
            pages_requested=1,
            pages_parsed=1 if rows is not None else 0,
            cards_observed=observed,
            unique_new_jobs=unique_new,
            duplicate_observations=duplicates,
            metadata={
                "cycle_key": cycle_key,
                "start_offset": start_offset,
                "next_offset": next_offset,
                "results_wanted": results_wanted,
                "fetch_details": fetch_details,
                "max_results": max_results,
                "detail_attempted": detail_attempted,
                "detail_stored": detail_stored,
                "detail_failed": detail_failed,
            },
        )
        return LinkedInChunkResult(
            status=status,
            cycle_key=cycle_key,
            start_offset=start_offset,
            next_offset=next_offset,
            cards_observed=observed,
            unique_new_jobs=unique_new,
            duplicate_observations=duplicates,
            detail_attempted=detail_attempted,
            detail_stored=detail_stored,
            detail_failed=detail_failed,
            elapsed_seconds=time.perf_counter() - started,
        )
    except InterruptedError:
        finish_run(
            run_id,
            status="STOPPED",
            pages_requested=1,
            pages_parsed=0,
            cards_observed=observed,
            unique_new_jobs=unique_new,
            duplicate_observations=duplicates,
            metadata={
                "cycle_key": cycle_key,
                "start_offset": start_offset,
                "detail_attempted": detail_attempted,
                "detail_stored": detail_stored,
                "detail_failed": detail_failed,
            },
        )
        raise
    except Exception as exc:
        finish_run(
            run_id,
            status="FAILED",
            pages_requested=1,
            pages_parsed=0,
            cards_observed=observed,
            unique_new_jobs=unique_new,
            duplicate_observations=duplicates,
            error=str(exc),
            metadata={
                "cycle_key": cycle_key,
                "start_offset": start_offset,
                "detail_attempted": detail_attempted,
                "detail_stored": detail_stored,
                "detail_failed": detail_failed,
            },
        )
        raise
