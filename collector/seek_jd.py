from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from collector.browser_broker import (
    BrowserBrokerError,
    navigate,
    seek_job_detail,
    select_page,
)
from collector.db import connect, store_job_jd_once, update_job_source_facts

SYDNEY = ZoneInfo("Australia/Sydney")
HUMAN_CHECK_WAIT_SECONDS = 900.0


class SeekJDFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeekFetchedDetail:
    full_description: str
    facts: dict[str, object]


@dataclass(frozen=True)
class SeekJDEnrichmentResult:
    candidates: int
    cached: int
    attempted: int
    stored: int
    failed: int
    remaining: int


def _now() -> str:
    return datetime.now(SYDNEY).isoformat(timespec="seconds")


def _clean(value: object) -> object | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _seek_job_id(url: str) -> str:
    match = re.search(r"/job/(\d+)", str(url or ""))
    return match.group(1) if match else ""


def _seek_fetch_url(source_job_id: str, fallback_url: str) -> str:
    return f"https://au.seek.com/job/{source_job_id}" if source_job_id else fallback_url


def fetch_seek_detail(
    page_id: int,
    url: str,
    *,
    expected_source_job_id: str | None = None,
    timeout_seconds: float = 15.0,
    human_wait_seconds: float = HUMAN_CHECK_WAIT_SECONDS,
) -> SeekFetchedDetail:
    """Read one SEEK JD and explicit structured source facts from the signed-in page."""
    expected_id = str(expected_source_job_id or _seek_job_id(url)).strip()
    target_url = _seek_fetch_url(expected_id, url)
    navigate(page_id, target_url)

    normal_deadline = time.monotonic() + timeout_seconds
    human_deadline: float | None = None
    brought_forward = False
    last_problem = "SEEK job detail did not become readable"

    while True:
        raw = seek_job_detail(page_id).result
        if not isinstance(raw, dict):
            last_problem = "SEEK detail command returned no structured result"
        else:
            page_url = str(raw.get("page_url") or "").strip()
            actual_page_id = _seek_job_id(page_url)
            if expected_id and actual_page_id and actual_page_id != expected_id:
                raise BrowserBrokerError(
                    f"SEEK tab ownership lost: expected job {expected_id}, browser is on {page_url!r}"
                )

            if bool(raw.get("human_check")):
                last_problem = f"SEEK needs human attention on job {expected_id or page_url}"
                if time.monotonic() >= normal_deadline:
                    if not brought_forward:
                        select_page(page_id, bring_to_front=True)
                        print(last_problem + "; brought dedicated JMM tab to front.", flush=True)
                        brought_forward = True
                        human_deadline = time.monotonic() + human_wait_seconds
                    elif human_deadline is not None and time.monotonic() >= human_deadline:
                        raise BrowserBrokerError(last_problem + "; human-check wait expired")
                time.sleep(1.0 if brought_forward else 0.35)
                continue

            actual_id = str(raw.get("source_job_id") or "").strip()
            if expected_id and actual_id != expected_id:
                raise SeekJDFetchError(
                    f"SEEK detail identity mismatch: expected {expected_id!r}, got {actual_id!r}"
                )

            description = str(raw.get("full_description") or "").strip()
            if len(description) < 80:
                last_problem = "SEEK returned an implausibly short JD"
            else:
                # Only explicit structured source facts belong here. Relative display
                # labels and inferred/normalised employment basis are deliberately excluded.
                facts: dict[str, object] = {}
                for key in (
                    "source_job_id",
                    "title",
                    "employer",
                    "location",
                    "classification_text",
                    "subclassification_text",
                    "employment_type",
                    "workplace_type",
                    "salary_text",
                    "posted_at",
                    "expires_at",
                    "source_status",
                    "easy_apply",
                    "apply_method",
                ):
                    cleaned = _clean(raw.get(key))
                    if cleaned is not None:
                        facts[key] = cleaned
                return SeekFetchedDetail(full_description=description, facts=facts)

        if time.monotonic() >= normal_deadline:
            raise SeekJDFetchError(last_problem)
        time.sleep(0.35)


def fetch_seek_jd(page_id: int, url: str, *, timeout_seconds: float = 15.0) -> str:
    return fetch_seek_detail(page_id, url, timeout_seconds=timeout_seconds).full_description


