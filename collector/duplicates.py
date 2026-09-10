from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from collector.db import connect, init_db
from collector.settings import get_setting

PUNCT_RE = re.compile(r"[^\w]+", re.UNICODE)


def normalize(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(PUNCT_RE.sub(" ", text).split())


def _hash(parts: list[str]) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def core_fingerprint_from_values(
    title: Any, employer: Any, location: Any = None
) -> str | None:
    # Candidate key intentionally excludes location. Cross-board location strings often
    # differ for the same vacancy (for example Sydney NSW vs Sydney, New South Wales).
    # Location remains evidence in duplicate_evidence(), not a prerequisite to compare.
    del location  # Kept in the signature for compatibility with existing callers.
    parts = [normalize(title), normalize(employer)]
    if not parts[0] or not parts[1]:
        return None
    return _hash(parts)


def exact_card_fingerprint_from_mapping(row: dict[str, Any]) -> str | None:
    fields = [
        "title",
        "employer",
        "location",
        "salary_text",
        "employment_type",
        "workplace_type",
        "classification_text",
        "subclassification_text",
        "teaser_text",
    ]
    parts = [normalize(row.get(field)) for field in fields]
    # Avoid declaring sparse rows exact merely because most card fields are absent.
    if sum(bool(part) for part in parts) < 4 or not parts[0] or not parts[1]:
        return None
    return _hash(parts)


def fingerprints(row: dict[str, Any]) -> tuple[str | None, str | None]:
    return (
        core_fingerprint_from_values(
            row.get("title"), row.get("employer"), row.get("location")
        ),
        exact_card_fingerprint_from_mapping(row),
    )


def _same_nonempty(a: Any, b: Any) -> bool:
    left, right = normalize(a), normalize(b)
    return bool(left and right and left == right)


def _teaser_similarity(a: Any, b: Any) -> float | None:
    left, right = normalize(a), normalize(b)
    if len(left) < 25 or len(right) < 25:
        return None
    return SequenceMatcher(None, left, right).ratio()


def duplicate_evidence(
    a: dict[str, Any], b: dict[str, Any]
) -> tuple[float, str, list[str]] | None:
    if a.get("id") == b.get("id"):
        return None
    if not _same_nonempty(a.get("title"), b.get("title")):
        return None
    if not _same_nonempty(a.get("employer"), b.get("employer")):
        return None

    exact_a = a.get("exact_card_fingerprint") or exact_card_fingerprint_from_mapping(a)
    exact_b = b.get("exact_card_fingerprint") or exact_card_fingerprint_from_mapping(b)
    if exact_a and exact_a == exact_b:
        return 1.0, "exact_rich_card", ["same rich-card fingerprint"]

    score = 0.72
    reasons = ["same normalized title", "same normalized employer"]

    if _same_nonempty(a.get("location"), b.get("location")):
        score += 0.08
        reasons.append("same location")
    if _same_nonempty(a.get("employment_type"), b.get("employment_type")):
        score += 0.04
        reasons.append("same employment type")
    if _same_nonempty(a.get("workplace_type"), b.get("workplace_type")):
        score += 0.03
        reasons.append("same workplace type")
    if _same_nonempty(a.get("salary_text"), b.get("salary_text")):
        score += 0.04
        reasons.append("same salary text")
    if _same_nonempty(a.get("classification_text"), b.get("classification_text")):
        score += 0.03
        reasons.append("same classification")

    teaser_similarity = _teaser_similarity(a.get("teaser_text"), b.get("teaser_text"))
    if teaser_similarity is not None:
        score += 0.12 * teaser_similarity
        reasons.append(f"teaser similarity {teaser_similarity:.2f}")

    return min(score, 0.999), "near_rich_card", reasons


def refresh_duplicate_links(job_id: int) -> int:
    """Record strong possible duplicates for one job without merging or deleting either row."""
    init_db()
    if not bool(get_setting("dedupe.enable_near_match")):
        return 0
    threshold = float(get_setting("dedupe.near_match_min_score"))
    detected_at = datetime.now(UTC).isoformat(timespec="seconds")

    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        current = dict(row)
        core = current.get("core_fingerprint")
        if not core:
            return 0
        candidates = conn.execute(
            "SELECT * FROM jobs WHERE core_fingerprint=? AND id<>?",
            (core, job_id),
        ).fetchall()
        written = 0
        for candidate_row in candidates:
            candidate = dict(candidate_row)
            evidence = duplicate_evidence(current, candidate)
            if not evidence:
                continue
            confidence, match_type, reasons = evidence
            if confidence < threshold and match_type != "exact_rich_card":
                continue
            a, b = sorted((job_id, int(candidate["id"])))
            conn.execute(
                """
                INSERT INTO duplicate_links(job_id_a, job_id_b, confidence, match_type, reasons_json, detected_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id_a, job_id_b) DO UPDATE SET
                    confidence=excluded.confidence,
                    match_type=excluded.match_type,
                    reasons_json=excluded.reasons_json,
                    detected_at=excluded.detected_at
                """,
                (a, b, confidence, match_type, json.dumps(reasons), detected_at),
            )
            written += 1
    return written


def backfill_fingerprints_and_duplicates() -> tuple[int, int]:
    init_db()
    updated = 0
    with connect() as conn:
        rows = conn.execute("SELECT * FROM jobs").fetchall()
        for row in rows:
            item = dict(row)
            core, exact = fingerprints(item)
            conn.execute(
                "UPDATE jobs SET core_fingerprint=?, exact_card_fingerprint=? WHERE id=?",
                (core, exact, item["id"]),
            )
            updated += 1
    links = 0
    with connect() as conn:
        ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM jobs WHERE core_fingerprint IS NOT NULL"
            )
        ]
    for job_id in ids:
        links += refresh_duplicate_links(job_id)
    return updated, links
