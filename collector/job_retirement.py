from __future__ import annotations

import json
from datetime import UTC, datetime

from collector.db import connect
from collector.run_logging import collection_logger
from collector.source_status import TERMINAL_SOURCE_STATUSES


def _write_tombstone(conn, job: dict, *, removed_at: str) -> None:
    state = conn.execute(
        "SELECT first_seen_at,last_seen_at,capture_count FROM job_observation_state WHERE job_id=?",
        (job["id"],),
    ).fetchone()
    first_seen_at = str(state["first_seen_at"] if state else removed_at)
    last_seen_at = str(state["last_seen_at"] if state else removed_at)
    capture_count = int(state["capture_count"] if state else 0)
    history = [dict(item) for item in conn.execute(
        """SELECT q.registry_key,q.source,q.query_text,q.location,h.first_seen_at,h.last_seen_at,h.hit_count
             FROM job_query_hits h JOIN queries q ON q.id=h.query_id
            WHERE h.job_id=? ORDER BY q.source,q.query_text,q.location""",
        (job["id"],),
    )]
    conn.execute(
        """INSERT INTO job_tombstones(
               source,source_job_id,identity_key,canonical_url,title,employer,location,
               core_fingerprint,exact_card_fingerprint,first_seen_at,last_seen_at,
               capture_count,query_history_json,removed_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(source,canonical_url) DO UPDATE SET
               source_job_id=excluded.source_job_id,identity_key=excluded.identity_key,
               title=excluded.title,employer=excluded.employer,location=excluded.location,
               core_fingerprint=excluded.core_fingerprint,
               exact_card_fingerprint=excluded.exact_card_fingerprint,
               first_seen_at=MIN(job_tombstones.first_seen_at,excluded.first_seen_at),
               last_seen_at=MAX(job_tombstones.last_seen_at,excluded.last_seen_at),
               capture_count=MAX(job_tombstones.capture_count,excluded.capture_count),
               query_history_json=excluded.query_history_json,removed_at=excluded.removed_at""",
        (job["source"], job["source_job_id"], job["identity_key"], job["canonical_url"],
         job["title"], job["employer"], job["location"], job["core_fingerprint"],
         job["exact_card_fingerprint"], first_seen_at, last_seen_at, capture_count,
         json.dumps(history, ensure_ascii=False), removed_at),
    )


def _transfer_primary_jd(conn, old_primary: dict, replacement: dict) -> None:
    jd = str(old_primary.get("full_description") or "").strip()
    if not jd or str(replacement.get("full_description") or "").strip():
        return
    conn.execute(
        "UPDATE jobs SET full_description=?,jd_fetched_at=?,jd_source=? WHERE id=?",
        (jd, old_primary.get("jd_fetched_at"), old_primary.get("jd_source"), replacement["id"]),
    )
    if replacement.get("identity_key") and old_primary.get("jd_fetched_at") and old_primary.get("jd_source"):
        conn.execute(
            """INSERT OR REPLACE INTO jd_fetch_registry(
                   identity_key,source,source_job_id,canonical_url,fetched_at,jd_source
               ) VALUES(?,?,?,?,?,?)""",
            (replacement["identity_key"], replacement["source"], replacement["source_job_id"],
             replacement["canonical_url"], old_primary["jd_fetched_at"], old_primary["jd_source"]),
        )


def retire_terminal_source_job(job_id: int, *, source_status: str, reason: str) -> int | None:
    """Tombstone and physically remove one source posting confirmed closed/gone."""
    log = collection_logger()
    removed_at = datetime.now(UTC).isoformat(timespec="seconds")
    replacement_primary: int | None = None
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if row is None:
            return None
        job = dict(row)
        if job.get("primary_job_id") is None:
            members = [dict(item) for item in conn.execute(
                """SELECT j.*,COALESCE(s.archived,0) AS archived,s.last_seen_at AS observation_last_seen_at
                     FROM jobs j LEFT JOIN job_observation_state s ON s.job_id=j.id
                    WHERE j.primary_job_id=?
                    ORDER BY datetime(s.last_seen_at) DESC, datetime(j.posted_at) DESC, j.id DESC""",
                (job_id,),
            )]
            active_members = [
                item for item in members
                if not int(item.get("archived") or 0)
                and str(item.get("source_status") or "").strip().casefold()
                    not in TERMINAL_SOURCE_STATUSES
            ]
            if active_members:
                replacement = active_members[0]
                replacement_primary = int(replacement["id"])
                _transfer_primary_jd(conn, job, replacement)
                # Preserve current SEEK coverage when a dead canonical posting is
                # replaced by an active repost/source alias.
                conn.execute(
                    """
                    INSERT OR IGNORE INTO seek_partition_jobs(partition_id,job_id,first_seen_at)
                    SELECT partition_id,?,first_seen_at
                      FROM seek_partition_jobs
                     WHERE job_id=?
                    """,
                    (replacement_primary, job_id),
                )
                conn.execute("UPDATE jobs SET primary_job_id=NULL WHERE id=?", (replacement_primary,))
                conn.execute(
                    "UPDATE jobs SET primary_job_id=? WHERE primary_job_id=? AND id<>?",
                    (replacement_primary, job_id, replacement_primary),
                )
                conn.execute("DELETE FROM same_vacancy_links WHERE job_id=?", (replacement_primary,))
                conn.execute(
                    "UPDATE same_vacancy_links SET primary_job_id=? WHERE primary_job_id=? AND job_id<>?",
                    (replacement_primary, job_id, replacement_primary),
                )
        _write_tombstone(conn, job, removed_at=removed_at)
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    log.info(
        "JD_TERMINAL_REMOVAL job_id=%s source=%s source_job_id=%s status=%s replacement_primary=%s title=%r url=%s reason=%s",
        job_id, job.get("source"), job.get("source_job_id"), source_status,
        replacement_primary, job.get("title"), job.get("canonical_url"), reason,
    )
    return replacement_primary


