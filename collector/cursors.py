from __future__ import annotations

from datetime import UTC, datetime

from collector.db import connect, init_db


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_cursor(source: str, query_text: str, location: str) -> dict:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM collection_cursors WHERE source=? AND query_text=? AND location=?",
            (source, query_text, location),
        ).fetchone()
    return (
        dict(row)
        if row
        else {
            "source": source,
            "query_text": query_text,
            "location": location,
            "cursor_value": 0,
            "status": "PENDING",
            "last_job_id": None,
            "total_results_hint": None,
        }
    )


def save_cursor(
    source: str,
    query_text: str,
    location: str,
    cursor_value: int,
    *,
    status: str,
    last_job_id: str | None = None,
    total_results_hint: int | None = None,
) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO collection_cursors(source, query_text, location, cursor_value, status, updated_at,
                                           last_job_id, total_results_hint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, query_text, location) DO UPDATE SET
                cursor_value=excluded.cursor_value,
                status=excluded.status,
                updated_at=excluded.updated_at,
                last_job_id=excluded.last_job_id,
                total_results_hint=COALESCE(excluded.total_results_hint, collection_cursors.total_results_hint)
            """,
            (
                source,
                query_text,
                location,
                cursor_value,
                status,
                now(),
                last_job_id,
                total_results_hint,
            ),
        )
