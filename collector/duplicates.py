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
GENERIC_LOCATION_NAMES = {
    "australia",
    "new south wales",
    "nsw",
    "queensland",
    "qld",
    "australian capital territory",
    "act",
}
LOCATION_STATE_SUFFIXES = (
    " australian capital territory",
    " new south wales",
    " queensland",
    " australia",
    " nsw",
    " qld",
    " act",
)
# Some boards publish an Australian Capital Territory suburb while another uses
# the Canberra metro label. Treat those as the same region for cross-board
# duplicate checks; do not generalise this to every suburb in a state.
LOCATION_REGION_ALIASES = {
    "australian capital territory": "canberra",
    "act": "canberra",
    "canberra": "canberra",
}


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


def specific_locality(value: Any) -> str:
    """Normalize a specific leading locality without treating a state as one."""
    if value is None:
        return ""
    locality = normalize(str(value).split(",", 1)[0])
    for suffix in LOCATION_STATE_SUFFIXES:
        if locality.endswith(suffix):
            locality = locality[: -len(suffix)].strip()
            break
    if locality in GENERIC_LOCATION_NAMES:
        return ""
    return locality


def location_region(value: Any) -> str:
    """Return a conservative metro/region key when the source names one."""
    if value is None:
        return ""
    text = normalize(value)
    for alias, region in LOCATION_REGION_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return region
    return ""


def _locations_conflict(a: Any, b: Any) -> bool:
    """Reject only a proven location conflict, not suburb-versus-metro wording."""
    locality_a = specific_locality(a)
    locality_b = specific_locality(b)
    if not locality_a or not locality_b or locality_a == locality_b:
        return False
    region_a = location_region(a)
    region_b = location_region(b)
    return not (region_a and region_a == region_b)


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

    locality_a = specific_locality(a.get("location"))
    locality_b = specific_locality(b.get("location"))
    if _locations_conflict(a.get("location"), b.get("location")):
        return None

    teaser_similarity = _teaser_similarity(
        a.get("teaser_text"), b.get("teaser_text"), min_chars=teaser_min_chars
    )
    if teaser_similarity is not None and teaser_similarity >= teaser_min_similarity:
        return (
            teaser_similarity,
            "strong_teaser",
            reasons + [f"substantial teaser similarity {teaser_similarity:.2f}"],
        )

    source_a = normalize(a.get("source"))
    source_b = normalize(b.get("source"))
    region_a = location_region(a.get("location"))
    region_b = location_region(b.get("location"))
    same_location_region = region_a and region_a == region_b
    if (
        source_a
        and source_b
        and source_a != source_b
        and locality_a
        and (locality_a == locality_b or same_location_region)
    ):
        location_reason = (
            f"same specific locality {locality_a}"
            if locality_a == locality_b
            else f"same metro region {region_a}"
        )
        return (
            0.96,
            "cross_source_locality",
            reasons + [location_reason],
        )

    secondary_signals = _secondary_signals(a, b)
    if len(secondary_signals) >= min_secondary_signals:
        duplicate = duplicate_evidence(a, b)
        if duplicate is None:
            return None
        score, _, _ = duplicate
        return score, "secondary_signals", reasons + secondary_signals
    return None


