from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from collector.db import connect, init_db


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def start_market_run(*, trigger: str, mode: str, states: list[str], backup_path: str | None) -> int:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO market_collection_runs(
                trigger,source_scope,mode,status,pid,started_at,backup_path,states_json,message
            ) VALUES(?, 'seek_whole_state', ?, 'RUNNING', ?, ?, ?, ?, 'Collection started')
            """,
            (trigger, mode, os.getpid(), utc_now(), backup_path, json.dumps(states)),
        )
        return int(cur.lastrowid)


def set_market_run_backup(run_id: int, backup_path: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE market_collection_runs SET backup_path=?, message=? WHERE id=?",
            (backup_path, "Pre-run backup verified; collection running.", run_id),
        )


def finish_market_run(run_id: int, *, status: str, message: str, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE market_collection_runs
               SET status=?, finished_at=?, message=?, error=?
             WHERE id=?
            """,
            (status, utc_now(), message, error, run_id),
        )


def latest_market_run() -> dict | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM market_collection_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    if item.get("states_json"):
        try:
            item["states"] = json.loads(item.pop("states_json"))
        except json.JSONDecodeError:
            item["states"] = []
    return item


def scheduler_state() -> dict:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM scheduler_state WHERE id=1").fetchone()
    return dict(row) if row else {"id": 1}


def update_scheduler_state(**fields: Any) -> dict:
    allowed = {
        "heartbeat_at",
        "last_attempt_local_date",
        "last_started_at",
        "last_finished_at",
        "last_status",
        "last_message",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unsupported scheduler fields: {sorted(unknown)}")
    init_db()
    if fields:
        assignments = ", ".join(f"{key}=?" for key in fields)
        with connect() as conn:
            conn.execute(
                f"UPDATE scheduler_state SET {assignments} WHERE id=1",
                (*fields.values(),),
            )
    return scheduler_state()
