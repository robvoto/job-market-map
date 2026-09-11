from __future__ import annotations

import re
from typing import Any

from collector.models import CardObservation

JOB_ID_RE = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{7,12})", re.IGNORECASE)
WORKPLACE_RE = re.compile(r"\s+\((Hybrid|Remote|On-site|On site)\)\s*$", re.IGNORECASE)
RESULT_COUNT_RE = re.compile(r"(?m)^([\d,]+) results$")
VERIFY_SUFFIX_RE = re.compile(r"\s+with verification$", re.IGNORECASE)


class LinkedInParseError(RuntimeError):
    pass


def _clean_title(value: str) -> str:
    text = " ".join(value.split()).strip()
    text = VERIFY_SUFFIX_RE.sub("", text).strip()
    # Snapshot link text often duplicates the visible title twice.
    half = len(text) // 2
    if len(text) % 2 == 0 and text[:half].strip() == text[half:].strip():
        text = text[:half].strip()
    return text


def _ordered_job_links(elements: list[dict[str, Any]]) -> list[dict[str, str]]:
    jobs: list[dict[str, str]] = []
    seen: set[str] = set()
    for element in elements:
        href = str(element.get("href") or "")
        match = JOB_ID_RE.search(href)
        if not match:
            continue
        job_id = match.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        label = str(element.get("ariaLabel") or "")
        text = str(element.get("text") or "")
        title = _clean_title(label or text)
        jobs.append({"source_job_id": job_id, "url": href, "title": title})
    return jobs


def result_count(text: str) -> int | None:
    match = RESULT_COUNT_RE.search(text or "")
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _normal_lines(text: str) -> list[str]:
    return [
        " ".join(line.split()).strip()
        for line in (text or "").splitlines()
        if " ".join(line.split()).strip()
    ]


def _find_card_starts(lines: list[str], titles: list[str]) -> list[int]:
    starts: list[int] = []
    cursor = 0
    for title in titles:
        wanted = " ".join(title.split()).casefold()
        found = None
        for i in range(cursor, len(lines)):
            if lines[i].casefold() == wanted:
                found = i
                break
        if found is None:
            raise LinkedInParseError(
                f"could not locate LinkedIn card title in page text: {title!r}"
            )
        starts.append(found)
        cursor = found + 1
    return starts


def parse_linkedin_snapshot(
    snapshot: dict[str, Any],
    *,
    query_text: str,
    query_location: str,
    offset: int = 0,
    captured_at: str | None = None,
    geography_code: str | None = None,
) -> tuple[list[CardObservation], int | None]:
    text = str(snapshot.get("text") or "")
    elements = list(snapshot.get("elements") or [])
    links = _ordered_job_links(elements)
    if not links:
        raise LinkedInParseError("LinkedIn snapshot has no parseable job links")

    lines = _normal_lines(text)
    titles = [item["title"] for item in links]
    if any(not title for title in titles):
        raise LinkedInParseError("LinkedIn job link missing title")
    starts = _find_card_starts(lines, titles)

    observations: list[CardObservation] = []
    for index, (link, start) in enumerate(zip(links, starts, strict=True)):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        block_lines = lines[start:end]
        title = link["title"]
        pos = 1
        # Second rendered line repeats the title, sometimes with "with verification".
        if (
            pos < len(block_lines)
            and _clean_title(block_lines[pos]).casefold() == title.casefold()
        ):
            pos += 1
        if pos + 1 >= len(block_lines):
            raise LinkedInParseError(
                f"LinkedIn card lacks employer/location: {block_lines!r}"
            )
        employer = block_lines[pos]
        location = block_lines[pos + 1]
        metadata = block_lines[pos + 2 :]

        workplace_type = None
        match = WORKPLACE_RE.search(location)
        if match:
            workplace_type = match.group(1).replace("On site", "On-site")
            location = WORKPLACE_RE.sub("", location).strip()

        easy_apply = any(line.casefold() == "easy apply" for line in metadata)
        source_metadata = [line for line in metadata if line.casefold() != "viewed"]
        teaser_metadata = [
            line
            for line in source_metadata
            if line.casefold()
            not in {
                "promoted",
                "easy apply",
                "actively reviewing applicants",
                "be an early applicant",
            }
        ]
        raw_block = "\n".join(
            line for line in block_lines if line.casefold() != "viewed"
        )
        observations.append(
            CardObservation(
                source="linkedin",
                source_job_id=link["source_job_id"],
                canonical_url=link["url"],
                title=title,
                employer=employer,
                location=location,
                workplace_type=workplace_type,
                easy_apply=True if easy_apply else None,
                card_tags=None,
                teaser_text="\n".join(teaser_metadata).strip() or None,
                raw_card_text=raw_block,
                raw_json={
                    "offset": offset,
                    "metadata_lines": source_metadata,
                    "link_title": title,
                },
                query_text=query_text,
                query_location=query_location,
                rank=offset + index + 1,
                page_number=(offset // max(len(links), 1)) + 1,
                captured_at=captured_at,
            )
        )
    return observations, result_count(text)
