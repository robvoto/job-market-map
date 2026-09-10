from __future__ import annotations

import sqlite3
from pathlib import Path

from collector.identity import job_identity_key

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "market.db"
SCHEMA_PATH = ROOT / "collector" / "schema.sql"

LEGACY_ACTIVITY_MAP = {
    "shown_to_rob": "shown",
    "reviewed": "reviewed",
    "applied": "applied",
    "rejected": "rejected",
    "dismissed": "dismissed",
}


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


def _project_activity(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    user_key: str,
    identity_key: str,
    activity_type: str,
    active: int,
    occurred_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO user_job_activity_current(
            user_key, job_identity_key, activity_type, active, updated_at, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_key, job_identity_key, activity_type) DO UPDATE SET
            active=excluded.active,
            updated_at=excluded.updated_at,
            last_event_id=excluded.last_event_id
        """,
        (user_key, identity_key, activity_type, active, occurred_at, event_id),
    )


def _migrate_legacy_activity(conn: sqlite3.Connection) -> None:
    """Move early global Rob-status state into the per-user activity ledger, then drop it."""
    job_columns = _columns(conn, "jobs")
    legacy_columns = [name for name in LEGACY_ACTIVITY_MAP if name in job_columns]

    if _table_exists(conn, "job_status_events"):
        rows = conn.execute(
            """
            SELECT e.*, j.identity_key
              FROM job_status_events e
              JOIN jobs j ON j.id=e.job_id
             ORDER BY e.occurred_at, e.id
            """
        ).fetchall()
        for row in rows:
            activity_type = LEGACY_ACTIVITY_MAP.get(
                row["event_type"], row["event_type"]
            )
            if activity_type not in set(LEGACY_ACTIVITY_MAP.values()) | {"seen"}:
                continue
            actor = row["actor"] or "legacy"
            migration_key = f"legacy-status-event:{row['id']}"
            conn.execute(
                """
                INSERT OR IGNORE INTO user_job_activity_events(
                    user_key, job_identity_key, activity_type, activity_value,
                    occurred_at, actor, note, idempotency_key
                ) VALUES ('rob', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["identity_key"],
                    activity_type,
                    int(row["event_value"]),
                    row["occurred_at"],
                    actor,
                    row["note"],
                    migration_key,
                ),
            )
            event = conn.execute(
                "SELECT id FROM user_job_activity_events WHERE user_key='rob' AND actor=? AND idempotency_key=?",
                (actor, migration_key),
            ).fetchone()
            if event:
                _project_activity(
                    conn,
                    event_id=int(event[0]),
                    user_key="rob",
                    identity_key=row["identity_key"],
                    activity_type=activity_type,
                    active=int(row["event_value"]),
                    occurred_at=row["occurred_at"],
                )
        conn.execute("DROP TABLE job_status_events")

    for legacy_field in legacy_columns:
        activity_type = LEGACY_ACTIVITY_MAP[legacy_field]
        rows = conn.execute(
            f"SELECT id, identity_key, last_seen_at FROM jobs WHERE {legacy_field}=1"
        ).fetchall()
        for row in rows:
            current = conn.execute(
                """
                SELECT active FROM user_job_activity_current
                 WHERE user_key='rob' AND job_identity_key=? AND activity_type=?
                """,
                (row["identity_key"], activity_type),
            ).fetchone()
            if current and int(current[0]) == 1:
                continue
            actor = "schema-migration"
            migration_key = f"legacy-flag:{row['identity_key']}:{activity_type}"
            conn.execute(
                """
                INSERT OR IGNORE INTO user_job_activity_events(
                    user_key, job_identity_key, activity_type, activity_value,
                    occurred_at, actor, note, idempotency_key
                ) VALUES ('rob', ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    row["identity_key"],
                    activity_type,
                    row["last_seen_at"],
                    actor,
                    f"Migrated legacy jobs.{legacy_field}=1",
                    migration_key,
                ),
            )
            event = conn.execute(
                "SELECT id FROM user_job_activity_events WHERE user_key='rob' AND actor=? AND idempotency_key=?",
                (actor, migration_key),
            ).fetchone()
            if event:
                _project_activity(
                    conn,
                    event_id=int(event[0]),
                    user_key="rob",
                    identity_key=row["identity_key"],
                    activity_type=activity_type,
                    active=1,
                    occurred_at=row["last_seen_at"],
                )

    conn.execute("DROP INDEX IF EXISTS idx_jobs_flags")
    for column in legacy_columns:
        conn.execute(f"ALTER TABLE jobs DROP COLUMN {column}")

    tombstone_columns = _columns(conn, "job_tombstones")
    for column in LEGACY_ACTIVITY_MAP:
        if column in tombstone_columns:
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
        _migrate_legacy_activity(conn)
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
