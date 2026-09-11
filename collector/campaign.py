from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

from collector.cursors import get_cursor
from collector.geographies import list_geographies
from collector.query_admin import list_queries
from collector.run_logging import collection_logger
from collector.settings import get_setting
from collector.source_campaign import get_cycle, get_or_start_cycle, set_cycle_status
from sources.linkedin_collector import (
    LINKEDIN_RESULT_CAP,
    LINKEDIN_TERMINAL_CURSOR_STATUSES,
    LinkedInFetchedPage,
    collect_linkedin_chunk,
    fetch_linkedin_geography_page,
    ingest_linkedin_geography_page,
)


@dataclass(frozen=True)
class CampaignStep:
    registry_key: str
    source: str
    query_text: str
    location: str
    status: str
    observed: int
    new_jobs: int
    detail: dict


@dataclass(frozen=True)
class LinkedInCampaignResult:
    status: str
    cycle_key: str | None
    geographies_total: int
    geographies_complete: int
    geographies_processed: int
    failed_geographies: int
    capped_geographies: int
    chunks_processed: int
    cards_observed: int
    unique_new_jobs: int
    duplicate_observations: int
    detail_attempted: int
    detail_stored: int
    detail_failed: int
    elapsed_seconds: float


def registry_runs(*, sources: set[str] | None = None) -> list[dict]:
    """Return optional/admin keyword runs; production LinkedIn discovery does not use them."""
    enabled_geographies = {row["code"] for row in list_geographies(enabled_only=True)}
    runs = []
    for row in list_queries(active_only=True):
        if sources and row["source"] not in sources:
            continue
        if row.get("geography_code") and row["geography_code"] not in enabled_geographies:
            continue
        runs.append(
            {
                "registry_key": row.get("registry_key") or f"db-query-{row['id']}",
                "query_id": row["id"],
                "query_text": row["query_text"],
                "source": row["source"],
                "location": row.get("location") or "",
                "geography_code": row.get("geography_code"),
                "last_run_at": row.get("last_run_at"),
            }
        )
    return runs


def run_one(
    run: dict,
    *,
    days: int | None = None,
    linkedin_cycle_key: str | None = None,
    should_stop: Callable[[], bool] | None = None,
    detail_attempted_ids: set[str] | None = None,
) -> CampaignStep:
    """Run one optional/admin query. The production LinkedIn market pass is geography-first."""
    source = run["source"]
    query_text = run["query_text"]
    location = run["location"]
    resolved_days = int(
        days if days is not None else get_setting("collection.default_freshness_days")
    )
    if source == "linkedin":
        if not linkedin_cycle_key:
            raise ValueError("linkedin_cycle_key is required for LinkedIn collection")
        result = collect_linkedin_chunk(
            query_text,
            location,
            geography_code=run.get("geography_code"),
            cycle_key=linkedin_cycle_key,
            days=resolved_days,
            should_stop=should_stop or (lambda: False),
            detail_attempted_ids=detail_attempted_ids,
        )
        return CampaignStep(
            run["registry_key"],
            source,
            query_text,
            location,
            result.status,
            result.cards_observed,
            result.unique_new_jobs,
            asdict(result),
        )
    return CampaignStep(
        run["registry_key"],
        source,
        query_text,
        location,
        "NOT_IMPLEMENTED",
        0,
        0,
        {"reason": f"no collector for {source}"},
    )


def linkedin_geography_runs() -> list[dict]:
    return [
        {
            "geography_code": row["code"],
            "location": row["linkedin_location"],
        }
        for row in list_geographies(enabled_only=True)
        if str(row.get("linkedin_location") or "").strip()
    ]


def _geography_status(run: dict, cycle_key: str) -> str:
    cursor = get_cursor("linkedin", "", run["location"])
    if str(cursor.get("cycle_key") or "") != cycle_key:
        return "PENDING"
    return str(cursor.get("status") or "PENDING")


def _geography_offset(run: dict, cycle_key: str) -> int:
    cursor = get_cursor("linkedin", "", run["location"])
    if str(cursor.get("cycle_key") or "") != cycle_key:
        return 0
    return int(cursor.get("cursor_value") or 0)


def _progress_counts(runs: list[dict], cycle_key: str) -> tuple[int, int, int]:
    statuses = [_geography_status(run, cycle_key) for run in runs]
    complete = sum(status == "COMPLETE" for status in statuses)
    capped = sum(status == "INCOMPLETE_CAP" for status in statuses)
    terminal = sum(status in LINKEDIN_TERMINAL_CURSOR_STATUSES for status in statuses)
    return complete, capped, terminal


def linkedin_campaign_progress() -> dict:
    state = get_cycle("linkedin")
    runs = linkedin_geography_runs()
    if state is None:
        return {
            "status": "NOT_RUN",
            "cycle_key": None,
            "geographies_total": len(runs),
            "geographies_complete": 0,
            "geographies_capped": 0,
            "geographies_remaining": len(runs),
        }
    cycle_key = str(state["cycle_key"])
    complete, capped, terminal = _progress_counts(runs, cycle_key)
    if runs and complete == len(runs):
        display_status = "COMPLETE"
    elif runs and terminal == len(runs) and capped:
        display_status = "INCOMPLETE_CAP"
    else:
        display_status = str(state["status"])
    return {
        "status": display_status,
        "cycle_key": cycle_key,
        "geographies_total": len(runs),
        "geographies_complete": complete,
        "geographies_capped": capped,
        "geographies_remaining": max(0, len(runs) - terminal),
        "started_at": state.get("started_at"),
        "updated_at": state.get("updated_at"),
        "completed_at": state.get("completed_at"),
    }


