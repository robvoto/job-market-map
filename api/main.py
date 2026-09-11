from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from collector.backup import create_backup, list_backups
from collector.browser_broker import (
    BrowserBrokerError,
    focus_or_open_tab,
    persistent_browser_ready,
)
from collector.campaign import linkedin_campaign_progress
from collector.consumers import advance_checkpoint, get_checkpoint
from collector.db import (
    ROOT,
    connect,
    get_job_by_identity_key,
    get_job_by_source_id,
    init_db,
)
from collector.geographies import (
    list_geographies,
    seed_geographies,
    set_geography_enabled,
)
from collector.jd_enrichment import (
    JDSourceFetchError,
    UnsupportedJDSourceError,
    get_or_enrich_job_jd,
)
from collector.query_admin import add_query, set_query_active
from collector.query_admin import list_queries as admin_list_queries
from collector.query_registry import sync_registry
from collector.retention import apply_retention
from collector.run_logging import read_collection_log_tail
from collector.run_stats import population_stats
from collector.scheduler import SCHEDULER
from collector.seek_cycle import enabled_state_codes
from collector.service_manager import PROCESS_MANAGER, CollectionProcessError
from collector.service_state import bootstrap_market_run, latest_market_run
from collector.settings import (
    SettingError,
    get_setting,
    list_settings,
    reset_setting,
    seed_settings,
    set_setting,
)

API_VERSION = "v3"
SCHEMA_VERSION = 8
ADMIN_HTML = ROOT / "api" / "admin.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    seed_settings()
    seed_geographies()
    sync_registry()
    SCHEDULER.start()
    try:
        yield
    finally:
        SCHEDULER.stop()


app = FastAPI(
    title="Job Market Map",
    version="0.4.0",
    description="Neutral local job-market feed for Job Hunter, Reset / Edge, Plan Z and other consumers.",
    lifespan=lifespan,
)


class SettingUpdate(BaseModel):
    value: bool | int | float
    actor: str = Field(default="rob", max_length=100)


class QueryToggle(BaseModel):
    active: bool


class GeographyToggle(BaseModel):
    enabled: bool
    actor: str = Field(default="rob", max_length=100)


class CheckpointUpdate(BaseModel):
    last_job_id: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=500)


