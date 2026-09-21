from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Annotated

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
    get_job_field_states,
    get_job_jd,
    init_db,
)
from collector.field_states import FIELD_STATES, NEUTRAL_FIELDS, capabilities_for_source
from collector.geographies import (
    list_geographies,
    seed_geographies,
    set_geography_enabled,
)
from collector.jd_enrichment import (
    JDSourceFetchError,
    JDSourceUnavailableError,
    UnsupportedJDSourceError,
    get_or_enrich_job_jd,
)
from collector.query_admin import add_query, set_query_active
from collector.query_admin import list_queries as admin_list_queries
from collector.query_registry import sync_registry
from collector.retention import apply_retention
from collector.run_logging import (
    collection_logger,
    configure_collection_logging,
    read_collection_log_tail,
)
from collector.run_stats import population_stats
from collector.scheduler import SCHEDULER, SchedulerService
from collector.seek_cycle import enabled_state_codes
from collector.service_manager import PROCESS_MANAGER, CollectionProcessError
from collector.service_state import (
    bootstrap_market_run,
    latest_market_run,
    latest_seek_market_run,
)
from collector.settings import (
    SettingError,
    get_setting,
    list_settings,
    reset_setting,
    seed_settings,
    set_setting,
)
from collector.source_status import source_status_is_active_sql

API_VERSION = "v3"
SCHEMA_VERSION = 10
ADMIN_HTML = ROOT / "api" / "admin.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_collection_logging()
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
    item["field_states"] = get_job_field_states(int(item["id"]))
    item["salary_normalized"] = {
        "state": item.pop("salary_normalized_state", None),
        "min_amount": item.pop("salary_min_amount", None),
        "max_amount": item.pop("salary_max_amount", None),
        "period": item.pop("salary_period", None),
        "currency": item.pop("salary_currency", None),
        "qualifier": item.pop("salary_qualifier", None),
    }
    for key in (
        "reposted",
        "easy_apply",
        "archived",
        "vacancy_archived",
    ):
        if item.get(key) is not None:
            item[key] = bool(item[key])
    if not include_raw:
        item.pop("raw_card_text", None)
    return item


def _vacancy_active_clause(job_alias: str = "j") -> str:
    """SQL predicate: primary vacancy is active when any linked source row is active."""
    return f"""
        EXISTS (
            SELECT 1
              FROM jobs vacancy_job
              JOIN job_observation_state vacancy_state ON vacancy_state.job_id=vacancy_job.id
             WHERE (vacancy_job.id={job_alias}.id OR vacancy_job.primary_job_id={job_alias}.id)
               AND COALESCE(vacancy_state.archived,0)=0
               AND {source_status_is_active_sql('vacancy_job.source_status')}
        )
    """


def _vacancy_lifecycle_select(job_alias: str = "j") -> str:
    """Derived vacancy lifecycle while preserving source-row timestamps."""
    return f"""
        (SELECT MAX(vacancy_state.last_seen_at)
           FROM jobs vacancy_job
           JOIN job_observation_state vacancy_state ON vacancy_state.job_id=vacancy_job.id
          WHERE vacancy_job.id={job_alias}.id OR vacancy_job.primary_job_id={job_alias}.id
        ) AS vacancy_last_seen_at,
        CASE WHEN {_vacancy_active_clause(job_alias)} THEN 0 ELSE 1 END AS vacancy_archived
    """


def _pending_active_primary_summary(after_id: int) -> dict[str, int]:
    """Return one fixed feed snapshot and its actionable primary-job count."""
    with connect() as conn:
        row = conn.execute(
            f"""
            WITH snapshot AS (
                SELECT COALESCE(MAX(id),0) AS snapshot_max_id
                  FROM jobs
            )
            SELECT snapshot.snapshot_max_id,
                   COUNT(j.id) AS pending_active_primary_count
              FROM snapshot
              LEFT JOIN jobs j
                ON j.id > ?
               AND j.id <= snapshot.snapshot_max_id
               AND j.primary_job_id IS NULL
               AND {_vacancy_active_clause('j')}
             GROUP BY snapshot.snapshot_max_id
            """,
            (after_id,),
        ).fetchone()
    return {
        "snapshot_max_id": int(row["snapshot_max_id"]),
        "pending_active_primary_count": int(row["pending_active_primary_count"]),
    }


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


