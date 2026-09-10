import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "market.db"
SCHEMA_PATH = ROOT / "collector" / "schema.sql"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


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
        _ensure_column(
            conn, "job_status_events", "idempotency_key", "idempotency_key TEXT"
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
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_status_event_idempotency "
            "ON job_status_events(actor, idempotency_key) WHERE idempotency_key IS NOT NULL"
        )


if __name__ == "__main__":
    init_db()
    print(DB_PATH)