class QueryCreate(BaseModel):
    source: str = Field(min_length=1, max_length=40)
    query_text: str = Field(min_length=1, max_length=300)
    location: str = Field(default="New South Wales NSW", max_length=200)
    geography_code: str | None = Field(default=None, max_length=10)
    registry_key: str | None = Field(default=None, max_length=200)
    origins: list[str] = Field(default_factory=lambda: ["admin"])
    active: bool = True


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _tags(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _job_payload(row, *, include_raw: bool = True) -> dict:
    item = dict(row)
    item["card_tags"] = _tags(item.pop("card_tags_json", None))
    for key in (
        "reposted",
        "easy_apply",
        "archived",
    ):
        if item.get(key) is not None:
            item[key] = bool(item[key])
    if not include_raw:
        item.pop("raw_card_text", None)
    return item


def _limit(requested: int | None) -> int:
    default = int(get_setting("api.default_page_size"))
    maximum = int(get_setting("api.max_page_size"))
    if requested is None:
        return default
    if requested < 1:
        raise HTTPException(400, "limit must be >= 1")
    if requested > maximum:
        raise HTTPException(400, f"limit exceeds admin maximum of {maximum}")
    return requested


@app.get("/", include_in_schema=False)
def root():
    return {
        "service": "Job Market Map",
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "docs": "/docs",
        "admin": "/admin",
    }


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page():
    return ADMIN_HTML.read_text(encoding="utf-8")


@app.get(f"/{API_VERSION}/health")
@app.get("/health", include_in_schema=False)
def health():
    with connect() as conn:
        conn.execute("SELECT 1").fetchone()
    return {"ok": True, "api_version": API_VERSION, "schema_version": SCHEMA_VERSION}


@app.get(f"/{API_VERSION}/stats")
@app.get("/stats", include_in_schema=False)
def stats():
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS jobs,
                   SUM(CASE WHEN COALESCE(s.archived,0)=0 THEN 1 ELSE 0 END) AS active_jobs
              FROM jobs j
              LEFT JOIN job_observation_state s ON s.job_id=j.id
            """
        ).fetchone()
        result = dict(row)
        result.update(
            card_captures=conn.execute("SELECT COUNT(*) FROM card_captures").fetchone()[
                0
            ],
            queries=conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0],
            duplicate_links=conn.execute(
                "SELECT COUNT(*) FROM duplicate_links"
            ).fetchone()[0],
            tombstones=conn.execute("SELECT COUNT(*) FROM job_tombstones").fetchone()[
                0
            ],
        )
    return {"api_version": API_VERSION, "schema_version": SCHEMA_VERSION, **result}


@app.get(f"/{API_VERSION}/feed/jobs")
def job_feed(
    after_id: int = Query(
        0,
        ge=0,
        description="Stable cursor: return current job rows with id greater than this value.",
    ),
    limit: int | None = Query(None, ge=1),
    source: str | None = None,
    geography_code: str | None = None,
    include_archived: bool = False,
    include_raw: bool = True,
):
    resolved_limit = _limit(limit)
    # Confirmed same-vacancy source postings remain lookup-addressable, but only
    # their oldest deterministic primary enters downstream processing feeds.
    clauses = ["j.id > ?", "j.primary_job_id IS NULL"]
    params: list[object] = [after_id]
    if source:
        clauses.append("j.source=?")
        params.append(source.casefold())
    if geography_code:
        clauses.append("j.geography_code=?")
        params.append(geography_code.upper())
    if not include_archived:
        clauses.append("COALESCE(s.archived,0)=0")
    where = " AND ".join(clauses)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT j.*, s.first_seen_at, s.last_seen_at, s.capture_count,
                   s.archived, s.compacted_at,
                   (SELECT COUNT(*) FROM duplicate_links d
                     WHERE d.job_id_a=j.id OR d.job_id_b=j.id) AS duplicate_link_count
              FROM jobs j
              LEFT JOIN job_observation_state s ON s.job_id=j.id
             WHERE {where}
             ORDER BY j.id ASC
             LIMIT ?
            """,
            (*params, resolved_limit + 1),
        ).fetchall()
    has_more = len(rows) > resolved_limit
    rows = rows[:resolved_limit]
    items = [_job_payload(row, include_raw=include_raw) for row in rows]
    next_cursor = int(rows[-1]["id"]) if rows else after_id
    return {
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


@app.get(f"/{API_VERSION}/consumers/{{consumer_key}}/state")
def consumer_state(consumer_key: str):
    return get_checkpoint(consumer_key)


@app.get(f"/{API_VERSION}/consumers/{{consumer_key}}/feed")
def consumer_feed(
    consumer_key: str,
    limit: int | None = Query(None, ge=1),
    geography_code: str | None = None,
    source: str | None = None,
    include_raw: bool = True,
):
    checkpoint = get_checkpoint(consumer_key)
    return job_feed(
        after_id=int(checkpoint["last_job_id"]),
        limit=limit,
        source=source,
        geography_code=geography_code,
        include_archived=False,
        include_raw=include_raw,
    )


@app.post(f"/{API_VERSION}/consumers/{{consumer_key}}/checkpoint")
def consumer_checkpoint(consumer_key: str, update: CheckpointUpdate):
    try:
        return advance_checkpoint(consumer_key, update.last_job_id, note=update.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get(f"/{API_VERSION}/jobs/new")
@app.get("/jobs/new", include_in_schema=False)
def new_jobs(days: int = Query(1, ge=0, le=30), limit: int | None = Query(None, ge=1)):
    resolved_limit = _limit(limit)
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT j.*, s.first_seen_at, s.last_seen_at, s.capture_count, s.archived, s.compacted_at
              FROM jobs j
              JOIN job_observation_state s ON s.job_id=j.id
             WHERE s.first_seen_at>=? AND j.primary_job_id IS NULL
             ORDER BY s.first_seen_at DESC LIMIT ?
            """,
            (cutoff, resolved_limit),
        ).fetchall()
    return [_job_payload(row) for row in rows]


@app.get(f"/{API_VERSION}/jobs/search")
@app.get("/jobs/search", include_in_schema=False)
def search_jobs(
    q: str | None = None,
    source: str | None = None,
    geography_code: str | None = None,
    include_archived: bool = False,
    limit: int | None = Query(None, ge=1),
):
    resolved_limit = _limit(limit)
    clauses = ["j.primary_job_id IS NULL"]
    params: list[object] = []
    if q:
        clauses.append("(j.title LIKE ? OR j.employer LIKE ? OR j.raw_card_text LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if source:
        clauses.append("j.source=?")
        params.append(source.casefold())
    if geography_code:
        clauses.append("j.geography_code=?")
        params.append(geography_code.upper())
    if not include_archived:
        clauses.append("COALESCE(s.archived,0)=0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT j.*, s.first_seen_at, s.last_seen_at, s.capture_count, s.archived, s.compacted_at
              FROM jobs j
              LEFT JOIN job_observation_state s ON s.job_id=j.id
              {where}
             ORDER BY s.first_seen_at DESC LIMIT ?
            """,
            (*params, resolved_limit),
        ).fetchall()
    return [_job_payload(row) for row in rows]


@app.post(f"/{API_VERSION}/jobs/{{job_id}}/jd")
def job_jd(job_id: int):
    """Return the canonical JD, enriching it through JMM when it is missing."""
    try:
        result = get_or_enrich_job_jd(job_id)
    except KeyError:
        raise HTTPException(404, "job not found") from None
    except UnsupportedJDSourceError as exc:
        raise HTTPException(422, str(exc)) from None
    except JDSourceFetchError as exc:
        raise HTTPException(502, str(exc)) from None
    return {
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        **result,
    }


def _job_detail_payload(job_id: int) -> dict:
    """Build the canonical job-detail response: job facts, captures, query hits and duplicate evidence."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT j.*, s.first_seen_at, s.last_seen_at, s.capture_count, s.archived, s.compacted_at
              FROM jobs j
              LEFT JOIN job_observation_state s ON s.job_id=j.id
             WHERE j.id=?
            """,
            (job_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "job not found")
        captures = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM card_captures WHERE job_id=? ORDER BY captured_at DESC LIMIT 20",
                (job_id,),
            )
        ]
        query_hits = [
            dict(r)
            for r in conn.execute(
                """
            SELECT q.id AS query_id, q.registry_key, q.source, q.query_text, q.location,
                   h.first_seen_at, h.last_seen_at, h.hit_count
              FROM job_query_hits h JOIN queries q ON q.id=h.query_id
             WHERE h.job_id=? ORDER BY h.hit_count DESC, q.query_text
            """,
                (job_id,),
            )
        ]
        duplicates = [
            dict(r)
            for r in conn.execute(
                """
            SELECT d.confidence, d.match_type, d.reasons_json,
                   CASE WHEN d.job_id_a=? THEN d.job_id_b ELSE d.job_id_a END AS other_job_id
              FROM duplicate_links d
             WHERE d.job_id_a=? OR d.job_id_b=?
             ORDER BY d.confidence DESC
            """,
                (job_id, job_id, job_id),
            )
        ]
        same_vacancy = [
            dict(r)
            for r in conn.execute(
                """
                SELECT l.job_id, l.primary_job_id, l.confidence, l.match_type,
                       l.matching_signals_json, l.detected_at
                  FROM same_vacancy_links l
                 WHERE l.job_id=? OR l.primary_job_id=?
                 ORDER BY l.primary_job_id, l.job_id
                """,
                (job_id, job_id),
            )
        ]
    for dup in duplicates:
        dup["reasons"] = json.loads(dup.pop("reasons_json"))
    for link in same_vacancy:
        link["matching_signals"] = json.loads(link.pop("matching_signals_json"))
    return {
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "job": _job_payload(row),
        "captures": captures,
        "query_hits": query_hits,
        "duplicates": duplicates,
        "same_vacancy": same_vacancy,
    }


