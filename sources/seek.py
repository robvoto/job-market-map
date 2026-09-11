from __future__ import annotations

import re
from typing import Any

from collector.models import CardObservation

JOB_ID_RE = re.compile(r"/job/(\d{7,10})", re.IGNORECASE)
LISTED_RE = re.compile(r"(?m)^Listed .+ ago$")
WORKPLACE_RE = re.compile(r"\((Hybrid|Remote|On-site|On site)\)\s*$", re.IGNORECASE)


class SeekParseError(RuntimeError):
    pass


def _ordered_job_links(elements: list[dict[str, Any]]) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for element in elements:
        href = str(element.get("href") or "")
        match = JOB_ID_RE.search(href)
        if not match:
            continue
        job_id = match.group(1)
        if job_id not in grouped:
            grouped[job_id] = {"source_job_id": job_id, "url": href, "title": ""}
            order.append(job_id)
        text = " ".join(str(element.get("text") or "").split()).strip()
        if text and not grouped[job_id]["title"]:
            grouped[job_id]["title"] = text
    return [grouped[job_id] for job_id in order]


def _card_blocks(text: str) -> list[str]:
    starts = [m.start() for m in LISTED_RE.finditer(text or "")]
    blocks: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        blocks.append(text[start:end].strip())
    return blocks


def _normal_lines(block: str) -> list[str]:
    return [line.strip() for line in block.splitlines() if line.strip()]


def _parse_block(block: str) -> dict[str, Any]:
    lines = _normal_lines(block)
    if len(lines) < 4 or lines[2].casefold() != "at":
        raise SeekParseError(f"unexpected SEEK card header: {lines[:6]!r}")

    posted_text, title, _, employer = lines[:4]
    tags: list[str] = []
    for known in ("New to you", "Strong applicant", "Be an early applicant"):
        if known in lines:
            tags.append(known)

    employment_type = None
    employment_index = None
    for i, line in enumerate(lines):
        m = re.fullmatch(r"This is a (.+?) job", line, re.IGNORECASE)
        if m:
            employment_type = m.group(1).strip()
            employment_index = i
            break

    location = None
    workplace_type = None
    content_start = None
    if employment_index is not None and employment_index + 1 < len(lines):
        location = lines[employment_index + 1]
        m = WORKPLACE_RE.search(location)
        if m:
            workplace_type = m.group(1).replace("On site", "On-site")
            location = WORKPLACE_RE.sub("", location).strip()
        content_start = employment_index + 2

    subclassification = None
    classification = None
    class_index = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("subClassification:"):
            subclassification = line.split(":", 1)[1].strip() or None
            class_index = min(class_index, i)
        elif line.startswith("classification:"):
            classification = line.split(":", 1)[1].strip() or None
            class_index = min(class_index, i)

    content = lines[content_start:class_index] if content_start is not None else []
    salary_text = None
    if content:
        first = content[0]
        if "$" in first or re.search(
            r"\b(?:competitive|salary|package|super|p\.a\.|per (?:hour|day|annum)|annual)\b",
            first,
            re.IGNORECASE,
        ):
            salary_text = first
            content = content[1:]

    # Remove recruiter attribution from teaser while retaining it in metadata/raw evidence.
    recruiter = None
    if "Recruited by" in content:
        idx = content.index("Recruited by")
        if idx + 1 < len(content):
            recruiter = content[idx + 1]
        content = content[:idx]

    return {
        "posted_text": posted_text,
        "title": title,
        "employer": employer,
        "employment_type": employment_type,
        "location": location,
        "workplace_type": workplace_type,
        "salary_text": salary_text,
        "teaser_text": "\n".join(content).strip() or None,
        "classification_text": classification,
        "subclassification_text": subclassification,
        "card_tags": tags,
        "recruiter": recruiter,
    }


def parse_seek_snapshot(
    snapshot: dict[str, Any],
    *,
    query_text: str | None,
    query_location: str | None,
    page_number: int = 1,
    captured_at: str | None = None,
    geography_code: str | None = None,
) -> list[CardObservation]:
    text = str(snapshot.get("text") or "")
    elements = list(snapshot.get("elements") or [])
    links = _ordered_job_links(elements)
    blocks = _card_blocks(text)

    if not links or not blocks:
        raise SeekParseError(
            f"SEEK snapshot has no parseable cards: links={len(links)} blocks={len(blocks)}"
        )
    if len(links) != len(blocks):
        raise SeekParseError(
            f"SEEK card/link count mismatch: links={len(links)} blocks={len(blocks)}"
        )

    observations: list[CardObservation] = []
    for rank, (link, block) in enumerate(zip(links, blocks, strict=True), start=1):
        parsed = _parse_block(block)
        link_title = " ".join(link["title"].split()).casefold()
        block_title = " ".join(parsed["title"].split()).casefold()
        if link_title and link_title != block_title:
            raise SeekParseError(
                f"SEEK title-order mismatch at rank {rank}: link={link['title']!r} block={parsed['title']!r}"
            )
        observations.append(
            CardObservation(
                source="seek",
                source_job_id=link["source_job_id"],
                canonical_url=link["url"],
                title=parsed["title"],
                employer=parsed["employer"],
                location=parsed["location"],
                geography_code=geography_code,
                salary_text=parsed["salary_text"],
                employment_type=parsed["employment_type"],
                workplace_type=parsed["workplace_type"],
                posted_text=parsed["posted_text"],
                teaser_text=parsed["teaser_text"],
                raw_card_text=block,
                classification_text=parsed["classification_text"],
                subclassification_text=parsed["subclassification_text"],
                card_tags=parsed["card_tags"],
                raw_json={
                    "classification_text": parsed["classification_text"],
                    "subclassification_text": parsed["subclassification_text"],
                    "card_tags": parsed["card_tags"],
                    "recruiter": parsed["recruiter"],
                    "link_title": link["title"],
                },
                query_text=query_text,
                query_location=query_location,
                rank=rank,
                page_number=page_number,
                captured_at=captured_at,
            )
        )
    return observations


