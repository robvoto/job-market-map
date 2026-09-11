from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from collector.browser_broker import (
    BrowserBrokerError,
    BrowserBrokerTimeout,
    navigate,
    seek_job_detail,
    select_page,
)
from collector.db import connect, store_job_jd_once, update_job_source_facts
from collector.run_logging import collection_logger
from collector.settings import get_setting

SYDNEY = ZoneInfo("Australia/Sydney")
_CHALLENGE_MARKERS = (
    "help us keep seek secure",
    "confirm you are human",
    "verify you are human",
    "captcha",
    "performing security verification",
    "enable javascript and cookies to continue",
    "access denied",
)
_STOP_MARKERS = {
    "employer questions",
    "company profile",
    "report this job advert",
    "report this job ad",
    "be careful",
    "job seekers",
}


class SeekJDFetchError(RuntimeError):
    pass


class SeekJDUnavailableError(SeekJDFetchError):
    """SEEK explicitly says the source job cannot provide a JD."""

    def __init__(self, message: str, *, source_status: str) -> None:
        super().__init__(message)
        self.source_status = source_status


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
    unavailable: int = 0


def _now() -> str:
    return datetime.now(SYDNEY).isoformat(timespec="seconds")


def _clean_lines(page_text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip()
        for line in str(page_text or "").splitlines()
        if line.replace("\xa0", " ").strip()
    ]


def _strip_how_you_match(body: list[str]) -> list[str]:
    if not body or body[0].casefold() != "how you match":
        return body
    for index, line in enumerate(body[1:18], start=1):
        if re.match(r"^\+\d+\s+more", line, flags=re.IGNORECASE):
            return body[index + 1 :]

    start = 1
    if start < len(body) and "match your profile" in body[start].casefold():
        start += 1
    scan_limit = min(len(body), start + 14)
    for index in range(start, scan_limit):
        line = body[index]
        if len(line) >= 55 or re.search(r"[.!?]$", line):
            return body[index:]
    return body[start:]


def extract_seek_jd_text(page_text: str) -> str:
    """Extract the actual ad, excluding SEEK chrome, profile match and footer."""
    lines = _clean_lines(page_text)
    start = next(
        (i + 1 for i, line in enumerate(lines[:80]) if line.casefold() == "save"),
        None,
    )
    if start is None:
        raise SeekJDFetchError("SEEK job page did not expose the JD start boundary")
    stop = next(
        (i for i in range(start, len(lines)) if lines[i].casefold() in _STOP_MARKERS),
        len(lines),
    )
    body = _strip_how_you_match(lines[start:stop])
    text = "\n".join(body).strip()
    if len(text) < 80:
        raise SeekJDFetchError("SEEK job page exposed an implausibly short JD")
    return text


def _seek_job_id(url: str) -> str:
    match = re.search(r"/job/(\d+)", str(url or ""))
    return match.group(1) if match else ""


def _seek_fetch_url(source_job_id: str, fallback_url: str) -> str:
    return f"https://au.seek.com/job/{source_job_id}" if source_job_id else fallback_url


def _header(lines: list[str]) -> list[str]:
    stop = next(
        (i for i, line in enumerate(lines[:80]) if line.casefold() == "save"),
        len(lines),
    )
    return lines[:stop]


def _employment_type(header: list[str]) -> str | None:
    values = {
        "full time": "Full time",
        "full-time": "Full time",
        "part time": "Part time",
        "part-time": "Part time",
        "contract/temp": "Contract/Temp",
        "casual": "Casual",
    }
    for line in reversed(header):
        if line.casefold() in values:
            return values[line.casefold()]
    return None


def _location_and_workplace(header: list[str]) -> tuple[str | None, str | None]:
    for line in header:
        if not re.search(r"\b(?:NSW|ACT|QLD|VIC|SA|WA|TAS|NT)\b", line):
            continue
        workplace = None
        match = re.search(
            r"\((Hybrid|Remote|On[- ]site)\)\s*$", line, flags=re.IGNORECASE
        )
        location = line
        if match:
            workplace = match.group(1).replace("On site", "On-site")
            location = line[: match.start()].strip()
        return location or None, workplace
    return None, None


