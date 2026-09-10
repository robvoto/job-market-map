from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from collector.db import connect, init_db


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def list_queries(*, active_only: bool = False) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM queries"
    if active_only:
        sql += " WHERE active=1"
    sql += " ORDER BY source, query_text, location"
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql)]


def set_query_active(query_id: int, active: bool) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        result = conn.execute(
            "UPDATE queries SET active=? WHERE id=?", (int(active), query_id)
        )
        if result.rowcount != 1:
            raise KeyError(query_id)
        row = conn.execute("SELECT * FROM queries WHERE id=?", (query_id,)).fetchone()
    return dict(row)


def add_query(
    *,
    source: str,
    query_text: str,
    location: str = "Sydney NSW",
    registry_key: str | None = None,
    origins: list[str] | None = None,
    active: bool = True,
) -> dict[str, Any]:
    source = " ".join(source.split()).casefold()
    query_text = " ".join(query_text.split())
    location = " ".join(location.split())
    if not source or not query_text:
        raise ValueError("source and query_text are required")
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO queries(source, query_text, location, active, created_at, registry_key, origins_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, query_text, location) DO UPDATE SET
                active=excluded.active,
                registry_key=COALESCE(excluded.registry_key, queries.registry_key),
                origins_json=COALESCE(excluded.origins_json, queries.origins_json)
            """,
            (
                source,
                query_text,
                location,
                int(active),
                _now(),
                registry_key,
                json.dumps(origins or ["admin"], ensure_ascii=False),
            ),
        )
        row = conn.execute(
            "SELECT * FROM queries WHERE source=? AND query_text=? AND location=?",
            (source, query_text, location),
        ).fetchone()
    return dict(row)