def _partition_matches_days(url: str, days: int) -> bool:
    values = parse_qs(urlsplit(url).query).get("daterange") or []
    return str(days) in values


def coverage_seek_jobs(*, codes: list[str], days: int) -> list[dict]:
    """Return unique SEEK jobs proven present in the requested coverage window."""
    if not codes:
        return []
    placeholders = ",".join("?" for _ in codes)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT j.id,j.identity_key,j.source_job_id,j.canonical_url,j.full_description,
                            p.url AS partition_url,
                            CASE WHEN r.identity_key IS NULL THEN 0 ELSE 1 END AS jd_fetch_completed
              FROM jobs j
              JOIN seek_partition_jobs spj ON spj.job_id=j.id
              JOIN seek_partitions p ON p.id=spj.partition_id
              LEFT JOIN jd_fetch_registry r ON r.identity_key=j.identity_key
             WHERE j.source='seek' AND p.geography_code IN ({placeholders})
             ORDER BY j.id
            """,
            codes,
        ).fetchall()
    by_id: dict[int, dict] = {}
    for row in rows:
        item = dict(row)
        if _partition_matches_days(str(item["partition_url"]), days):
            by_id[int(item["id"])] = item
    return list(by_id.values())


def all_unfetched_seek_jobs() -> list[dict]:
    """Return SEEK rows that have never had a successful JD fetch."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT j.id,j.identity_key,j.source_job_id,j.canonical_url,j.full_description,
                   CASE WHEN r.identity_key IS NULL THEN 0 ELSE 1 END AS jd_fetch_completed
              FROM jobs j
              LEFT JOIN jd_fetch_registry r ON r.identity_key=j.identity_key
             WHERE j.source='seek' AND r.identity_key IS NULL
             ORDER BY j.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _merge_candidates(coverage: list[dict], *, include_existing_unfetched: bool) -> list[dict]:
    by_id = {int(row["id"]): row for row in coverage}
    if include_existing_unfetched:
        for row in all_unfetched_seek_jobs():
            by_id.setdefault(int(row["id"]), row)
    return [by_id[key] for key in sorted(by_id)]


def enrich_seek_coverage_jds(
    *,
    page_id: int,
    codes: list[str],
    days: int,
    should_stop,
    deadline_reached,
    include_existing_unfetched: bool = False,
) -> SeekJDEnrichmentResult:
    rows = _merge_candidates(
        coverage_seek_jobs(codes=codes, days=days),
        include_existing_unfetched=include_existing_unfetched,
    )
    completed_ids = {
        int(row["id"]) for row in rows if int(row.get("jd_fetch_completed") or 0)
    }
    cached = len(completed_ids)
    attempted = stored = failed = 0

    for row in rows:
        job_id = int(row["id"])
        if job_id in completed_ids:
            continue
        if should_stop() or deadline_reached():
            break
        attempted += 1
        try:
            expected_source_id = str(row["source_job_id"] or "").strip()
            detail = fetch_seek_detail(
                page_id,
                str(row["canonical_url"]),
                expected_source_job_id=expected_source_id,
            )
            actual_source_id = str(detail.facts.get("source_job_id") or "").strip()
            if not expected_source_id or actual_source_id != expected_source_id:
                raise SeekJDFetchError(
                    f"SEEK detail identity mismatch: expected {expected_source_id!r}, got {actual_source_id!r}"
                )
            source_facts = {
                key: value for key, value in detail.facts.items() if key != "source_job_id"
            }
            update_job_source_facts(job_id, **source_facts)
            store_job_jd_once(
                job_id,
                full_description=detail.full_description,
                jd_fetched_at=_now(),
                jd_source="seek_job_page",
            )
            completed_ids.add(job_id)
            stored += 1
        except BrowserBrokerError:
            raise
        except SeekJDFetchError as exc:
            failed += 1
            print(f"SEEK JD fetch failed for job {job_id}: {exc}", flush=True)

    remaining = len(rows) - len(completed_ids)
    return SeekJDEnrichmentResult(
        candidates=len(rows),
        cached=cached,
        attempted=attempted,
        stored=stored,
        failed=failed,
        remaining=remaining,
    )
