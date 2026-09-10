from __future__ import annotations

import sqlite3
from pathlib import Path

from collector.identity import job_identity_key

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "market.db"
SCHEMA_PATH = ROOT / "collector" / "schema.sql"

LEGACY_PERSONAL_COLUMNS = (
    "shown_to_rob",
    "reviewed",
    "applied",
    "rejected",
    "dismissed",
)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_job_by_source_id(source: str, source_job_id: str) -> dict[str, object] | None:
    """Return an active canonical job by trustworthy source ID."""
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE source=? AND source_job_id=?",
            (str(source).strip().casefold(), str(source_job_id).strip()),
        ).fetchone()
    return dict(row) if row else None


def job_jd_fetch_completed(job_id: int) -> bool:
    """Return whether this canonical identity has ever had a successful JD fetch."""
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT 1
              FROM jobs j
              JOIN jd_fetch_registry r ON r.identity_key=j.identity_key
             WHERE j.id=?
            """,
            (job_id,),
        ).fetchone()
    return row is not None


def _record_successful_jd_fetch(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    conn.execute(
        """
        INSERT INTO jd_fetch_registry(
            identity_key,source,source_job_id,canonical_url,fetched_at,jd_source
        ) VALUES(?,?,?,?,?,?)
        ON CONFLICT(identity_key) DO NOTHING
        """,
        (
            row["identity_key"],
            row["source"],
            row["source_job_id"],
            row["canonical_url"],
            row["jd_fetched_at"],
            row["jd_source"],
        ),
    )


def _backfill_jd_fetch_registry(conn: sqlite3.Connection) -> None:
    """Preserve permanent fetch memory for JDs stored before the registry existed."""
    # This migration is only needed once. Avoid rescanning all jobs on every
    # store_job_jd_once() call during a large initial enrichment run.
    if conn.execute("SELECT 1 FROM jd_fetch_registry LIMIT 1").fetchone():
        return
    rows = conn.execute(
        """
        SELECT identity_key,source,source_job_id,canonical_url,jd_fetched_at,jd_source
          FROM jobs
         WHERE identity_key IS NOT NULL AND trim(identity_key)<>''
           AND full_description IS NOT NULL AND trim(full_description)<>''
           AND jd_fetched_at IS NOT NULL AND trim(jd_fetched_at)<>''
           AND jd_source IS NOT NULL AND trim(jd_source)<>''
        """
    ).fetchall()
    for row in rows:
        _record_successful_jd_fetch(conn, row)


def get_job_jd(job_id: int) -> dict[str, object] | None:
    """Return the one canonical neutral JD for a job, if JMM has it."""
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT id, full_description, jd_fetched_at, jd_source FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"job {job_id} not found")
    if not str(row["full_description"] or "").strip():
        return None
    return dict(row)


def store_job_jd_once(
    job_id: int,
    *,
    full_description: str,
    jd_fetched_at: str,
    jd_source: str,
) -> dict[str, object]:
    """Store a neutral JD only when the canonical job does not already have one."""
    if not str(full_description or "").strip():
        raise ValueError("full_description is required")
    fetched_at = str(jd_fetched_at or "").strip()
    if not fetched_at:
        raise ValueError("jd_fetched_at is required")
    source = str(jd_source or "").strip()
    if not source:
        raise ValueError("jd_source is required")

    init_db()
    with connect() as conn:
        existing = conn.execute(
            "SELECT id, identity_key, source, source_job_id, canonical_url, full_description, jd_fetched_at, jd_source FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if existing is None:
            raise KeyError(f"job {job_id} not found")
        if str(existing["full_description"] or "").strip():
            if existing["jd_fetched_at"] and existing["jd_source"]:
                _record_successful_jd_fetch(conn, existing)
            return {
                "id": existing["id"],
                "full_description": existing["full_description"],
                "jd_fetched_at": existing["jd_fetched_at"],
                "jd_source": existing["jd_source"],
            }

        conn.execute(
            """
            UPDATE jobs
               SET full_description=?, jd_fetched_at=?, jd_source=?
             WHERE id=?
               AND (full_description IS NULL OR trim(full_description)='')
            """,
            (full_description, fetched_at, source, job_id),
        )
        stored = conn.execute(
            "SELECT id, identity_key, source, source_job_id, canonical_url, full_description, jd_fetched_at, jd_source FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        _record_successful_jd_fetch(conn, stored)
    return {
        "id": stored["id"],
        "full_description": stored["full_description"],
        "jd_fetched_at": stored["jd_fetched_at"],
        "jd_source": stored["jd_source"],
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _backfill_identity(conn: sqlite3.Connection, table: str) -> None:
    if not _table_exists(conn, table) or "identity_key" not in _columns(conn, table):
        return
    rows = conn.execute(
        f"SELECT id, source, source_job_id, canonical_url FROM {table} "
        "WHERE identity_key IS NULL OR identity_key=''"
    ).fetchall()
    for row in rows:
        identity = job_identity_key(
            row["source"], row["source_job_id"], row["canonical_url"]
        )
        conn.execute(
            f"UPDATE {table} SET identity_key=? WHERE id=?", (identity, row["id"])
        )


def _remove_obsolete_personal_activity_scaffolding(conn: sqlite3.Connection) -> None:
    """Remove the abandoned local activity model only when it contains no personal data.

    Job Market Map is neutral/global. JH-305 owns personal activity. If an older
    database contains non-empty activity state, fail closed rather than silently
    deleting or re-homing personal history.
    """
    for table in (
        "user_job_activity_events",
        "user_job_activity_current",
        "job_status_events",
    ):
        if _table_exists(conn, table):
            count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if count:
                raise RuntimeError(
                    f"obsolete personal activity table {table} contains {count} rows; "
                    "migrate that data to Job Hunter JH-305 before removing the table"
                )

    job_columns = _columns(conn, "jobs")
    for column in LEGACY_PERSONAL_COLUMNS:
        if column in job_columns:
            count = int(
                conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {column}=1").fetchone()[
                    0
                ]
            )
            if count:
                raise RuntimeError(
                    f"legacy jobs.{column} contains {count} personal-state rows; "
                    "migrate that data to Job Hunter JH-305 before removing the column"
                )

    conn.execute("DROP INDEX IF EXISTS idx_jobs_flags")
    conn.execute("DROP INDEX IF EXISTS idx_user_activity_idempotency")
    conn.execute("DROP INDEX IF EXISTS idx_user_activity_identity")
    conn.execute("DROP INDEX IF EXISTS idx_user_activity_current_active")
    for table in (
        "job_status_events",
        "user_job_activity_current",
        "user_job_activity_events",
    ):
        if _table_exists(conn, table):
            conn.execute(f"DROP TABLE {table}")

    for column in LEGACY_PERSONAL_COLUMNS:
        if column in _columns(conn, "jobs"):
            conn.execute(f"ALTER TABLE jobs DROP COLUMN {column}")
        if column in _columns(conn, "job_tombstones"):
            conn.execute(f"ALTER TABLE job_tombstones DROP COLUMN {column}")


def _ensure_identity_triggers(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS trg_jobs_identity_after_insert
        AFTER INSERT ON jobs
        WHEN NEW.identity_key IS NULL OR NEW.identity_key=''
        BEGIN
            UPDATE jobs
               SET identity_key = lower(trim(NEW.source)) ||
                   CASE
                     WHEN NEW.source_job_id IS NOT NULL AND trim(NEW.source_job_id)<>''
                     THEN ':id:' || trim(NEW.source_job_id)
                     ELSE ':url:' || NEW.canonical_url
                   END
             WHERE id=NEW.id;
        END;

        CREATE TRIGGER IF NOT EXISTS trg_tombstones_identity_after_insert
        AFTER INSERT ON job_tombstones
        WHEN NEW.identity_key IS NULL OR NEW.identity_key=''
        BEGIN
            UPDATE job_tombstones
               SET identity_key = lower(trim(NEW.source)) ||
                   CASE
                     WHEN NEW.source_job_id IS NOT NULL AND trim(NEW.source_job_id)<>''
                     THEN ':id:' || trim(NEW.source_job_id)
                     ELSE ':url:' || NEW.canonical_url
                   END
             WHERE id=NEW.id;
        END;
        """
    )


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Lightweight forward migrations for databases created by early builds.
        _ensure_column(conn, "jobs", "archived", "archived INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "jobs", "compacted_at", "compacted_at TEXT")
        _ensure_column(conn, "jobs", "classification_text", "classification_text TEXT")
        _ensure_column(
            conn, "jobs", "subclassification_text", "subclassification_text TEXT"
        )
        _ensure_column(conn, "jobs", "card_tags_json", "card_tags_json TEXT")
        _ensure_column(conn, "jobs", "full_description", "full_description TEXT")
        _ensure_column(conn, "jobs", "jd_fetched_at", "jd_fetched_at TEXT")
        _ensure_column(conn, "jobs", "jd_source", "jd_source TEXT")
        _ensure_column(conn, "jobs", "geography_code", "geography_code TEXT")
        _ensure_column(conn, "jobs", "core_fingerprint", "core_fingerprint TEXT")
        _ensure_column(
            conn, "jobs", "exact_card_fingerprint", "exact_card_fingerprint TEXT"
        )
        _ensure_column(conn, "jobs", "identity_key", "identity_key TEXT")
        _ensure_column(conn, "job_tombstones", "identity_key", "identity_key TEXT")
        _ensure_column(conn, "queries", "geography_code", "geography_code TEXT")
        _ensure_column(conn, "queries", "registry_key", "registry_key TEXT")
        _ensure_column(conn, "queries", "origins_json", "origins_json TEXT")
        _ensure_column(conn, "queries", "last_run_at", "last_run_at TEXT")
        _ensure_column(
            conn,
            "queries",
            "cards_observed",
            "cards_observed INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn,
            "queries",
            "unique_new_jobs",
            "unique_new_jobs INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn,
            "queries",
            "duplicate_hits",
            "duplicate_hits INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(conn, "queries", "last_error", "last_error TEXT")

        _backfill_identity(conn, "jobs")
        _backfill_identity(conn, "job_tombstones")
        _backfill_jd_fetch_registry(conn)
        _remove_obsolete_personal_activity_scaffolding(conn)
        _ensure_identity_triggers(conn)

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_identity ON jobs(identity_key)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_job_tombstones_identity ON job_tombstones(identity_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_archived_last_seen ON jobs(archived, last_seen_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_core_fingerprint ON jobs(core_fingerprint)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_exact_card_fingerprint ON jobs(exact_card_fingerprint)"
        )


if __name__ == "__main__":
    init_db()
    print(DB_PATH)