def retire_known_terminal_family(job_id: int) -> int | None:
    """Remove terminal rows in one canonical family and return its surviving primary."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id,primary_job_id FROM jobs WHERE id=?", (int(job_id),)
        ).fetchone()
        if row is None:
            return None
        primary_id = int(row["primary_job_id"] or row["id"])
        rows = [
            dict(item)
            for item in conn.execute(
                "SELECT id,source_status FROM jobs WHERE id=? OR primary_job_id=? ORDER BY id",
                (primary_id, primary_id),
            )
        ]

    terminal = [
        item for item in rows
        if str(item.get("source_status") or "").strip().casefold()
        in TERMINAL_SOURCE_STATUSES
    ]
    if not terminal:
        return primary_id

    collection_logger().info(
        "JD_TERMINAL_FAMILY_CLEANUP primary_job_id=%s terminal_job_ids=%s",
        primary_id,
        [int(item["id"]) for item in terminal],
    )

    # Remove a dead primary first so retire_terminal_source_job can promote the
    # freshest active linked posting and keep the family canonical.
    terminal.sort(key=lambda item: int(item["id"]) != primary_id)
    survivor: int | None = primary_id
    for item in terminal:
        terminal_id = int(item["id"])
        with connect() as conn:
            live = conn.execute(
                "SELECT id,primary_job_id,source_status FROM jobs WHERE id=?",
                (terminal_id,),
            ).fetchone()
        if live is None:
            continue
        status = str(live["source_status"] or "").strip().casefold()
        replacement = retire_terminal_source_job(
            terminal_id, source_status=status, reason="pre-existing terminal source evidence"
        )
        if terminal_id == primary_id:
            survivor = replacement

    if survivor is None:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT id,primary_job_id FROM jobs WHERE id=?", (survivor,)
        ).fetchone()
    if row is None:
        return None
    return int(row["primary_job_id"] or row["id"])


def cleanup_observed_terminal_families(*, source: str, observed_since: str) -> int:
    """Clean terminal rows from vacancy families observed in the current source cycle."""
    resolved_source = str(source or "").strip().casefold()
    if not resolved_source or not str(observed_since or "").strip():
        return 0
    with connect() as conn:
        primary_ids = [
            int(row["primary_id"])
            for row in conn.execute(
                """
                SELECT DISTINCT COALESCE(j.primary_job_id,j.id) AS primary_id
                  FROM jobs j
                  JOIN job_observation_state s ON s.job_id=j.id
                 WHERE j.source=? AND datetime(s.last_seen_at) >= datetime(?)
                 ORDER BY primary_id
                """,
                (resolved_source, observed_since),
            )
        ]
    cleaned = 0
    for primary_id in primary_ids:
        with connect() as conn:
            terminal_before = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM jobs
                     WHERE (id=? OR primary_job_id=?)
                       AND lower(trim(COALESCE(source_status,''))) IN ({','.join('?' for _ in TERMINAL_SOURCE_STATUSES)})
                    """,
                    (primary_id, primary_id, *sorted(TERMINAL_SOURCE_STATUSES)),
                ).fetchone()[0]
            )
        if not terminal_before:
            continue
        retire_known_terminal_family(primary_id)
        cleaned += 1
    collection_logger().info(
        "JD_TERMINAL_CYCLE_CLEANUP source=%s observed_since=%s families=%s cleaned=%s",
        resolved_source, observed_since, len(primary_ids), cleaned,
    )
    return cleaned


def purge_terminal_jobs(*, posted_since: str | None = None) -> int:
    """Remove already-known terminal rows, optionally limited to a posted-at window."""
    params: list[object] = sorted(TERMINAL_SOURCE_STATUSES)
    where = f"source_status IN ({','.join('?' for _ in params)})"
    if posted_since:
        where += " AND datetime(posted_at) >= datetime(?)"
        params.append(posted_since)
    with connect() as conn:
        ids = [int(row[0]) for row in conn.execute(f"SELECT id FROM jobs WHERE {where} ORDER BY id", params)]
    removed = 0
    for job_id in ids:
        with connect() as conn:
            row = conn.execute("SELECT source_status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            continue
        retire_terminal_source_job(job_id, source_status=str(row[0]), reason="pre-existing terminal source status")
        removed += 1
    collection_logger().info("JD_TERMINAL_PURGE cutoff=%s removed=%s", posted_since, removed)
    return removed
