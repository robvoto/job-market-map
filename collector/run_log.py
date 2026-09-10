from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from collector.db import connect, init_db


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def start_run(source: str, query_text: str, location: str | None) -> int:
    init_db()
    with connect() as conn:
        q = conn.execute(
            "SELECT id FROM queries WHERE source=? AND query_text=? AND location=?",
            (source, query_text, location or ""),
        ).fetchone()
        cur = conn.execute(
            """
            INSERT INTO collection_runs(source, query_id, query_text, location, started_at, status)
            VALUES (?, ?, ?, ?, ?, 'RUNNING')
            """,
            (source, int(q[0]) if q else None, query_text, location or "", now()),
        )
        return int(cur.lastrowid)


def finish_run(
    run_id: int,
    *,
    status: str,
    pages_requested: int,
    pages_parsed: int,
    cards_observed: int,
    unique_new_jobs: int,
    duplicate_observations: int,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE collection_runs
               SET finished_at=?, status=?, pages_requested=?, pages_parsed=?, cards_observed=?,
                   unique_new_jobs=?, duplicate_observations=?, error=?, metadata_json=?
             WHERE id=?
            """,
            (
                now(),
                status,
                pages_requested,
                pages_parsed,
                cards_observed,
                unique_new_jobs,
                duplicate_observations,
                error,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                run_id,
            ),
        )
        row = conn.execute(
            "SELECT query_id FROM collection_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row and row[0]:
            conn.execute(
                "UPDATE queries SET last_run_at=?, last_error=? WHERE id=?",
                (now(), error, int(row[0])),
            )
