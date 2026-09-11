from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from collector.cursors import get_cursor
from collector.geographies import list_geographies
from collector.query_admin import list_queries
from collector.run_logging import collection_logger
from collector.settings import get_setting
from collector.source_campaign import get_cycle, get_or_start_cycle, set_cycle_status
from sources.linkedin_collector import collect_linkedin_chunk


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
    queries_total: int
    queries_complete: int
    queries_processed: int
    failed_queries: int
    cards_observed: int
    unique_new_jobs: int
    duplicate_observations: int
    detail_attempted: int
    detail_stored: int
    detail_failed: int
    elapsed_seconds: float


def registry_runs(*, sources: set[str] | None = None) -> list[dict]:
    enabled_geographies = {row["code"] for row in list_geographies(enabled_only=True)}
    runs = []
    for row in list_queries(active_only=True):
        if sources and row["source"] not in sources:
            continue
        if (
            row.get("geography_code")
            and row["geography_code"] not in enabled_geographies
        ):
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


def _query_complete(run: dict, cycle_key: str) -> bool:
    cursor = get_cursor("linkedin", run["query_text"], run["location"])
    return (
        str(cursor.get("cycle_key") or "") == cycle_key
        and str(cursor.get("status") or "") == "COMPLETE"
    )


def linkedin_campaign_progress() -> dict:
    state = get_cycle("linkedin")
    runs = registry_runs(sources={"linkedin"})
    if state is None:
        return {
            "status": "NOT_RUN",
            "cycle_key": None,
            "queries_total": len(runs),
            "queries_complete": 0,
            "queries_remaining": len(runs),
        }
    cycle_key = str(state["cycle_key"])
    complete = sum(_query_complete(run, cycle_key) for run in runs)
    return {
        "status": state["status"],
        "cycle_key": cycle_key,
        "queries_total": len(runs),
        "queries_complete": complete,
        "queries_remaining": max(0, len(runs) - complete),
        "started_at": state.get("started_at"),
        "updated_at": state.get("updated_at"),
        "completed_at": state.get("completed_at"),
    }


def run_linkedin_campaign(
    *,
    days: int,
    should_stop: Callable[[], bool],
    deadline_reached: Callable[[], bool],
) -> LinkedInCampaignResult:
    started = time.perf_counter()
    if not bool(get_setting("collection.linkedin_enabled")):
        return LinkedInCampaignResult(
            status="DISABLED",
            cycle_key=None,
            queries_total=0,
            queries_complete=0,
            queries_processed=0,
            failed_queries=0,
            cards_observed=0,
            unique_new_jobs=0,
            duplicate_observations=0,
            detail_attempted=0,
            detail_stored=0,
            detail_failed=0,
            elapsed_seconds=time.perf_counter() - started,
        )

    state = get_or_start_cycle("linkedin")
    cycle_key = str(state["cycle_key"])
    detail_attempted_ids: set[str] = set()
    failed_query_ids: set[int] = set()
    consecutive_failures = 0
    max_consecutive_failures = int(
        get_setting("collection.linkedin_max_consecutive_query_failures")
    )
    processed = observed = new_jobs = duplicates = 0
    detail_attempted = detail_stored = detail_failed = 0
    log = collection_logger()

    while not should_stop() and not deadline_reached():
        runs = registry_runs(sources={"linkedin"})
        pending = [
            run
            for run in runs
            if int(run["query_id"]) not in failed_query_ids
            and not _query_complete(run, cycle_key)
        ]
        if not pending:
            break
        pending.sort(key=lambda run: (str(run.get("last_run_at") or ""), int(run["query_id"])))
        run = pending[0]
        try:
            step = run_one(
                run,
                days=days,
                linkedin_cycle_key=cycle_key,
                should_stop=should_stop,
                detail_attempted_ids=detail_attempted_ids,
            )
        except InterruptedError:
            break
        except Exception as exc:  # noqa: BLE001 - source query remains retryable next run.
            failed_query_ids.add(int(run["query_id"]))
            consecutive_failures += 1
            log.error(
                "LinkedIn query failed query_id=%s query=%r location=%r error=%s",
                run["query_id"],
                run["query_text"],
                run["location"],
                exc,
            )
            if consecutive_failures >= max_consecutive_failures:
                log.error(
                    "LinkedIn circuit breaker opened after %s consecutive query failures",
                    consecutive_failures,
                )
                break
            continue

        consecutive_failures = 0
        processed += 1
        observed += int(step.detail.get("cards_observed", 0))
        new_jobs += int(step.detail.get("unique_new_jobs", 0))
        duplicates += int(step.detail.get("duplicate_observations", 0))
        detail_attempted += int(step.detail.get("detail_attempted", 0))
        detail_stored += int(step.detail.get("detail_stored", 0))
        detail_failed += int(step.detail.get("detail_failed", 0))
        log.info(
            "LinkedIn progress cycle=%s query_id=%s status=%s observed=%s new=%s detail_attempted=%s detail_stored=%s detail_failed=%s",
            cycle_key,
            run["query_id"],
            step.status,
            step.observed,
            step.new_jobs,
            step.detail.get("detail_attempted", 0),
            step.detail.get("detail_stored", 0),
            step.detail.get("detail_failed", 0),
        )

    final_runs = registry_runs(sources={"linkedin"})
    complete = sum(_query_complete(run, cycle_key) for run in final_runs)
    if complete == len(final_runs):
        status = "COMPLETE"
    elif should_stop():
        status = "STOPPED"
    elif deadline_reached():
        status = "PARTIAL_TIME_LIMIT"
    elif failed_query_ids:
        status = "PARTIAL_FAILURE"
    else:
        status = "PARTIAL"
    set_cycle_status(
        "linkedin",
        cycle_key=cycle_key,
        status="COMPLETE" if status == "COMPLETE" else "PARTIAL",
    )
    return LinkedInCampaignResult(
        status=status,
        cycle_key=cycle_key,
        queries_total=len(final_runs),
        queries_complete=complete,
        queries_processed=processed,
        failed_queries=len(failed_query_ids),
        cards_observed=observed,
        unique_new_jobs=new_jobs,
        duplicate_observations=duplicates,
        detail_attempted=detail_attempted,
        detail_stored=detail_stored,
        detail_failed=detail_failed,
        elapsed_seconds=time.perf_counter() - started,
    )
