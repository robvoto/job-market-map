from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from collector.db import ROOT, connect, init_db
from collector.query_admin import add_query, set_query_active
from collector.query_admin import list_queries as admin_list_queries
from collector.query_registry import sync_registry
from collector.retention import apply_retention
from collector.settings import (
    SettingError,
    get_setting,
    list_settings,
    reset_setting,
    seed_settings,
    set_setting,
)
from collector.status import STATUS_FIELDS, set_status

API_VERSION = "v1"
SCHEMA_VERSION = 1
ADMIN_HTML = ROOT / "api" / "admin.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    seed_settings()
    sync_registry()
    yield


app = FastAPI(
    title="Job Market Map",
    version="0.2.0",
    description="Neutral local job-market feed for Job Hunter, Reset / Edge, Plan Z and other consumers.",
    lifespan=lifespan,
)


class StatusUpdate(BaseModel):
    field: str
    value: bool = True
    actor: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    idempotency_key: str | None = Field(default=None, max_length=200)


class SettingUpdate(BaseModel):
    value: bool | int | float
    actor: str = Field(default="rob", max_length=100)


class QueryToggle(BaseModel):
    active: bool


class QueryCreate(BaseModel):
    source: str = Field(min_length=1, max_length=40)
    query_text: str = Field(min_length=1, max_length=300)
    location: str = Field(default="Sydney NSW", max_length=200)
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
        "shown_to_rob",
        "reviewed",
        "applied",
        "rejected",
        "dismissed",
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
                   SUM(CASE WHEN archived=0 THEN 1 ELSE 0 END) AS active_jobs,
                   SUM(CASE WHEN shown_to_rob=1 THEN 1 ELSE 0 END) AS shown,
                   SUM(CASE WHEN reviewed=1 THEN 1 ELSE 0 END) AS reviewed,
                   SUM(CASE WHEN applied=1 THEN 1 ELSE 0 END) AS applied,
                   SUM(CASE WHEN rejected=1 THEN 1 ELSE 0 END) AS rejected
              FROM jobs
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
    include_archived: bool = False,
    include_raw: bool = True,
):
    resolved_limit = _limit(limit)
    clauses = ["j.id > ?"]
    params: list[object] = [after_id]
    if source:
        clauses.append("j.source=?")
        params.append(source.casefold())
    if not include_archived:
        clauses.append("j.archived=0")
    where = " AND ".join(clauses)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT j.*,
                   (SELECT COUNT(*) FROM duplicate_links d
                     WHERE d.job_id_a=j.id OR d.job_id_b=j.id) AS duplicate_link_count
              FROM jobs j
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


@app.get(f"/{API_VERSION}/jobs/new")
@app.get("/jobs/new", include_in_schema=False)
def new_jobs(days: int = Query(1, ge=0, le=30), limit: int | None = Query(None, ge=1)):
    resolved_limit = _limit(limit)
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE first_seen_at>=? ORDER BY first_seen_at DESC LIMIT ?",
            (cutoff, resolved_limit),
        ).fetchall()
    return [_job_payload(row) for row in rows]


@app.get(f"/{API_VERSION}/jobs/search")
@app.get("/jobs/search", include_in_schema=False)
def search_jobs(
    q: str | None = None,
    source: str | None = None,
    unseen_only: bool = False,
    include_archived: bool = False,
    limit: int | None = Query(None, ge=1),
):
    resolved_limit = _limit(limit)
    clauses = []
    params: list[object] = []
    if q:
        clauses.append("(title LIKE ? OR employer LIKE ? OR raw_card_text LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if source:
        clauses.append("source=?")
        params.append(source.casefold())
    if unseen_only:
        clauses.append("shown_to_rob=0")
    if not include_archived:
        clauses.append("archived=0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY first_seen_at DESC LIMIT ?",
            (*params, resolved_limit),
        ).fetchall()
    return [_job_payload(row) for row in rows]


@app.get(f"/{API_VERSION}/jobs/{{job_id}}")
@app.get("/jobs/{job_id}", include_in_schema=False)
def get_job(job_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
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
    for dup in duplicates:
        dup["reasons"] = json.loads(dup.pop("reasons_json"))
    return {
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "job": _job_payload(row),
        "captures": captures,
        "query_hits": query_hits,
        "duplicates": duplicates,
    }


@app.post(f"/{API_VERSION}/jobs/{{job_id}}/status")
@app.post("/jobs/{job_id}/status", include_in_schema=False)
def update_status(job_id: int, update: StatusUpdate):
    if update.field not in STATUS_FIELDS:
        raise HTTPException(
            400, f"field must be one of: {', '.join(sorted(STATUS_FIELDS))}"
        )
    try:
        result = set_status(
            job_id,
            update.field,
            update.value,
            actor=update.actor,
            note=update.note,
            idempotency_key=update.idempotency_key,
        )
    except KeyError:
        raise HTTPException(404, "job not found") from None
    return {
        "ok": True,
        "job_id": job_id,
        "field": update.field,
        "value": update.value,
        "changed": result.changed,
        "event_id": result.event_id,
    }


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


@app.post(f"/{API_VERSION}/admin/retention/run")
def admin_run_retention():
    return apply_retention().__dict__