def _cross_source_locality_is_reciprocally_unique(
    conn, current: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    """Require one matching source row on each side before locality-only grouping."""
    core = current.get("core_fingerprint")
    current_source = str(current.get("source") or "")
    candidate_source = str(candidate.get("source") or "")
    locality = specific_locality(current.get("location"))
    region = location_region(current.get("location"))
    candidate_locality = specific_locality(candidate.get("location"))
    candidate_region = location_region(candidate.get("location"))
    if (
        not core
        or not locality
        or not current_source
        or not candidate_source
        or current_source == candidate_source
        or not (
            locality == candidate_locality
            or (region and region == candidate_region)
        )
    ):
        return False
    rows = conn.execute(
        "SELECT id, source, location FROM jobs WHERE core_fingerprint=? AND source IN (?, ?)",
        (core, current_source, candidate_source),
    ).fetchall()
    by_source = {current_source: set(), candidate_source: set()}
    for row in rows:
        source = str(row["source"] or "")
        row_locality = specific_locality(row["location"])
        row_region = location_region(row["location"])
        if source in by_source and (
            row_locality == locality or (region and row_region == region)
        ):
            # Multiple captures of one source posting may already share a
            # primary. Count vacancy groups, not raw source rows.
            by_source[source].add(resolve_primary_job_id(conn, int(row["id"])))
    return (
        by_source[current_source] == {resolve_primary_job_id(conn, int(current["id"]))}
        and by_source[candidate_source]
        == {resolve_primary_job_id(conn, int(candidate["id"]))}
    )


def _group_specific_localities(conn, job_id: int) -> set[str]:
    primary_id = resolve_primary_job_id(conn, job_id)
    rows = conn.execute(
        "SELECT location FROM jobs WHERE id=? OR primary_job_id=?",
        (primary_id, primary_id),
    ).fetchall()
    return {
        locality
        for row in rows
        if (locality := specific_locality(row["location"]))
    }


def _groups_have_conflicting_localities(conn, job_id: int, candidate_id: int) -> bool:
    primary_id = resolve_primary_job_id(conn, job_id)
    candidate_primary_id = resolve_primary_job_id(conn, candidate_id)
    current_locations = [
        row["location"]
        for row in conn.execute(
            "SELECT location FROM jobs WHERE id=? OR primary_job_id=?",
            (primary_id, primary_id),
        ).fetchall()
    ]
    candidate_locations = [
        row["location"]
        for row in conn.execute(
            "SELECT location FROM jobs WHERE id=? OR primary_job_id=?",
            (candidate_primary_id, candidate_primary_id),
        ).fetchall()
    ]
    return any(
        _locations_conflict(current_location, candidate_location)
        for current_location in current_locations
        for candidate_location in candidate_locations
    )


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
        if _groups_have_conflicting_localities(conn, int(current["id"]), int(candidate["id"])):
            continue
        confidence, match_type, reasons = evidence
        if (
            match_type == "cross_source_locality"
            and not any(reason.startswith("same metro region ") for reason in reasons)
            and not _cross_source_locality_is_reciprocally_unique(
                conn, current, candidate
            )
        ):
            continue
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


def rebuild_same_vacancy_links() -> int:
    """Rebuild canonical vacancy grouping from the current deterministic rules."""
    init_db()
    with connect() as conn:
        ids = [int(row[0]) for row in conn.execute("SELECT id FROM jobs ORDER BY id")]
        conn.execute("DELETE FROM same_vacancy_links")
        conn.execute("UPDATE jobs SET primary_job_id=NULL")
    for job_id in ids:
        establish_same_vacancy(job_id)
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM same_vacancy_links").fetchone()[0])


def repair_conflicting_same_vacancy_groups() -> tuple[int, int, int]:
    """Rebuild only groups that currently contain conflicting specific localities."""
    init_db()
    grouped: dict[int, list[tuple[int, str]]] = {}
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, primary_job_id, location
              FROM jobs
             WHERE primary_job_id IS NOT NULL
                OR id IN (SELECT DISTINCT primary_job_id FROM jobs WHERE primary_job_id IS NOT NULL)
            """
        ).fetchall()
        for row in rows:
            root = int(row["primary_job_id"] or row["id"])
            grouped.setdefault(root, []).append(
                (int(row["id"]), specific_locality(row["location"]))
            )
        bad_roots = {
            root
            for root, members in grouped.items()
            if len({locality for _, locality in members if locality}) > 1
        }
        member_ids = sorted(
            {job_id for root in bad_roots for job_id, _ in grouped[root]}
        )
        if member_ids:
            conn.execute("CREATE TEMP TABLE repair_same_vacancy_ids(id INTEGER PRIMARY KEY)")
            conn.executemany(
                "INSERT INTO repair_same_vacancy_ids(id) VALUES(?)",
                [(job_id,) for job_id in member_ids],
            )
            conn.execute(
                "DELETE FROM same_vacancy_links WHERE job_id IN (SELECT id FROM repair_same_vacancy_ids) OR primary_job_id IN (SELECT id FROM repair_same_vacancy_ids)"
            )
            conn.execute(
                "UPDATE jobs SET primary_job_id=NULL WHERE id IN (SELECT id FROM repair_same_vacancy_ids)"
            )
    for job_id in member_ids:
        establish_same_vacancy(job_id)
    with connect() as conn:
        final_links = int(conn.execute("SELECT COUNT(*) FROM same_vacancy_links").fetchone()[0])
    return len(bad_roots), len(member_ids), final_links
