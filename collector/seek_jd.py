from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from collector.browser_broker import BrowserBrokerError, navigate, snapshot
from collector.db import connect, store_job_jd_once

_CHALLENGE_MARKERS = (
    "help us keep seek secure",
    "confirm you are human",
    "enable javascript and cookies to continue",
    "security check",
    "access denied",
)
_STOP_MARKERS = (
    "employer questions",
    "report this job advert",
    "report this job ad",
    "be careful",
    "job seekers",
)
_START_MARKERS = ("save", "quick apply", "apply")


class SeekJDFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeekJDEnrichmentResult:
    candidates: int
    cached: int
    attempted: int
    stored: int
    failed: int
    remaining: int


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def extract_seek_jd_text(page_text: str) -> str:
    """Extract the actual SEEK ad body from a browser snapshot."""
    raw = str(page_text or "")
    low = raw.casefold()
    if any(marker in low for marker in _CHALLENGE_MARKERS):
        raise BrowserBrokerError("SEEK browser challenge while fetching JD")

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        raise SeekJDFetchError("SEEK job page has no visible text")

    start = None
    for index, line in enumerate(lines[:40]):
        if line.casefold() == "save":
            start = index + 1
            break
    if start is None:
        for index, line in enumerate(lines[:40]):
            if line.casefold() in _START_MARKERS:
                start = index + 1
    if start is None:
        raise SeekJDFetchError("SEEK job page did not expose the JD start boundary")

    stop = len(lines)
    for index in range(start, len(lines)):
        if lines[index].casefold() in _STOP_MARKERS:
            stop = index
            break

    text = "\n".join(lines[start:stop]).strip()
    if len(text) < 80:
        raise SeekJDFetchError("SEEK job page exposed an implausibly short JD")
    return text


def fetch_seek_jd(page_id: int, url: str, *, timeout_seconds: float = 15.0) -> str:
    navigate(page_id, url)
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        snap = snapshot(page_id, verbose=True).result or {}
        try:
            return extract_seek_jd_text(str(snap.get("text") or ""))
        except BrowserBrokerError:
            raise
        except SeekJDFetchError as exc:
            last_error = exc
            time.sleep(0.35)
    raise SeekJDFetchError(str(last_error or "SEEK JD did not become readable"))


def _partition_matches_days(url: str, days: int) -> bool:
    values = parse_qs(urlsplit(url).query).get("daterange") or []
    return str(days) in values


def coverage_seek_jobs(*, codes: list[str], days: int) -> list[dict]:
    """Return unique SEEK jobs proven present in the current coverage workspace."""
    if not codes:
        return []
    placeholders = ",".join("?" for _ in codes)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT j.id,j.identity_key,j.canonical_url,j.full_description,
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


def enrich_seek_coverage_jds(
    *,
    page_id: int,
    codes: list[str],
    days: int,
    should_stop,
    deadline_reached,
) -> SeekJDEnrichmentResult:
    rows = coverage_seek_jobs(codes=codes, days=days)
    cached = attempted = stored = failed = 0
    completed_ids = {int(row["id"]) for row in rows if int(row["jd_fetch_completed"])}
    cached = len(completed_ids)
    for row in rows:
        job_id = int(row["id"])
        if job_id in completed_ids:
            continue
        if should_stop() or deadline_reached():
            break
        attempted += 1
        try:
            jd = fetch_seek_jd(page_id, str(row["canonical_url"]))
            store_job_jd_once(
                job_id,
                full_description=jd,
                jd_fetched_at=_now(),
                jd_source="seek_job_page",
            )
            completed_ids.add(job_id)
            stored += 1
        except BrowserBrokerError:
            raise
        except SeekJDFetchError:
            failed += 1

    remaining = len(rows) - len(completed_ids)
    return SeekJDEnrichmentResult(
        candidates=len(rows),
        cached=cached,
        attempted=attempted,
        stored=stored,
        failed=failed,
        remaining=remaining,
    )