_SEARCH_TOKEN_RE = re.compile(
    r'''\s*(?:(AND|OR)\b|([()])|([A-Za-z_][A-Za-z0-9_]*:"(?:[^"\\]|\\.)*")|([A-Za-z_][A-Za-z0-9_]*:[^\s()]+)|("(?:[^"\\]|\\.)*")|([^\s()]+))''',
    re.IGNORECASE,
)
_SEARCH_FIELDS = {
    "title": ("title",), "company": ("employer",), "employer": ("employer",),
    "location": ("location",), "classification": ("classification_text",),
    "subclassification": ("subclassification_text",), "employment_type": ("employment_type",),
    "workplace_type": ("workplace_type",), "apply_method": ("apply_method",),
    "description": ("teaser_text", "raw_card_text", "full_description"),
}
_SEARCH_STATE_FIELDS = {
    "title": "title",
    "company": "company",
    "employer": "company",
    "location": "location",
    "classification": "classification",
    "subclassification": "subclassification",
    "employment_type": "employment_type",
    "workplace_type": "workplace_type",
    "apply_method": "apply_method",
    "description": "description",
}
_SEARCH_DEFAULT_FIELDS = (
    "title", "employer", "location", "classification_text", "subclassification_text",
    "employment_type", "workplace_type", "apply_method", "teaser_text", "raw_card_text",
    "full_description",
)
_SEARCH_MAX_TERMS = 16


def _search_unquote(value: str) -> str:
    return value[1:-1].replace('\\"', '"').replace('\\\\', '\\')


def _search_term(token: str) -> tuple[str, str, bool]:
    field = ""
    value = token
    if ":" in token and not token.startswith('"'):
        candidate, value = token.split(":", 1)
        field = candidate.casefold()
        if field not in _SEARCH_FIELDS:
            raise HTTPException(400, f"unsupported search field: {candidate}")
        if not value:
            raise HTTPException(400, "field-scoped search terms must have a value")
    exact = value.startswith('"') and value.endswith('"')
    if exact:
        value = _search_unquote(value)
    value = " ".join(value.split()).strip()
    if not value:
        raise HTTPException(400, "search terms must not be empty")
    return field, value, exact


def _parse_search_expression(expression: str) -> tuple[object, int]:
    normalized = " ".join(str(expression).split()).strip()
    if not normalized:
        raise HTTPException(400, "search expressions must not be empty")
    if not re.search(r"\b(?:AND|OR)\b|[()]|[A-Za-z_][A-Za-z0-9_]*:", normalized, re.IGNORECASE):
        field, value, exact = _search_term(normalized)
        return ("term", field, value, exact), 1
    tokens: list[tuple[str, str]] = []
    position = 0
    for match in _SEARCH_TOKEN_RE.finditer(normalized):
        if match.start() != position and normalized[position:match.start()].strip():
            raise HTTPException(400, "malformed search expression")
        position = match.end()
        if match.group(1):
            tokens.append((match.group(1).upper(), match.group(1).upper()))
        elif match.group(2):
            tokens.append((match.group(2), match.group(2)))
        else:
            tokens.append(("TERM", next(group for group in match.groups()[2:] if group is not None)))
    if normalized[position:].strip() or not tokens:
        raise HTTPException(400, "malformed search expression")
    index = 0
    term_count = 0

    def parse_primary() -> tuple[object, int]:
        nonlocal index, term_count
        if index >= len(tokens):
            raise HTTPException(400, "expected a search term")
        kind, value = tokens[index]
        if kind == "(":
            index += 1
            node, count = parse_or()
            if index >= len(tokens) or tokens[index][0] != ")":
                raise HTTPException(400, "unclosed search group")
            index += 1
            return node, count
        if kind != "TERM":
            raise HTTPException(400, "expected a search term")
        index += 1
        term_count += 1
        if term_count > _SEARCH_MAX_TERMS:
            raise HTTPException(400, f"search expression exceeds {_SEARCH_MAX_TERMS} terms")
        field, text, exact = _search_term(value)
        return ("term", field, text, exact), 1

    def parse_and() -> tuple[object, int]:
        nonlocal index
        node, count = parse_primary()
        while index < len(tokens) and tokens[index][0] == "AND":
            index += 1
            right, right_count = parse_primary()
            node, count = ("and", node, right), count + right_count
        return node, count

    def parse_or() -> tuple[object, int]:
        nonlocal index
        node, count = parse_and()
        while index < len(tokens) and tokens[index][0] == "OR":
            index += 1
            right, right_count = parse_and()
            node, count = ("or", node, right), count + right_count
        return node, count

    node, count = parse_or()
    if index != len(tokens):
        raise HTTPException(400, "unexpected token in search expression")
    return node, count


