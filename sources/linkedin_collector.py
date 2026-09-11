from __future__ import annotations

import logging
import multiprocessing
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from collector.cursors import get_cursor, save_cursor
from collector.db import (
    connect,
    get_job_by_id,
    get_primary_job_id,
    store_job_jd_once,
    update_job_source_facts,
)
from collector.duplicates import refresh_duplicate_links, refresh_job_fingerprints
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


def _jobspy_worker(search_params: dict, send_conn, progress_send_conn) -> None:
    notices: list[tuple[int, str]] = []
    jobspy_logger = logging.getLogger("JobSpy:LinkedIn")
    handler = _JobSpyNoticeHandler(notices)
    jobspy_logger.addHandler(handler)
    original_create_session = None
    jobspy_linkedin = None
    try:
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
) -> LinkedInChunkResult:
    started = time.perf_counter()
    detail_attempted_ids = detail_attempted_ids if detail_attempted_ids is not None else set()
    results_wanted = int(
        results_wanted
        if results_wanted is not None
        else get_setting("collection.linkedin_results_per_query")
    )
    cursor = get_cursor("linkedin", query_text, location)
    same_cycle = str(cursor.get("cycle_key") or "") == cycle_key
    start_offset = int(cursor.get("cursor_value") or 0) if same_cycle else 0
    if same_cycle and str(cursor.get("status")) == "COMPLETE":
        return LinkedInChunkResult(
            status="COMPLETE",
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

                native_key = next(
                    iter(linkedin_identity_aliases(observation.source_job_id or "")),
                    observation.source_job_id or observation.canonical_url,
                )
                # Ingest the card before detail work. This lets JMM detect a
                # confirmed repost and reuse the primary's JD instead of opening
                # the newer source posting again.
                ingest_result = ingest_card(observation)
                canonical_job = get_job_by_id(ingest_result.job_id)
                if canonical_job is None:
                    raise LinkedInCollectionError(
                        f"ingest returned missing primary job {ingest_result.job_id}"
                    )
                detail = None
                needs_detail = not str(canonical_job.get("full_description") or "").strip()
                if needs_detail and native_key not in detail_attempted_ids:
                    detail_attempted_ids.add(native_key)
                    detail_attempted += 1
                    try:
                        detail = fetch_linkedin_detail(observation.canonical_url)
                        if not detail.full_description:
                            detail_failed += 1
                    except LinkedInDetailError as exc:
                        detail_failed += 1
                        collection_logger().warning(
                            "LinkedIn detail fetch failed source_job_id=%s error=%s",
                            observation.source_job_id,
                            exc,
                        )

                if detail is not None:
                    observation_job_id = (
                        ingest_result.observation_job_id or ingest_result.job_id
                    )
                    update_job_source_facts(
                        observation_job_id,
                        source_status=detail.source_status,
                        apply_method=detail.apply_method,
                        reposted=detail.reposted,
                        applicant_count=detail.applicant_count,
                        easy_apply=detail.easy_apply,
                    )
                    refresh_job_fingerprints(observation_job_id)
                    refresh_duplicate_links(observation_job_id)
                    final_primary_id = get_primary_job_id(observation_job_id)
                    if final_primary_id != ingest_result.job_id:
                        if ingest_result.created:
                            with connect() as conn:
                                conn.execute(
                                    """
                                    UPDATE queries
                                       SET unique_new_jobs=MAX(unique_new_jobs-1, 0),
                                           duplicate_hits=duplicate_hits+1
                                     WHERE id=?
                                    """,
                                    (ingest_result.query_id,),
                                )
                        ingest_result = type(ingest_result)(
                            job_id=final_primary_id,
                            created=False,
                            query_id=ingest_result.query_id,
                            resurrected=ingest_result.resurrected,
                            observation_job_id=observation_job_id,
                        )
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

            if status != "STOPPED":
                status = "COMPLETE" if total_rows < results_wanted else "PARTIAL"

        next_offset = start_offset + processed_rows
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
