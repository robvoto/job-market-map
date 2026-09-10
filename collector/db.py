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