def _search_like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _search_sql(node: object, params: list[object]) -> str:
    kind = node[0]
    if kind in {"and", "or"}:
        operator = " AND " if kind == "and" else " OR "
        return "(" + _search_sql(node[1], params) + operator + _search_sql(node[2], params) + ")"
    _, field, value, _exact = node
    columns = _SEARCH_FIELDS.get(field, _SEARCH_DEFAULT_FIELDS)
    like = _search_like(value)
    clauses = []
    state_field = _SEARCH_STATE_FIELDS.get(field)
    if state_field:
        value_clauses = []
        for column in columns:
            value_clauses.append(f"COALESCE(vacancy_job.{column}, '') LIKE ? ESCAPE '\\'")
            params.append(like)
        clauses.append(
            "((" + " OR ".join(value_clauses) + ") AND "
            "EXISTS (SELECT 1 FROM job_field_states known_state "
            "WHERE known_state.job_id=vacancy_job.id AND known_state.field_name=? "
            "AND known_state.state='known'))"
        )
        params.append(state_field)
        # A field-scoped query keeps source rows whose value is unresolved or
        # not applicable. Missing state rows are legacy/unevaluated evidence
        # and therefore have the same semantics as ``unknown``. Explicit
        # ``not_present`` is authoritative even when an older value remains.
        clauses.append(
            "EXISTS (SELECT 1 FROM job_field_states q_state "
            "WHERE q_state.job_id=vacancy_job.id AND q_state.field_name=? "
            "AND q_state.state IN ('unknown','not_applicable'))"
        )
        params.append(state_field)
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM job_field_states missing_state "
            "WHERE missing_state.job_id=vacancy_job.id AND missing_state.field_name=?)"
        )
        params.append(state_field)
    else:
        for column in columns:
            clauses.append(f"COALESCE(vacancy_job.{column}, '') LIKE ? ESCAPE '\\'")
            params.append(like)
    return "(" + " OR ".join(clauses) + ")"


@app.get(f"/{API_VERSION}/capabilities/fields")
@app.get("/capabilities/fields", include_in_schema=False)
def field_capabilities():
    """Expose the neutral field/state contract for current and future adapters."""
    sources = {source: capabilities_for_source(source) for source in ("seek", "linkedin", "apsjobs", "future")}
    return {"api_version": API_VERSION, "schema_version": SCHEMA_VERSION, "field_states": list(FIELD_STATES), "fields": list(NEUTRAL_FIELDS), "sources": sources}


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
    through_id: int | None = Query(
        None,
        ge=0,
        description="Optional fixed high-water job id for a stable multi-page market snapshot.",
    ),
    limit: int | None = Query(None, ge=1),
    source: str | None = None,
    geography_code: str | None = None,
    include_archived: bool = False,
    include_raw: bool = True,
):
    resolved_limit = _limit(limit)
    with connect() as conn:
        snapshot_max_id = (
            int(conn.execute("SELECT COALESCE(MAX(id),0) FROM jobs").fetchone()[0])
            if through_id is None
            else int(through_id)
        )
    # Confirmed same-vacancy source postings remain lookup-addressable, but only
    # their oldest deterministic primary enters downstream processing feeds.
    clauses = ["j.id > ?", "j.id <= ?", "j.primary_job_id IS NULL"]
    params: list[object] = [after_id, snapshot_max_id]
    if source:
        clauses.append("j.source=?")
        params.append(source.casefold())
    if geography_code:
        clauses.append("j.geography_code=?")
        params.append(geography_code.upper())
    if not include_archived:
        clauses.append(_vacancy_active_clause("j"))
    where = " AND ".join(clauses)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT j.*, s.first_seen_at, s.last_seen_at, s.capture_count,
                   s.archived, s.compacted_at,
                   {_vacancy_lifecycle_select("j")},
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
        "snapshot_max_id": snapshot_max_id,
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


@app.get(f"/{API_VERSION}/consumers/{{consumer_key}}/state")
def consumer_state(consumer_key: str):
    checkpoint = get_checkpoint(consumer_key)
    return {
        **checkpoint,
        **_pending_active_primary_summary(int(checkpoint["last_job_id"])),
    }