def _classification(header: list[str]) -> tuple[str | None, str | None]:
    for line in reversed(header):
        match = re.match(r"^(.+?)\s+\((.+)\)$", line)
        if not match:
            continue
        parent = match.group(2).strip()
        if parent.casefold() in {"hybrid", "remote", "on-site", "on site"}:
            continue
        return parent, match.group(1).strip()
    return None, None


def _salary(header: list[str], elements: list[dict]) -> str | None:
    for element in elements:
        label = str(element.get("ariaLabel") or "").strip()
        if label.casefold().startswith("salary"):
            return label
    for line in header:
        low = line.casefold()
        if (
            low == "salary undisclosed"
            or "$" in line
            or re.search(r"\b(?:per hour|per day|per annum|p\.a\.|p\.d\.)\b", low)
        ):
            return line
    return None


def parse_seek_detail_snapshot(
    snap: dict,
    *,
    expected_source_job_id: str | None = None,
    reference: datetime | None = None,
) -> SeekFetchedDetail:
    del reference
    page_url = str(snap.get("url") or "").strip()
    page_text = str(snap.get("text") or "")
    low = page_text.casefold()
    if any(marker in low for marker in _CHALLENGE_MARKERS):
        raise BrowserBrokerError("SEEK browser challenge while fetching JD")

    actual_id = _seek_job_id(page_url)
    expected_id = str(expected_source_job_id or "").strip()
    if expected_id and actual_id != expected_id:
        raise BrowserBrokerError(
            f"SEEK tab ownership lost: expected job {expected_id}, browser is on {page_url!r}"
        )
    if not actual_id:
        raise SeekJDFetchError(f"SEEK detail page has no job id: {page_url!r}")

    lines = _clean_lines(page_text)
    header = _header(lines)
    full_description = extract_seek_jd_text(page_text)
    employment_type = _employment_type(header)
    location, workplace_type = _location_and_workplace(header)
    classification_text, subclassification_text = _classification(header)
    elements = list(snap.get("elements") or [])
    quick_apply = any(
        str(element.get("text") or "").strip().casefold() == "quick apply"
        for element in elements
    ) or any(line.casefold() == "quick apply" for line in header)

    facts: dict[str, object] = {"source_job_id": actual_id}
    optional = {
        "location": location,
        "classification_text": classification_text,
        "subclassification_text": subclassification_text,
        "employment_type": employment_type,
        "workplace_type": workplace_type,
        "salary_text": _salary(header, elements),
        "easy_apply": True if quick_apply else None,
        "apply_method": "quick_apply" if quick_apply else None,
    }
    facts.update(
        {key: value for key, value in optional.items() if value not in (None, "")}
    )
    return SeekFetchedDetail(full_description=full_description, facts=facts)