RESULT_COUNT_RE = re.compile(r"(?im)^([\d,]+)\s+.+?jobs?(?:\s+in\s+[^\n]+)?$")


def seek_result_count(text: str) -> int | None:
    """Return SEEK's headline result count from a result page."""
    for line in (text or "").splitlines()[:20]:
        match = re.search(
            r"^([\d,]+)\s+(?:.+?\s+)?jobs?(?:\s+in\s+.+)?$", line.strip(), re.IGNORECASE
        )
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def seek_refinement_links(
    snapshot: dict[str, Any], *, state_slug: str, level: str, current_url: str
) -> list[dict[str, str]]:
    """Extract disjoint SEEK child partitions from an expanded refinement snapshot."""
    from urllib.parse import urlsplit

    current_path = urlsplit(current_url).path.rstrip("/")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for element in snapshot.get("elements") or []:
        href = str(element.get("href") or "")
        label = " ".join(
            str(element.get("text") or element.get("ariaLabel") or "").split()
        ).strip()
        if not href or not label or href in seen:
            continue
        path = urlsplit(href).path.rstrip("/")
        child_level = None
        if level == "state":
            if re.fullmatch(
                rf"/jobs-in-[^/]+/in-{re.escape(state_slug)}", path, re.IGNORECASE
            ):
                child_level = "classification"
        elif level == "classification":
            m = re.fullmatch(
                rf"(/jobs-in-[^/]+)/([^/]+)/in-{re.escape(state_slug)}",
                path,
                re.IGNORECASE,
            )
            if m and not current_path.endswith("/" + m.group(2)):
                # The first path segment must match the selected classification.
                selected_prefix = current_path.split(f"/in-{state_slug}")[0]
                if m.group(1).casefold() == selected_prefix.casefold():
                    child_level = "subclassification"
        elif (
            level == "subclassification"
            and path.startswith(current_path + "/")
            and path.rsplit("/", 1)[-1].casefold()
            in {
                "full-time",
                "part-time",
                "contract-temp",
                "casual-vacation",
            }
        ):
            child_level = "work_type"
        if child_level:
            seen.add(href)
            rows.append({"level": child_level, "label": label, "url": href})
    return rows


def parse_seek_dom_cards(
    cards: list[dict[str, Any]],
    *,
    query_text: str | None,
    query_location: str | None,
    page_number: int = 1,
    captured_at: str | None = None,
    geography_code: str | None = None,
) -> list[CardObservation]:
    """Build neutral observations from SEEK's stable job-card DOM selectors."""
    observations: list[CardObservation] = []
    for rank, card in enumerate(cards, start=1):
        source_job_id = str(card.get("source_job_id") or "").strip()
        url = str(card.get("canonical_url") or "").strip()
        title = str(card.get("title") or "").strip()
        if not source_job_id or not JOB_ID_RE.search(url) or not title:
            raise SeekParseError(f"invalid SEEK DOM card at rank {rank}: {card!r}")
        easy_apply = card.get("easy_apply")
        observations.append(
            CardObservation(
                source="seek",
                source_job_id=source_job_id,
                canonical_url=url,
                title=title,
                employer=str(card.get("employer") or "").strip() or None,
                location=str(card.get("location") or "").strip() or None,
                geography_code=geography_code,
                salary_text=str(card.get("salary_text") or "").strip() or None,
                employment_type=str(card.get("employment_type") or "").strip() or None,
                workplace_type=str(card.get("workplace_type") or "").strip() or None,
                posted_text=str(card.get("posted_text") or "").strip() or None,
                posted_at=str(card.get("posted_at") or "").strip() or None,
                teaser_text=str(card.get("teaser_text") or "").strip() or None,
                raw_card_text=str(card.get("raw_card_text") or "").strip() or None,
                classification_text=str(card.get("classification_text") or "").strip()
                or None,
                subclassification_text=str(
                    card.get("subclassification_text") or ""
                ).strip()
                or None,
                apply_method=str(card.get("apply_method") or "").strip() or None,
                easy_apply=easy_apply if isinstance(easy_apply, bool) else None,
                card_tags=list(card.get("card_tags") or []),
                raw_json={
                    "classification_text": str(
                        card.get("classification_text") or ""
                    ).strip()
                    or None,
                    "subclassification_text": str(
                        card.get("subclassification_text") or ""
                    ).strip()
                    or None,
                    "card_tags": list(card.get("card_tags") or []),
                    "apply_method": str(card.get("apply_method") or "").strip() or None,
                    "recruiter": str(card.get("recruiter") or "").strip() or None,
                },
                query_text=query_text,
                query_location=query_location,
                rank=rank,
                page_number=page_number,
                captured_at=captured_at,
            )
        )
    if not observations:
        raise SeekParseError("SEEK DOM exposed no job cards")
    return observations