@app.get(f"/{API_VERSION}/consumers/{{consumer_key}}/feed")
def consumer_feed(
    consumer_key: str,
    through_id: int | None = Query(
        None,
        ge=0,
        description="Optional run-scoped high-water job id returned by the first consumer page.",
    ),
    limit: int | None = Query(None, ge=1),
    geography_code: str | None = None,
    source: str | None = None,
    include_raw: bool = True,
):
    checkpoint = get_checkpoint(consumer_key)
    return job_feed(
        after_id=int(checkpoint["last_job_id"]),
        through_id=through_id,
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
    q: Annotated[list[str] | None, Query(max_length=300)] = None,
    source: Annotated[list[str] | None, Query()] = None,
    geography_code: Annotated[list[str] | None, Query()] = None,
    location: Annotated[list[str] | None, Query()] = None,
    classification: Annotated[list[str] | None, Query()] = None,
    subclassification: Annotated[list[str] | None, Query()] = None,
    employment_type: Annotated[list[str] | None, Query()] = None,
    workplace_type: Annotated[list[str] | None, Query()] = None,
    apply_method: Annotated[list[str] | None, Query()] = None,
    company: Annotated[list[str] | None, Query()] = None,
    posted_after: str | None = None,
    after_id: int = Query(0, ge=0),
    through_id: int | None = Query(None, ge=0),
    include_archived: bool = False,
    limit: int | None = Query(None, ge=1),
    include_raw: bool = False,
):
    """Search canonical vacancies using neutral, snapshot-bounded filters.

    Repeated ``q`` expressions are ORed for backward compatibility. Within an
    expression, ``AND`` binds more tightly than ``OR``; quoted phrases and
    field scopes such as ``title:"delivery manager"`` are deterministic. JH
    owns personal fit and decision policy; JMM only searches neutral evidence.
    """
    started = perf_counter()
    resolved_limit = _limit(limit)
    expressions = [str(value).strip() for value in q or [] if str(value).strip()]
    if len(expressions) > _SEARCH_MAX_TERMS:
        raise HTTPException(400, f"search supports at most {_SEARCH_MAX_TERMS} q expressions")
    if any(len(expression) > 300 for expression in expressions):
        raise HTTPException(400, "search expressions must be 300 characters or shorter")
    parsed_queries = [_parse_search_expression(expression) for expression in expressions]
    sources = [str(value).strip().casefold() for value in source or [] if str(value).strip()]
    geographies = [str(value).strip().upper() for value in geography_code or [] if str(value).strip()]

    with connect() as conn:
        snapshot_max_id = (
            int(conn.execute("SELECT COALESCE(MAX(id),0) FROM jobs").fetchone()[0])
            if through_id is None
            else int(through_id)
        )

    linked_clauses = [
        "vacancy_job.id <= ?",
    ]
    if not include_archived:
        linked_clauses.extend(
            [
                "COALESCE(vacancy_state.archived,0)=0",
                source_status_is_active_sql("vacancy_job.source_status"),
            ]
        )
    linked_params: list[object] = [snapshot_max_id]
    if sources:
        placeholders = ",".join("?" for _ in sources)
        linked_clauses.append(f"vacancy_job.source IN ({placeholders})")
        linked_params.extend(sources)
    if geographies:
        placeholders = ",".join("?" for _ in geographies)
        linked_clauses.append(f"vacancy_job.geography_code IN ({placeholders})")
        linked_params.extend(geographies)
    if posted_after:
        linked_clauses.append("(vacancy_job.posted_at IS NULL OR vacancy_job.posted_at>=?)")
        linked_params.append(posted_after)

    filter_columns = {
        "location": "location",
        "classification": "classification_text",
        "subclassification": "subclassification_text",
        "employment_type": "employment_type",
        "workplace_type": "workplace_type",
        "apply_method": "apply_method",
        "company": "employer",
    }
    filter_state_fields = {
        "location": "location", "classification": "classification",
        "subclassification": "subclassification", "employment_type": "employment_type",
        "workplace_type": "workplace_type", "apply_method": "apply_method", "company": "company",
    }
    for parameter, column in filter_columns.items():
        values = [str(value).strip() for value in (locals()[parameter] or []) if str(value).strip()]
        if values:
            state_field = filter_state_fields[parameter]
            linked_clauses.append("(" + " OR ".join(
                f"((LOWER(COALESCE(vacancy_job.{column},'')) LIKE ? ESCAPE '\\' "
                "AND EXISTS (SELECT 1 FROM job_field_states known_state WHERE known_state.job_id=vacancy_job.id AND known_state.field_name=? AND known_state.state='known')) "
                "OR EXISTS (SELECT 1 FROM job_field_states filter_state WHERE filter_state.job_id=vacancy_job.id AND filter_state.field_name=? AND filter_state.state IN ('unknown','not_applicable')) "
                "OR NOT EXISTS (SELECT 1 FROM job_field_states missing_state WHERE missing_state.job_id=vacancy_job.id AND missing_state.field_name=?))"
                for _ in values
            ) + ")")
            for value in values:
                linked_params.extend((_search_like(value.casefold()), state_field, state_field, state_field))

    if parsed_queries:
        query_clauses = []
        for node, _term_count in parsed_queries:
            query_params: list[object] = []
            query_clauses.append(_search_sql(node, query_params))
            linked_params.extend(query_params)
        linked_clauses.append("(" + " OR ".join(query_clauses) + ")")

    linked_from_where = (
        " FROM jobs vacancy_job "
        "JOIN job_observation_state vacancy_state ON vacancy_state.job_id=vacancy_job.id "
        "WHERE " + " AND ".join(linked_clauses)
    )
    with connect() as conn:
        total = int(
            conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(vacancy_job.primary_job_id, vacancy_job.id))"
                + linked_from_where,
                linked_params,
            ).fetchone()[0]
        )
        page_ids = [
            int(row["primary_id"])
            for row in conn.execute(
                "SELECT DISTINCT COALESCE(vacancy_job.primary_job_id, vacancy_job.id) AS primary_id"
                + linked_from_where
                + " AND COALESCE(vacancy_job.primary_job_id, vacancy_job.id) > ?"
                + " ORDER BY primary_id ASC LIMIT ?",
                (*linked_params, after_id, resolved_limit + 1),
            ).fetchall()
        ]
        rows = []
        if page_ids:
            placeholders = ",".join("?" for _ in page_ids)
            rows = conn.execute(
                f"""
                SELECT j.*, s.first_seen_at, s.last_seen_at, s.capture_count, s.archived, s.compacted_at
                  FROM jobs j
                  LEFT JOIN job_observation_state s ON s.job_id=j.id
                 WHERE j.id IN ({placeholders})
                 ORDER BY j.id ASC
                """,
                page_ids,
            ).fetchall()
        page_ids = [int(row["id"]) for row in rows]
        lifecycle_by_id: dict[int, dict[str, object]] = {}
        if page_ids:
            placeholders = ",".join("?" for _ in page_ids)
            lifecycle_rows = conn.execute(
                f"""
                SELECT primary_job.id AS primary_id,
                       MAX(vacancy_state.last_seen_at) AS vacancy_last_seen_at,
                       CASE WHEN MAX(
                           CASE WHEN COALESCE(vacancy_state.archived,0)=0
                                  AND {source_status_is_active_sql("vacancy_job.source_status")}
                                THEN 1 ELSE 0 END
                       )=1 THEN 0 ELSE 1 END AS vacancy_archived
                  FROM jobs primary_job
                  JOIN jobs vacancy_job
                    ON vacancy_job.id=primary_job.id OR vacancy_job.primary_job_id=primary_job.id
                  JOIN job_observation_state vacancy_state ON vacancy_state.job_id=vacancy_job.id
                 WHERE primary_job.id IN ({placeholders})
                 GROUP BY primary_job.id
                """,
                page_ids,
            ).fetchall()
            lifecycle_by_id = {
                int(row["primary_id"]): {
                    "vacancy_last_seen_at": row["vacancy_last_seen_at"],
                    "vacancy_archived": row["vacancy_archived"],
                }
                for row in lifecycle_rows
            }
    has_more = len(rows) > resolved_limit
    rows = rows[:resolved_limit]
    payload_rows = []
    for row in rows:
        item = dict(row)
        item.update(lifecycle_by_id.get(int(item["id"]), {}))
        payload_rows.append(item)
    elapsed_ms = round((perf_counter() - started) * 1000, 1)
    collection_logger().info(
        "JMM_SEARCH page total=%d page_size=%d returned=%d has_more=%s after_id=%d through_id=%d q_expressions=%d elapsed_ms=%.1f",
        total, resolved_limit, len(rows), has_more, after_id, snapshot_max_id, len(parsed_queries), elapsed_ms,
    )
    return {
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "snapshot_max_id": snapshot_max_id,
        "total": total,
        "items": [_job_payload(row, include_raw=include_raw) for row in payload_rows],
        "next_cursor": int(rows[-1]["id"]) if rows else after_id,
        "has_more": has_more,
    }