def run_linkedin_campaign(
    *,
    days: int | None = None,
    hours_old: int | None = None,
    desired_cycle_key: str | None = None,
    should_stop: Callable[[], bool],
    deadline_reached: Callable[[], bool],
) -> LinkedInCampaignResult:
    """Collect recent LinkedIn cards by geography only, without vacancy-detail fetches."""
    started = time.perf_counter()
    runs = linkedin_geography_runs()
    resolved_hours = int(
        hours_old
        if hours_old is not None
        else max(1, int(days if days is not None else get_setting("collection.default_freshness_days")))
        * 24
    )
    if resolved_hours < 1:
        raise ValueError("LinkedIn freshness window must be at least 1 hour")
    if not bool(get_setting("collection.linkedin_enabled")):
        return LinkedInCampaignResult(
            status="DISABLED",
            cycle_key=None,
            geographies_total=0,
            geographies_complete=0,
            geographies_processed=0,
            failed_geographies=0,
            capped_geographies=0,
            chunks_processed=0,
            cards_observed=0,
            unique_new_jobs=0,
            duplicate_observations=0,
            detail_attempted=0,
            detail_stored=0,
            detail_failed=0,
            elapsed_seconds=time.perf_counter() - started,
        )

    state = get_or_start_cycle("linkedin", desired_cycle_key=desired_cycle_key)
    cycle_key = str(state["cycle_key"])
    failed_codes: set[str] = set()
    touched_codes: set[str] = set()
    chunks = observed = new_jobs = duplicates = 0

    while not should_stop() and not deadline_reached():
        pending = [
            run
            for run in runs
            if run["geography_code"] not in failed_codes
            and _geography_status(run, cycle_key) not in LINKEDIN_TERMINAL_CURSOR_STATUSES
        ]
        if not pending:
            break
        pending.sort(key=lambda run: run["geography_code"])
        abort_fetch = lambda: should_stop() or deadline_reached()
        fetched_pages: list[tuple[dict, LinkedInFetchedPage]] = []
        with ThreadPoolExecutor(max_workers=min(3, len(pending))) as executor:
            future_to_run = {
                executor.submit(
                    fetch_linkedin_geography_page,
                    run["location"],
                    start_offset=_geography_offset(run, cycle_key),
                    hours_old=resolved_hours,
                    should_stop=abort_fetch,
                    max_results=LINKEDIN_RESULT_CAP,
                ): run
                for run in pending
            }
            for future in as_completed(future_to_run):
                run = future_to_run[future]
                try:
                    fetched_pages.append((run, future.result()))
                except InterruptedError:
                    continue
                except Exception as exc:  # noqa: BLE001 - geography remains retryable next run.
                    failed_codes.add(run["geography_code"])
                    collection_logger().error(
                        "LinkedIn geography fetch failed geography=%s location=%r error=%s",
                        run["geography_code"],
                        run["location"],
                        exc,
                    )

        if should_stop() or deadline_reached():
            break

        # Network work above may run concurrently, but all ingestion/cursor/dedupe
        # writes stay serialized in this coordinator thread.
        for run, fetched in sorted(
            fetched_pages, key=lambda item: item[0]["geography_code"]
        ):
            if should_stop() or deadline_reached():
                break
            try:
                cursor = get_cursor("linkedin", "", run["location"])
                result = ingest_linkedin_geography_page(
                    fetched,
                    run["location"],
                    geography_code=run["geography_code"],
                    cycle_key=cycle_key,
                    hours_old=resolved_hours,
                    should_stop=should_stop,
                    last_job_id=cursor.get("last_job_id"),
                )
            except InterruptedError:
                break
            except Exception as exc:  # noqa: BLE001 - geography remains retryable next run.
                failed_codes.add(run["geography_code"])
                collection_logger().error(
                    "LinkedIn geography ingest failed geography=%s location=%r error=%s",
                    run["geography_code"],
                    run["location"],
                    exc,
                )
                continue

            touched_codes.add(run["geography_code"])
            chunks += 1
            observed += result.cards_observed
            new_jobs += result.unique_new_jobs
            duplicates += result.duplicate_observations
            collection_logger().info(
                "LinkedIn geography progress cycle=%s geography=%s offset=%s status=%s observed=%s new=%s duplicates=%s",
                cycle_key,
                run["geography_code"],
                result.next_offset,
                result.status,
                result.cards_observed,
                result.unique_new_jobs,
                result.duplicate_observations,
            )

    complete, capped, terminal = _progress_counts(runs, cycle_key)
    if runs and complete == len(runs):
        status = "COMPLETE"
    elif runs and terminal == len(runs) and capped:
        status = "INCOMPLETE_CAP"
    elif should_stop():
        status = "STOPPED"
    elif deadline_reached():
        status = "PARTIAL_TIME_LIMIT"
    elif failed_codes:
        status = "PARTIAL_FAILURE"
    else:
        status = "PARTIAL"

    persisted_status = status if status in {"COMPLETE", "INCOMPLETE_CAP"} else "PARTIAL"
    set_cycle_status(
        "linkedin",
        cycle_key=cycle_key,
        status=persisted_status,
    )
    return LinkedInCampaignResult(
        status=status,
        cycle_key=cycle_key,
        geographies_total=len(runs),
        geographies_complete=complete,
        geographies_processed=len(touched_codes),
        failed_geographies=len(failed_codes),
        capped_geographies=capped,
        chunks_processed=chunks,
        cards_observed=observed,
        unique_new_jobs=new_jobs,
        duplicate_observations=duplicates,
        detail_attempted=0,
        detail_stored=0,
        detail_failed=0,
        elapsed_seconds=time.perf_counter() - started,
    )