def _exact_source_timestamp(value: object) -> str | None:
    """Accept only an exact structured source timestamp; never derive from relative text."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        return None
    return raw


def fetch_seek_detail(
    page_id: int,
    url: str,
    *,
    expected_source_job_id: str | None = None,
    timeout_seconds: float | None = None,
    human_wait_seconds: float | None = None,
) -> SeekFetchedDetail:
    """Read one SEEK JD + structured facts from JMM's dedicated Playwright tab."""
    expected_id = str(expected_source_job_id or _seek_job_id(url)).strip()
    fetch_url = _seek_fetch_url(expected_id, url)
    timeout_seconds = float(
        timeout_seconds
        if timeout_seconds is not None
        else get_setting("collection.seek_parse_wait_seconds")
    )
    human_wait_seconds = float(
        human_wait_seconds
        if human_wait_seconds is not None
        else get_setting("collection.seek_human_check_wait_seconds")
    )
    navigate(page_id, fetch_url)
    normal_deadline = time.monotonic() + timeout_seconds
    human_deadline: float | None = None
    human_mode = False
    incomplete_render_retries = 0
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
                last_problem = (
                    f"SEEK needs human attention for job {expected_id or page_url}"
                )
                if not human_mode:
                    select_page(page_id, bring_to_front=True)
                    collection_logger().warning(
                        "%s; JMM browser is waiting for verification", last_problem
                    )
                    human_mode = True
                    human_deadline = time.monotonic() + human_wait_seconds
                elif (
                    human_mode
                    and human_deadline is not None
                    and time.monotonic() >= human_deadline
                ):
                    raise BrowserBrokerError(
                        last_problem + "; human-check wait expired"
                    )
                time.sleep(1.0 if human_mode else 0.35)
                continue

            actual_id = str(raw.get("source_job_id") or "").strip()
            if bool(raw.get("terminal_unavailable")):
                terminal_status = str(raw.get("source_status") or "not_found").strip()
                raise SeekJDUnavailableError(
                    f"SEEK job {expected_id or actual_id or page_url} is unavailable ({terminal_status})",
                    source_status=terminal_status,
                )
            if expected_id and actual_id != expected_id:
                raise SeekJDFetchError(
                    f"SEEK detail identity mismatch: expected {expected_id!r}, got {actual_id!r}"
                )
            description = (
                str(raw.get("full_description") or "").replace("\xa0", " ").strip()
            )
            if len(description) < 80:
                last_problem = "SEEK returned an implausibly short JD"
            else:
                facts: dict[str, object] = {"source_job_id": actual_id}
                for key in (
                    "title",
                    "employer",
                    "location",
                    "classification_text",
                    "subclassification_text",
                    "employment_type",
                    "workplace_type",
                    "salary_text",
                    "expires_at",
                    "source_status",
                    "easy_apply",
                    "apply_method",
                ):
                    value = raw.get(key)
                    if isinstance(value, str):
                        value = value.strip()
                    if value not in (None, ""):
                        facts[key] = value
                posted_at = _exact_source_timestamp(raw.get("posted_at"))
                if posted_at:
                    facts["posted_at"] = posted_at
                return SeekFetchedDetail(full_description=description, facts=facts)

        if not human_mode and time.monotonic() >= normal_deadline:
            if (
                last_problem == "SEEK returned an implausibly short JD"
                and incomplete_render_retries == 0
            ):
                incomplete_render_retries += 1
                collection_logger().info(
                    "SEEK JD incomplete render; re-navigating once source_job_id=%s",
                    expected_id,
                )
                navigate(page_id, fetch_url)
                normal_deadline = time.monotonic() + timeout_seconds
                last_problem = "SEEK job detail did not become readable after re-navigation"
                continue
            raise SeekJDFetchError(last_problem)
        time.sleep(0.35)


def fetch_seek_jd(
    page_id: int, url: str, *, timeout_seconds: float | None = None
) -> str:
    return fetch_seek_detail(
        page_id, url, timeout_seconds=timeout_seconds
    ).full_description


def _partition_matches_days(url: str, days: int) -> bool:
    values = parse_qs(urlsplit(url).query).get("daterange") or []
    return str(days) in values


