from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from collector.db import connect, init_db, resolve_primary_job_id
from collector.settings import get_setting

PUNCT_RE = re.compile(r"[^\w]+", re.UNICODE)
SAME_VACANCY_MATCH_TYPE = "same_vacancy"


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


def refresh_job_fingerprints(job_id: int) -> dict[str, Any]:
    """Recompute one source row after detail enrichment adds card evidence."""
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        current = dict(row)
        core, exact = fingerprints(current)
        conn.execute(
            "UPDATE jobs SET core_fingerprint=?, exact_card_fingerprint=? WHERE id=?",
            (core, exact, job_id),
        )
        current["core_fingerprint"] = core
        current["exact_card_fingerprint"] = exact
    return current


def _same_nonempty(a: Any, b: Any) -> bool:
    left, right = normalize(a), normalize(b)
    return bool(left and right and left == right)


def _teaser_similarity(a: Any, b: Any, *, min_chars: int = 25) -> float | None:
    left, right = normalize(a), normalize(b)
    if len(left) < min_chars or len(right) < min_chars:
        return None
    return SequenceMatcher(None, left, right).ratio()


def _secondary_signals(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    signals = (
        ("location", "same location"),
        ("workplace_type", "same workplace type"),
        ("employment_type", "same employment type"),
        ("salary_text", "same salary text"),
        ("classification_text", "same classification"),
    )
    return [
        label
        for field, label in signals
        if _same_nonempty(a.get(field), b.get(field))
    ]


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

    secondary_signals = _secondary_signals(a, b)
    if "same location" in secondary_signals:
        score += 0.08
        reasons.append("same location")
    if "same employment type" in secondary_signals:
        score += 0.04
        reasons.append("same employment type")
    if "same workplace type" in secondary_signals:
        score += 0.03
        reasons.append("same workplace type")
    if "same salary text" in secondary_signals:
        score += 0.04
        reasons.append("same salary text")
    if "same classification" in secondary_signals:
        score += 0.03
        reasons.append("same classification")

    teaser_similarity = _teaser_similarity(a.get("teaser_text"), b.get("teaser_text"))
    if teaser_similarity is not None:
        score += 0.12 * teaser_similarity
        reasons.append(f"teaser similarity {teaser_similarity:.2f}")

    return min(score, 0.999), "near_rich_card", reasons


def same_vacancy_evidence(
    a: dict[str, Any],
    b: dict[str, Any],
    *,
    teaser_min_similarity: float,
    teaser_min_chars: int,
    min_secondary_signals: int,
) -> tuple[float, str, list[str]] | None:
    """Return evidence for the explicit JMM-008 same-vacancy rules."""
    if a.get("id") == b.get("id"):
        return None
    if not _same_nonempty(a.get("title"), b.get("title")):
        return None
    if not _same_nonempty(a.get("employer"), b.get("employer")):
        return None

    reasons = ["same normalized title", "same normalized employer"]
    exact_a = a.get("exact_card_fingerprint") or exact_card_fingerprint_from_mapping(a)
    exact_b = b.get("exact_card_fingerprint") or exact_card_fingerprint_from_mapping(b)
    if exact_a and exact_a == exact_b:
        return 1.0, "exact_rich_card", reasons + ["same rich-card fingerprint"]

    teaser_similarity = _teaser_similarity(
        a.get("teaser_text"), b.get("teaser_text"), min_chars=teaser_min_chars
    )
    if teaser_similarity is not None and teaser_similarity >= teaser_min_similarity:
        return (
            teaser_similarity,
            "strong_teaser",
            reasons + [f"substantial teaser similarity {teaser_similarity:.2f}"],
        )

    secondary_signals = _secondary_signals(a, b)
    if len(secondary_signals) >= min_secondary_signals:
        duplicate = duplicate_evidence(a, b)
        if duplicate is None:
            return None
        score, _, _ = duplicate
        return score, "secondary_signals", reasons + secondary_signals
    return None


def _same_vacancy_matches(
    conn,
    current: dict[str, Any],
    *,
    teaser_min_similarity: float,
    teaser_min_chars: int,
    min_secondary_signals: int,
) -> list[tuple[int, float, str, list[str]]]:
    core = current.get("core_fingerprint")
    if not core:
        return []
    matches = []
    for row in conn.execute(
        "SELECT * FROM jobs WHERE core_fingerprint=? AND id<>?",
        (core, current["id"]),
    ).fetchall():
        candidate = dict(row)
        evidence = same_vacancy_evidence(
            current,
            candidate,
            teaser_min_similarity=teaser_min_similarity,
            teaser_min_chars=teaser_min_chars,
            min_secondary_signals=min_secondary_signals,
        )
        if evidence is None:
            continue
        confidence, match_type, reasons = evidence
        matches.append((int(candidate["id"]), confidence, match_type, reasons))
    return matches


def _record_same_vacancy_link(
    conn,
    job_id: int,
    candidate_id: int,
    *,
    confidence: float,
    match_type: str,
    reasons: list[str],
    detected_at: str,
) -> int:
    """Attach both source postings to the oldest primary and retain the audit trail."""
    current_primary = resolve_primary_job_id(conn, job_id)
    candidate_primary = resolve_primary_job_id(conn, candidate_id)
    primary_id = min(current_primary, candidate_primary)
    roots = {current_primary, candidate_primary, int(job_id), int(candidate_id)}
    placeholders = ",".join("?" for _ in roots)
    members = [
        int(row[0])
        for row in conn.execute(
            f"SELECT id FROM jobs WHERE id IN ({placeholders}) OR primary_job_id IN ({placeholders})",
            (*roots, *roots),
        ).fetchall()
    ]
    for member_id in set(members):
        if member_id != primary_id:
            conn.execute(
                "UPDATE jobs SET primary_job_id=? WHERE id=?",
                (primary_id, member_id),
            )
            conn.execute(
                "UPDATE same_vacancy_links SET primary_job_id=? WHERE job_id=?",
                (primary_id, member_id),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO same_vacancy_links(
                    job_id, primary_job_id, confidence, match_type,
                    matching_signals_json, detected_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    member_id,
                    primary_id,
                    confidence,
                    match_type,
                    json.dumps(reasons, ensure_ascii=False),
                    detected_at,
                ),
            )

    if int(job_id) == primary_id:
        linked_job_id = int(candidate_id)
    else:
        linked_job_id = int(job_id)
    if linked_job_id == primary_id:
        return primary_id
    conn.execute(
        """
        INSERT INTO same_vacancy_links(
            job_id, primary_job_id, confidence, match_type,
            matching_signals_json, detected_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            primary_job_id=excluded.primary_job_id,
            confidence=excluded.confidence,
            match_type=excluded.match_type,
            matching_signals_json=excluded.matching_signals_json,
            detected_at=excluded.detected_at
        """,
        (
            linked_job_id,
            primary_id,
            confidence,
            match_type,
            json.dumps(reasons, ensure_ascii=False),
            detected_at,
        ),
    )
    return primary_id


def establish_same_vacancy(job_id: int) -> int:
    """Assign a strong evidence match to one oldest primary, or keep it standalone."""
    init_db()
    if not bool(get_setting("dedupe.enable_near_match")):
        return int(job_id)
    teaser_min_similarity = float(
        get_setting("dedupe.same_vacancy_teaser_min_similarity")
    )
    teaser_min_chars = int(get_setting("dedupe.same_vacancy_teaser_min_chars"))
    min_secondary_signals = int(
        get_setting("dedupe.same_vacancy_min_secondary_signals")
    )
    detected_at = datetime.now(UTC).isoformat(timespec="seconds")
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        current = dict(row)
        matches = _same_vacancy_matches(
            conn,
            current,
            teaser_min_similarity=teaser_min_similarity,
            teaser_min_chars=teaser_min_chars,
            min_secondary_signals=min_secondary_signals,
        )
        if not matches:
            return resolve_primary_job_id(conn, job_id)
        candidate_id, confidence, match_type, reasons = min(
            matches,
            key=lambda item: (resolve_primary_job_id(conn, item[0]), -item[1], item[0]),
        )
        return _record_same_vacancy_link(
            conn,
            job_id,
            candidate_id,
            confidence=confidence,
            match_type=match_type,
            reasons=reasons,
            detected_at=detected_at,
        )


def refresh_duplicate_links(job_id: int) -> int:
    """Record duplicate evidence and establish confirmed same-vacancy primaries."""
    init_db()
    if not bool(get_setting("dedupe.enable_near_match")):
        return 0
    threshold = float(get_setting("dedupe.near_match_min_score"))
    detected_at = datetime.now(UTC).isoformat(timespec="seconds")

    establish_same_vacancy(job_id)

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
