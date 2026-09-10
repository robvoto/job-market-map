from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from collector import db
from collector.identity import job_identity_key
from collector.ingest import canonicalise_url, ingest_card
from collector.models import CardObservation

BOOTSTRAP_ORIGIN = "job_hunter_bootstrap_v1"


@dataclass(slots=True)
class BootstrapCandidate:
    source: str
    source_job_id: str
    canonical_url: str
    title: str | None = None
    employer: str | None = None
    location: str | None = None
    salary_text: str | None = None
    employment_type: str | None = None
    workplace_type: str | None = None
    posted_text: str | None = None
    posted_at: str | None = None
    reposted: bool = False
    teaser_text: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    full_description: str | None = None
    jd_fetched_at: str | None = None
    jd_source: str | None = None

    @property
    def identity_key(self) -> str:
        return job_identity_key(self.source, self.source_job_id, self.canonical_url)


@dataclass(slots=True)
class BootstrapReport:
    mode: str
    source_db: str
    records_checked: int = 0
    valid_source_records: int = 0
    valid_importable_jobs: int = 0
    skipped_invalid_records: int = 0
    unmapped_records: int = 0
    existing_jmm_jobs: int = 0
    new_jmm_jobs: int = 0
    jobs_with_jds: int = 0
    jobs_without_jds: int = 0
    identity_conflicts: int = 0
    jd_conflicts: int = 0
    conflicts: int = 0
    jobs_created: int = 0
    jobs_updated: int = 0
    jobs_resurrected: int = 0
    jobs_already_imported: int = 0
    jds_stored: int = 0
    jds_already_present: int = 0
    backup_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BootstrapPlan:
    report: BootstrapReport
    candidates: list[BootstrapCandidate]
    blocked_identities: set[str]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    return text or None