@app.get(f"/{API_VERSION}/jobs/lookup")
def lookup_job(
    identity_key: str | None = Query(
        None, description="Exact stable identity_key issued by Job Market Map."
    ),
    source: str | None = Query(
        None, description="Exact source name, paired with source_job_id."
    ),
    source_job_id: str | None = Query(
        None, description="Exact source-native job ID, paired with source."
    ),
):
    """Resolve a known source vacancy to its JMM record by exact identity only.

    Never falls back to title/employer/raw-text search, fuzzy matching or URL
    similarity. Exactly one of identity_key or (source + source_job_id) is required.
    """
    if identity_key is not None:
        if source is not None or source_job_id is not None:
            raise HTTPException(
                400, "identity_key cannot be combined with source/source_job_id"
            )
        if not identity_key.strip():
            raise HTTPException(400, "identity_key must not be blank")
        found = get_job_by_identity_key(identity_key)
    elif source is not None or source_job_id is not None:
        if source is None or source_job_id is None:
            raise HTTPException(
                400, "source and source_job_id must both be provided together"
            )
        if not source.strip() or not source_job_id.strip():
            raise HTTPException(400, "source and source_job_id must not be blank")
        found = get_job_by_source_id(source, source_job_id)
    else:
        raise HTTPException(
            400, "identity_key or source+source_job_id is required"
        )
    if not found:
        raise HTTPException(404, "job not found")
    return _job_detail_payload(int(found["id"]))