@app.get(f"/{API_VERSION}/jobs/{{job_id}}/jd")
def cached_job_jd(job_id: int, identity_key: str | None = None):
    """Return only a JD already cached in JMM; never fetch from the source board."""
    resolved_job_id = job_id
    try:
        result = get_job_jd(resolved_job_id)
    except KeyError:
        result = None
        stable_identity = str(identity_key or "").strip()
        if stable_identity:
            active = get_job_by_identity_key(stable_identity)
            if active is not None:
                resolved_job_id = int(active["id"])
                result = get_job_jd(resolved_job_id)
            else:
                with connect() as conn:
                    tombstone = conn.execute(
                        "SELECT 1 FROM job_tombstones WHERE identity_key=? LIMIT 1",
                        (stable_identity,),
                    ).fetchone()
                if tombstone is not None:
                    raise HTTPException(410, "job was terminal-retired")
        if result is None and resolved_job_id == job_id:
            raise HTTPException(404, "job not found")
    if result is None:
        raise HTTPException(409, "JD not cached yet")
    return {
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "status": "cached",
        **result,
    }


@app.post(f"/{API_VERSION}/jobs/{{job_id}}/jd")
def job_jd(job_id: int, source: str | None = None):
    """Return the canonical JD, enriching it through JMM when it is missing."""
    try:
        result = get_or_enrich_job_jd(job_id, source=source) if source else get_or_enrich_job_jd(job_id)
    except KeyError:
        raise HTTPException(404, "job not found") from None
    except UnsupportedJDSourceError as exc:
        raise HTTPException(422, str(exc)) from None
    except JDSourceUnavailableError as exc:
        raise HTTPException(410, str(exc)) from None
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
                SELECT id AS history_id,captured_at,root_status,reported_results,
                       covered_unique_jobs,incomplete_partitions
                  FROM seek_coverage_history
                 WHERE geography_code=?
                 ORDER BY captured_at DESC, id DESC
                 LIMIT 1
                """,
                (geography["code"],),
            ).fetchone()
            previous_payload = dict(previous) if previous else None
            if previous_payload is not None:
                diagnostics = conn.execute(
                    """
                    SELECT hierarchy_label,url,status,reported_results,
                           collected_unique_jobs,last_error
                      FROM seek_coverage_diagnostics
                     WHERE coverage_history_id=?
                     ORDER BY id
                    """,
                    (previous_payload["history_id"],),
                ).fetchall()
                previous_payload["diagnostics"] = [dict(row) for row in diagnostics]
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
                    "previous": previous_payload,
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
        "latest_seek": latest_seek_market_run(),
        "linkedin_campaign": linkedin_campaign_progress(),
    }


@app.post(f"/{API_VERSION}/admin/collection/run")
def admin_start_collection():
    try:
        return PROCESS_MANAGER.start(trigger="manual")
    except CollectionProcessError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post(f"/{API_VERSION}/admin/linkedin/run")
def admin_start_linkedin_collection():
    try:
        return PROCESS_MANAGER.start_linkedin(
            hours_old=int(get_setting("collection.linkedin_window_hours")),
            cycle_key=SchedulerService.linkedin_cycle_key(),
        )
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


@app.post(f"/{API_VERSION}/admin/scheduler/seek/pause")
def admin_pause_seek_scheduler():
    setting = set_setting("scheduler.seek_enabled", False, actor="rob-admin")
    return {"ok": True, "setting": setting, "scheduler": SCHEDULER.status()}


@app.post(f"/{API_VERSION}/admin/scheduler/seek/resume")
def admin_resume_seek_scheduler():
    setting = set_setting("scheduler.seek_enabled", True, actor="rob-admin")
    return {"ok": True, "setting": setting, "scheduler": SCHEDULER.status()}


@app.post(f"/{API_VERSION}/admin/scheduler/linkedin/pause")
def admin_pause_linkedin_scheduler():
    setting = set_setting("scheduler.linkedin_enabled", False, actor="rob-admin")
    return {"ok": True, "setting": setting, "scheduler": SCHEDULER.status()}


@app.post(f"/{API_VERSION}/admin/scheduler/linkedin/resume")
def admin_resume_linkedin_scheduler():
    setting = set_setting("scheduler.linkedin_enabled", True, actor="rob-admin")
    return {"ok": True, "setting": setting, "scheduler": SCHEDULER.status()}


@app.get(f"/{API_VERSION}/admin/backups")
def admin_backups(limit: int = Query(20, ge=1, le=120)):
    return {"backups": list_backups(limit=limit)}


@app.post(f"/{API_VERSION}/admin/backup/run")
def admin_run_backup():
    return create_backup().__dict__