def _source_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _normalise_timestamp(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def _timestamp_key(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def _is_usable_url(value: str | None) -> bool:
    if not value:
        return False
    parts = urlsplit(value)
    return parts.scheme.casefold() in {"http", "https"} and bool(parts.netloc)


def _read_only_connect(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Job Hunter database not found: {resolved}")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _detail_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("detail_evidence")
    return value if isinstance(value, dict) else {}


def _candidate_from_row(row: sqlite3.Row) -> tuple[BootstrapCandidate | None, str | None]:
    source = (_clean(row["source"]) or "").casefold()
    source_job_id = _clean(row["platform_id"])
    row_job_key = _clean(row["job_key"])
    if not source or not source_job_id or not row_job_key:
        return None, "invalid"

    key_source, separator, key_platform_id = row_job_key.partition(":")
    if not separator or key_source.casefold() != source or key_platform_id != source_job_id:
        return None, "conflict"

    raw_data = row["data"]
    if raw_data is None:
        return None, "unmapped"
    try:
        payload = json.loads(raw_data)
    except (TypeError, json.JSONDecodeError):
        return None, "unmapped"
    if not isinstance(payload, dict):
        return None, "unmapped"

    payload_job_key = _clean(payload.get("job_key"))
    if payload_job_key and payload_job_key != row_job_key:
        return None, "conflict"

    claimed_source = _clean(payload.get("source"))
    if claimed_source and claimed_source.casefold() != source:
        return None, "conflict"

    detail_evidence = _detail_evidence(payload)
    metadata_raw = detail_evidence.get("source_metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    metadata_source = _clean(metadata.get("platform"))
    if metadata_source and metadata_source.casefold() != source:
        return None, "conflict"
    metadata_job_id = _clean(metadata.get("platform_job_id"))
    if metadata_job_id and metadata_job_id != source_job_id:
        return None, "conflict"

    metadata_url = _clean(metadata.get("canonical_url"))
    payload_url = _clean(payload.get("url"))
    selected_url = metadata_url if _is_usable_url(metadata_url) else payload_url
    if not _is_usable_url(selected_url):
        return None, "invalid"
    canonical_url = canonicalise_url(selected_url)
    if not _is_usable_url(canonical_url):
        return None, "invalid"

    first_seen = _normalise_timestamp(row["first_seen"]) or _normalise_timestamp(
        payload.get("first_seen_at")
    )
    last_seen = _normalise_timestamp(row["last_seen"]) or _normalise_timestamp(
        payload.get("last_seen_at")
    )

    details_text = _source_text(detail_evidence.get("details_text"))
    jd_fetched_at = _normalise_timestamp(detail_evidence.get("fetched_at"))
    description_source = _clean(detail_evidence.get("description_source"))
    metadata_canonical_url = (
        canonicalise_url(metadata_url) if _is_usable_url(metadata_url) else None
    )
    jd_is_source_backed = bool(
        details_text
        and jd_fetched_at
        and description_source
        and metadata_source
        and metadata_source.casefold() == source
        and metadata_job_id == source_job_id
        and metadata_canonical_url == canonical_url
    )
    full_description = details_text if jd_is_source_backed else None
    jd_source = (
        f"job_hunter_detail_evidence:{description_source}"
        if full_description
        else None
    )

    return (
        BootstrapCandidate(
            source=source,
            source_job_id=source_job_id,
            canonical_url=canonical_url,
            title=_clean(row["title"]) or _clean(payload.get("title")),
            employer=_clean(row["company"]) or _clean(payload.get("company")),
            first_seen_at=first_seen,
            last_seen_at=last_seen,
            full_description=full_description,
            jd_fetched_at=jd_fetched_at,
            jd_source=jd_source,
        ),
        None,
    )


def _merge_candidates(current: BootstrapCandidate, incoming: BootstrapCandidate) -> BootstrapCandidate:
    newer, older = (incoming, current)
    if _timestamp_key(current.last_seen_at) > _timestamp_key(incoming.last_seen_at):
        newer, older = current, incoming

    def value(name: str):
        return getattr(newer, name) if getattr(newer, name) not in (None, "") else getattr(older, name)

    first_seen_values = [value for value in (current.first_seen_at, incoming.first_seen_at) if value]
    last_seen_values = [value for value in (current.last_seen_at, incoming.last_seen_at) if value]
    first_seen = min(first_seen_values, key=_timestamp_key) if first_seen_values else None
    last_seen = max(last_seen_values, key=_timestamp_key) if last_seen_values else None

    return BootstrapCandidate(
        source=current.source,
        source_job_id=current.source_job_id,
        canonical_url=newer.canonical_url,
        title=value("title"),
        employer=value("employer"),
        location=value("location"),
        salary_text=value("salary_text"),
        employment_type=value("employment_type"),
        workplace_type=value("workplace_type"),
        posted_text=value("posted_text"),
        posted_at=value("posted_at"),
        reposted=bool(current.reposted or incoming.reposted),
        teaser_text=value("teaser_text"),
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        full_description=value("full_description"),
        jd_fetched_at=value("jd_fetched_at"),
        jd_source=value("jd_source"),
    )


def _existing_jmm_rows(path: Path, table: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path.expanduser().resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            return []
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        wanted = [
            name
            for name in (
                "id",
                "source",
                "source_job_id",
                "canonical_url",
                "identity_key",
                "first_seen_at",
                "last_seen_at",
                "full_description",
            )
            if name in columns
        ]
        return [dict(row) for row in conn.execute(f"SELECT {', '.join(wanted)} FROM {table}")]
    finally:
        conn.close()


def _find_jmm_conflicts(
    candidates: list[BootstrapCandidate], jmm_db_path: Path
) -> tuple[set[str], int]:
    blocked: set[str] = set()
    jd_conflicts = 0

    candidate_urls: dict[tuple[str, str], str] = {}
    for candidate in candidates:
        url_key = (candidate.source, candidate.canonical_url)
        prior_identity = candidate_urls.get(url_key)
        if prior_identity and prior_identity != candidate.identity_key:
            blocked.add(prior_identity)
            blocked.add(candidate.identity_key)
        else:
            candidate_urls[url_key] = candidate.identity_key

    jobs = _existing_jmm_rows(jmm_db_path, "jobs")
    tombstones = _existing_jmm_rows(jmm_db_path, "job_tombstones")

    for candidate in candidates:
        identity = candidate.identity_key
        if identity in blocked:
            continue
        for rows in (jobs, tombstones):
            by_id = [
                row
                for row in rows
                if (_clean(row.get("source")) or "").casefold() == candidate.source
                and _clean(row.get("source_job_id")) == candidate.source_job_id
            ]
            by_url = [
                row
                for row in rows
                if (_clean(row.get("source")) or "").casefold() == candidate.source
                and _clean(row.get("canonical_url")) == candidate.canonical_url
            ]
            row_ids = {row.get("id") for row in by_id + by_url}
            if len(row_ids) > 1:
                blocked.add(identity)
                break
            if by_url:
                existing_source_id = _clean(by_url[0].get("source_job_id"))
                if existing_source_id and existing_source_id != candidate.source_job_id:
                    blocked.add(identity)
                    break

        if identity in blocked:
            continue
        matching_job = next(
            (
                row
                for row in jobs
                if (_clean(row.get("source")) or "").casefold() == candidate.source
                and (
                    _clean(row.get("source_job_id")) == candidate.source_job_id
                    or _clean(row.get("canonical_url")) == candidate.canonical_url
                )
            ),
            None,
        )
        existing_jd = _clean(matching_job.get("full_description")) if matching_job else None
        if existing_jd and candidate.full_description and existing_jd != candidate.full_description:
            jd_conflicts += 1

    return blocked, jd_conflicts


def _candidate_exists_in_rows(
    candidate: BootstrapCandidate, rows: list[dict[str, Any]]
) -> bool:
    return any(
        (_clean(row.get("source")) or "").casefold() == candidate.source
        and (
            _clean(row.get("source_job_id")) == candidate.source_job_id
            or _clean(row.get("canonical_url")) == candidate.canonical_url
        )
        for row in rows
    )


def plan_bootstrap(job_hunter_db: Path, *, jmm_db_path: Path | None = None) -> BootstrapPlan:
    target_db = (jmm_db_path or db.DB_PATH).expanduser().resolve()
    report = BootstrapReport(mode="dry-run", source_db=str(job_hunter_db.expanduser().resolve()))
    grouped: dict[str, BootstrapCandidate] = {}
    row_conflict_identities: set[str] = set()

    with _read_only_connect(job_hunter_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(job_history)")}
        required = {"job_key", "source", "platform_id", "title", "company", "first_seen", "last_seen", "data"}
        missing = required - columns
        if missing:
            raise RuntimeError(
                "Job Hunter job_history schema is missing required columns: " + ", ".join(sorted(missing))
            )
        rows = conn.execute(
            "SELECT job_key, source, platform_id, title, company, first_seen, last_seen, data FROM job_history ORDER BY id"
        ).fetchall()

    report.records_checked = len(rows)
    for row in rows:
        candidate, issue = _candidate_from_row(row)
        if candidate is None:
            report.skipped_invalid_records += 1
            if issue == "unmapped":
                report.unmapped_records += 1
            if issue == "conflict":
                report.identity_conflicts += 1
            continue
        report.valid_source_records += 1
        identity = candidate.identity_key
        current = grouped.get(identity)
        grouped[identity] = _merge_candidates(current, candidate) if current else candidate

    candidates = list(grouped.values())
    blocked, jd_conflicts = _find_jmm_conflicts(candidates, target_db)
    blocked.update(row_conflict_identities)
    report.identity_conflicts += len(blocked)
    report.jd_conflicts = jd_conflicts
    report.conflicts = report.identity_conflicts + report.jd_conflicts
    report.valid_importable_jobs = sum(1 for candidate in candidates if candidate.identity_key not in blocked)
    report.jobs_with_jds = sum(
        1
        for candidate in candidates
        if candidate.identity_key not in blocked and candidate.full_description
    )
    report.jobs_without_jds = report.valid_importable_jobs - report.jobs_with_jds
    existing_rows = _existing_jmm_rows(target_db, "jobs") + _existing_jmm_rows(
        target_db, "job_tombstones"
    )
    report.existing_jmm_jobs = sum(
        1
        for candidate in candidates
        if candidate.identity_key not in blocked
        and _candidate_exists_in_rows(candidate, existing_rows)
    )
    report.new_jmm_jobs = report.valid_importable_jobs - report.existing_jmm_jobs
    return BootstrapPlan(report=report, candidates=candidates, blocked_identities=blocked)


def _bootstrap_capture_exists(conn: sqlite3.Connection, job_id: int) -> bool:
    rows = conn.execute(
        "SELECT raw_json FROM card_captures WHERE job_id=? AND raw_json IS NOT NULL", (job_id,)
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("origin") == BOOTSTRAP_ORIGIN:
            return True
    return False


def _matching_existing(conn: sqlite3.Connection, candidate: BootstrapCandidate):
    by_id = conn.execute(
        "SELECT * FROM jobs WHERE source=? AND source_job_id=?",
        (candidate.source, candidate.source_job_id),
    ).fetchone()
    if by_id:
        return "jobs", by_id
    by_url = conn.execute(
        "SELECT * FROM jobs WHERE source=? AND canonical_url=?",
        (candidate.source, candidate.canonical_url),
    ).fetchone()
    if by_url:
        return "jobs", by_url
    tombstone = conn.execute(
        "SELECT * FROM job_tombstones WHERE source=? AND source_job_id=?",
        (candidate.source, candidate.source_job_id),
    ).fetchone()
    if tombstone:
        return "job_tombstones", tombstone
    tombstone = conn.execute(
        "SELECT * FROM job_tombstones WHERE source=? AND canonical_url=?",
        (candidate.source, candidate.canonical_url),
    ).fetchone()
    if tombstone:
        return "job_tombstones", tombstone
    return None, None


def _prefer_existing_market_evidence(
    candidate: BootstrapCandidate, existing: sqlite3.Row | None
) -> BootstrapCandidate:
    if existing is None:
        return candidate

    keys = set(existing.keys())

    def existing_value(column: str, fallback: str | None) -> str | None:
        if column not in keys:
            return fallback
        return _clean(existing[column]) or fallback

    return replace(
        candidate,
        canonical_url=existing_value("canonical_url", candidate.canonical_url)
        or candidate.canonical_url,
        title=existing_value("title", candidate.title),
        employer=existing_value("employer", candidate.employer),
        location=existing_value("location", candidate.location),
        salary_text=existing_value("salary_text", candidate.salary_text),
        employment_type=existing_value("employment_type", candidate.employment_type),
        workplace_type=existing_value("workplace_type", candidate.workplace_type),
        posted_text=existing_value("posted_text", candidate.posted_text),
        posted_at=existing_value("posted_at", candidate.posted_at),
        teaser_text=existing_value("teaser_text", candidate.teaser_text),
    )


def _promote_url_only_identity(candidate: BootstrapCandidate) -> None:
    with db.connect() as conn:
        table, row = _matching_existing(conn, candidate)
        if not row or _clean(row["source_job_id"]):
            return
        conn.execute(
            f"UPDATE {table} SET source_job_id=?, identity_key=? WHERE id=?",
            (candidate.source_job_id, candidate.identity_key, row["id"]),
        )


def _restore_market_freshness(
    job_id: int,
    *,
    prior_first_seen: str | None,
    prior_last_seen: str | None,
    candidate: BootstrapCandidate,
) -> None:
    first_values = [
        value for value in (prior_first_seen, candidate.first_seen_at) if _normalise_timestamp(value)
    ]
    last_values = [
        value for value in (prior_last_seen, candidate.last_seen_at) if _normalise_timestamp(value)
    ]
    first_seen = min(first_values, key=lambda value: _timestamp_key(_normalise_timestamp(value))) if first_values else None
    last_seen = max(last_values, key=lambda value: _timestamp_key(_normalise_timestamp(value))) if last_values else None
    if first_seen is None and last_seen is None:
        return
    with db.connect() as conn:
        current = conn.execute("SELECT first_seen_at, last_seen_at FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not current:
            return
        conn.execute(
            "UPDATE jobs SET first_seen_at=?, last_seen_at=? WHERE id=?",
            (first_seen or current["first_seen_at"], last_seen or current["last_seen_at"], job_id),
        )


def apply_bootstrap(plan: BootstrapPlan) -> BootstrapReport:
    db.init_db()
    report = BootstrapReport(**{**plan.report.as_dict(), "mode": "apply"})
    for candidate in plan.candidates:
        if candidate.identity_key in plan.blocked_identities:
            continue

        with db.connect() as conn:
            table, existing = _matching_existing(conn, candidate)
            prior_first = existing["first_seen_at"] if table == "jobs" and existing else None
            prior_last = existing["last_seen_at"] if table == "jobs" and existing else None
            if table == "jobs" and existing and _bootstrap_capture_exists(conn, int(existing["id"])):
                report.jobs_already_imported += 1
                if candidate.full_description:
                    existing_jd = _clean(existing["full_description"])
                    if existing_jd:
                        report.jds_already_present += 1
                    elif candidate.jd_fetched_at and candidate.jd_source:
                        db.store_job_jd_once(
                            int(existing["id"]),
                            full_description=candidate.full_description,
                            jd_fetched_at=candidate.jd_fetched_at,
                            jd_source=candidate.jd_source,
                        )
                        report.jds_stored += 1
                continue

        candidate = _prefer_existing_market_evidence(candidate, existing)
        _promote_url_only_identity(candidate)
        captured_at = candidate.last_seen_at or candidate.first_seen_at or datetime.now(UTC).isoformat(
            timespec="seconds"
        )
        result = ingest_card(
            CardObservation(
                source=candidate.source,
                source_job_id=candidate.source_job_id,
                canonical_url=candidate.canonical_url,
                title=candidate.title,
                employer=candidate.employer,
                location=candidate.location,
                salary_text=candidate.salary_text,
                employment_type=candidate.employment_type,
                workplace_type=candidate.workplace_type,
                posted_text=candidate.posted_text,
                posted_at=candidate.posted_at,
                reposted=candidate.reposted,
                teaser_text=candidate.teaser_text,
                captured_at=captured_at,
                raw_json={
                    "origin": BOOTSTRAP_ORIGIN,
                    "source_job_key": candidate.identity_key,
                },
            )
        )
        if result.created:
            report.jobs_created += 1
        elif result.resurrected:
            report.jobs_resurrected += 1
        else:
            report.jobs_updated += 1

        _restore_market_freshness(
            result.job_id,
            prior_first_seen=prior_first,
            prior_last_seen=prior_last,
            candidate=candidate,
        )
        if candidate.full_description and candidate.jd_fetched_at and candidate.jd_source:
            existing_jd = db.get_job_jd(result.job_id)
            if existing_jd is None:
                db.store_job_jd_once(
                    result.job_id,
                    full_description=candidate.full_description,
                    jd_fetched_at=candidate.jd_fetched_at,
                    jd_source=candidate.jd_source,
                )
                report.jds_stored += 1
            else:
                report.jds_already_present += 1

    return report


def write_report(path: Path, report: BootstrapReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def require_matching_dry_run(path: Path, job_hunter_db: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(
            f"JMM-006 apply refused: run the dry-run first so {path} exists and can be reviewed."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"JMM-006 dry-run report is unreadable: {path}") from exc
    expected = str(job_hunter_db.expanduser().resolve())
    if payload.get("mode") != "dry-run" or payload.get("source_db") != expected:
        raise RuntimeError(
            "JMM-006 apply refused: the dry-run report does not match this Job Hunter database."
        )
    return payload