@app.get(f"/{API_VERSION}/jobs/{{job_id}}")
@app.get("/jobs/{job_id}", include_in_schema=False)
def get_job(job_id: int):
    return _job_detail_payload(job_id)


@app.get(f"/{API_VERSION}/runs")
@app.get("/runs", include_in_schema=False)
def recent_runs(limit: int = Query(100, ge=1, le=1000)):
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM collection_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
        ]


@app.get(f"/{API_VERSION}/admin/settings")
def admin_settings():
    return {"settings": list_settings()}


@app.put(f"/{API_VERSION}/admin/settings/{{key}}")
def admin_update_setting(key: str, update: SettingUpdate):
    try:
        return set_setting(key, update.value, actor=update.actor)
    except KeyError:
        raise HTTPException(404, "setting not found") from None
    except SettingError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post(f"/{API_VERSION}/admin/settings/{{key}}/reset")
def admin_reset_setting(key: str):
    try:
        return reset_setting(key, actor="rob")
    except KeyError:
        raise HTTPException(404, "setting not found") from None
    except SettingError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get(f"/{API_VERSION}/admin/queries")
def admin_queries(active_only: bool = False):
    return {"queries": admin_list_queries(active_only=active_only)}


@app.post(f"/{API_VERSION}/admin/queries")
def admin_add_query(query: QueryCreate):
    try:
        return add_query(**query.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.patch(f"/{API_VERSION}/admin/queries/{{query_id}}")
def admin_toggle_query(query_id: int, update: QueryToggle):
    try:
        return set_query_active(query_id, update.active)
    except KeyError:
        raise HTTPException(404, "query not found") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get(f"/{API_VERSION}/coverage/seek")
def seek_coverage():
    geographies = list_geographies()
    with connect() as conn:
        coverage = []
        for geography in geographies:
            root = conn.execute(
                "SELECT * FROM seek_partitions WHERE geography_code=? AND parent_id IS NULL ORDER BY updated_at DESC LIMIT 1",
                (geography["code"],),
            ).fetchone()
            incomplete = conn.execute(
                "SELECT COUNT(*) FROM seek_partitions WHERE geography_code=? AND status NOT LIKE 'COMPLETE%'",
                (geography["code"],),
            ).fetchone()[0]
            previous = conn.execute(
                """
                SELECT captured_at,root_status,reported_results,covered_unique_jobs,incomplete_partitions
                  FROM seek_coverage_history
                 WHERE geography_code=?
                 ORDER BY captured_at DESC, id DESC
                 LIMIT 1
                """,
                (geography["code"],),
            ).fetchone()
            coverage.append(
                {
                    "geography_code": geography["code"],
                    "label": geography["label"],
                    "enabled": bool(geography["enabled"]),
                    "has_current_cycle": root is not None,
                    "status": root["status"] if root else "NOT_RUN",
                    "reported_results": root["reported_results"] if root else None,
                    "covered_unique_jobs": root["collected_unique_jobs"] if root else 0,
                    "incomplete_partitions": int(incomplete),
                    "previous": dict(previous) if previous else None,
                }
            )
    return {
        "source": "seek",
        "partition_threshold": get_setting("collection.seek_partition_max_results"),
        "geographies": coverage,
    }


@app.get(f"/{API_VERSION}/admin/geographies")
def admin_geographies():
    return {"geographies": list_geographies()}


@app.patch(f"/{API_VERSION}/admin/geographies/{{code}}")
def admin_toggle_geography(code: str, update: GeographyToggle):
    try:
        return set_geography_enabled(code, update.enabled, actor=update.actor)
    except KeyError:
        raise HTTPException(404, "geography not found") from None


@app.post(f"/{API_VERSION}/admin/retention/run")
def admin_run_retention():
    return apply_retention().__dict__


@app.get(f"/{API_VERSION}/admin/service/status")
def admin_service_status():
    backups = list_backups(limit=1)
    return {
        "scheduler": SCHEDULER.status(),
        "collection": PROCESS_MANAGER.status(),
        "browser": {"active": persistent_browser_ready()},
        "last_backup": backups[0] if backups else None,
        "backup_directory": str(ROOT / "backups"),
        "log_url": f"/{API_VERSION}/admin/log",
    }


@app.get(f"/{API_VERSION}/admin/log", response_class=PlainTextResponse)
def admin_collection_log(lines: int = Query(500, ge=1, le=5000)):
    return PlainTextResponse(read_collection_log_tail(lines))


@app.post(f"/{API_VERSION}/admin/browser/seek")
def admin_open_seek_browser():
    try:
        result = focus_or_open_tab("https://au.seek.com/", host_suffix="seek.com")
    except BrowserBrokerError as exc:
        raise HTTPException(502, str(exc)) from None
    return result.result


@app.get(f"/{API_VERSION}/admin/stats")
def admin_collection_stats():
    return {
        "current": population_stats(enabled_state_codes()),
        "bootstrap": bootstrap_market_run(),
        "latest": latest_market_run(),
        "linkedin_campaign": linkedin_campaign_progress(),
    }


@app.post(f"/{API_VERSION}/admin/collection/run")
def admin_start_collection():
    try:
        return PROCESS_MANAGER.start(trigger="manual")
    except CollectionProcessError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post(f"/{API_VERSION}/admin/collection/stop")
def admin_stop_collection():
    try:
        return PROCESS_MANAGER.stop()
    except CollectionProcessError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post(f"/{API_VERSION}/admin/scheduler/pause")
def admin_pause_scheduler():
    setting = set_setting("scheduler.enabled", False, actor="rob-admin")
    return {"ok": True, "setting": setting, "scheduler": SCHEDULER.status()}


@app.post(f"/{API_VERSION}/admin/scheduler/resume")
def admin_resume_scheduler():
    setting = set_setting("scheduler.enabled", True, actor="rob-admin")
    return {"ok": True, "setting": setting, "scheduler": SCHEDULER.status()}


@app.get(f"/{API_VERSION}/admin/backups")
def admin_backups(limit: int = Query(20, ge=1, le=120)):
    return {"backups": list_backups(limit=limit)}


@app.post(f"/{API_VERSION}/admin/backup/run")
def admin_run_backup():
    return create_backup().__dict__
