from __future__ import annotations

from dataclasses import asdict, dataclass

from collector.query_admin import list_queries
from collector.settings import get_setting
from sources.linkedin_collector import collect_linkedin_chunk
from sources.seek_collector import collect_seek_query


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


def registry_runs(*, sources: set[str] | None = None) -> list[dict]:
    runs = []
    for row in list_queries(active_only=True):
        if sources and row["source"] not in sources:
            continue
        runs.append(
            {
                "registry_key": row.get("registry_key") or f"db-query-{row['id']}",
                "query_id": row["id"],
                "query_text": row["query_text"],
                "source": row["source"],
                "location": row.get("location") or "",
            }
        )
    return runs


def run_one(
    run: dict,
    *,
    linkedin_max_offsets: int | None = None,
    days: int | None = None,
    page_id: int | None = None,
) -> CampaignStep:
    source = run["source"]
    query_text = run["query_text"]
    location = run["location"]
    resolved_days = int(
        days if days is not None else get_setting("collection.default_freshness_days")
    )
    if source == "seek":
        result = collect_seek_query(
            query_text, location, days=resolved_days, page_id=page_id
        )
        detail = asdict(result)
        return CampaignStep(
            run["registry_key"],
            source,
            query_text,
            location,
            result.status,
            result.cards_observed,
            result.unique_new_jobs,
            detail,
        )
    if source == "linkedin":
        offsets = int(
            linkedin_max_offsets
            if linkedin_max_offsets is not None
            else get_setting("collection.linkedin_chunk_offsets")
        )
        result = collect_linkedin_chunk(
            query_text,
            location,
            days=resolved_days,
            max_offsets=offsets,
            page_id=page_id,
        )
        detail = asdict(result)
        return CampaignStep(
            run["registry_key"],
            source,
            query_text,
            location,
            result.status,
            result.cards_observed,
            result.unique_new_jobs,
            detail,
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


def run_steps_in_one_browser_tab(
    runs: list[dict],
    *,
    linkedin_max_offsets: int | None = None,
    days: int | None = None,
) -> list[CampaignStep]:
    """Open one workflow tab in Rob's existing Chrome and reuse it for every step.

    This owns a tab, not a browser/profile. It deliberately avoids one-tab-per-query churn.
    """
    from collector.browser_broker import open_tab

    if not runs:
        return []
    page_id = int(open_tab("about:blank", active=False).result["pageId"])
    return [
        run_one(
            run,
            linkedin_max_offsets=linkedin_max_offsets,
            days=days,
            page_id=page_id,
        )
        for run in runs
    ]