def coverage_seek_jobs(*, codes: list[str], days: int) -> list[dict]:
    if not codes:
        return []
    placeholders = ",".join("?" for _ in codes)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT j.id,j.identity_key,j.source_job_id,j.canonical_url,j.full_description,j.source_status,
                            p.url AS partition_url,
                            CASE WHEN r.identity_key IS NULL THEN 0 ELSE 1 END AS jd_fetch_completed
              FROM jobs j
              JOIN seek_partition_jobs spj ON spj.job_id=j.id
              JOIN seek_partitions p ON p.id=spj.partition_id
              LEFT JOIN jd_fetch_registry r ON r.identity_key=j.identity_key
             WHERE j.source='seek' AND COALESCE(j.source_status,'') NOT IN ('no_longer_advertised','not_found') AND p.geography_code IN ({placeholders})
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
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT j.id,j.identity_key,j.source_job_id,j.canonical_url,j.full_description,j.source_status,
                   CASE WHEN r.identity_key IS NULL THEN 0 ELSE 1 END AS jd_fetch_completed
              FROM jobs j
              LEFT JOIN jd_fetch_registry r ON r.identity_key=j.identity_key
             WHERE j.source='seek' AND r.identity_key IS NULL AND COALESCE(j.source_status,'') NOT IN ('no_longer_advertised','not_found')
             ORDER BY j.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _merge_candidates(
    coverage: list[dict], *, include_existing_unfetched: bool
) -> list[dict]:
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
    max_attempts: int | None = None,
    on_progress=None,
) -> SeekJDEnrichmentResult:
    rows = _merge_candidates(
        coverage_seek_jobs(codes=codes, days=days),
        include_existing_unfetched=include_existing_unfetched,
    )
    completed_ids = {
        int(row["id"]) for row in rows if int(row.get("jd_fetch_completed") or 0)
    }
    cached = len(completed_ids)
    attempted = stored = failed = unavailable = 0
    unavailable_ids: set[int] = set()

    for row in rows:
        job_id = int(row["id"])
        if job_id in completed_ids:
            continue
        if should_stop() or deadline_reached():
            break
        if max_attempts is not None and attempted >= max_attempts:
            break
        attempted += 1
        if on_progress is not None:
            on_progress("attempted")
        try:
            expected_source_id = str(row["source_job_id"] or "").strip()
            detail = None
            for navigation_attempt in range(2):
                try:
                    detail = fetch_seek_detail(
                        page_id,
                        str(row["canonical_url"]),
                        expected_source_job_id=expected_source_id,
                    )
                    break
                except BrowserBrokerTimeout as exc:
                    if navigation_attempt == 0:
                        collection_logger().info(
                            "SEEK JD navigation timeout; retrying once job_id=%s source_job_id=%s error=%s",
                            job_id,
                            expected_source_id,
                            exc,
                        )
                        continue
                    raise
            assert detail is not None
            actual_source_id = str(detail.facts.get("source_job_id") or "").strip()
            if not expected_source_id or actual_source_id != expected_source_id:
                raise SeekJDFetchError(
                    f"SEEK detail identity mismatch: expected {expected_source_id!r}, got {actual_source_id!r}"
                )
            update_job_source_facts(
                job_id,
                **{
                    key: value
                    for key, value in detail.facts.items()
                    if key != "source_job_id"
                },
            )
            store_job_jd_once(
                job_id,
                full_description=detail.full_description,
                jd_fetched_at=_now(),
                jd_source="seek_job_page",
            )
            completed_ids.add(job_id)
            stored += 1
            if on_progress is not None:
                on_progress("stored")
            if stored == 1 or stored % 25 == 0:
                collection_logger().info(
                    "JD progress stored=%s attempted=%s failed=%s job_id=%s source_job_id=%s jd_chars=%s",
                    stored,
                    attempted,
                    failed,
                    job_id,
                    expected_source_id,
                    len(detail.full_description),
                )
        except BrowserBrokerTimeout as exc:
            failed += 1
            if on_progress is not None:
                on_progress("failed")
            collection_logger().warning(
                "SEEK JD navigation timed out after retry; leaving retryable job_id=%s source_job_id=%s error=%s",
                job_id,
                str(row["source_job_id"] or "").strip(),
                exc,
            )
        except BrowserBrokerError:
            raise
        except SeekJDUnavailableError as exc:
            update_job_source_facts(job_id, source_status=exc.source_status)
            unavailable += 1
            unavailable_ids.add(job_id)
            if on_progress is not None:
                on_progress("unavailable")
            collection_logger().info(
                "SEEK JD unavailable job_id=%s source_job_id=%s status=%s",
                job_id,
                str(row["source_job_id"] or "").strip(),
                exc.source_status,
            )
        except SeekJDFetchError as exc:
            failed += 1
            if on_progress is not None:
                on_progress("failed")
            collection_logger().warning(
                "SEEK JD fetch failed job_id=%s error=%s", job_id, exc
            )

    remaining = len(rows) - len(completed_ids) - len(unavailable_ids)
    return SeekJDEnrichmentResult(
        candidates=len(rows),
        cached=cached,
        attempted=attempted,
        stored=stored,
        failed=failed,
        remaining=remaining,
        unavailable=unavailable,
    )
