from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from collector.db import connect, init_db


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def start_market_run(
    *,
    trigger: str,
    mode: str,
    states: list[str],
    backup_path: str | None,
    source_scope: str = "seek_whole_state",
    run_kind: str = "normal",
) -> int:
    init_db()
    recover_stale_market_runs()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO market_collection_runs(
                trigger,source_scope,run_kind,mode,status,pid,started_at,backup_path,states_json,message
            ) VALUES(?, ?, ?, ?, 'RUNNING', ?, ?, ?, ?, 'Collection started')
            """,
            (
                trigger,
                source_scope,
                run_kind,
                mode,
                os.getpid(),
                utc_now(),
                backup_path,
                json.dumps(states),
            ),
        )
        return int(cur.lastrowid)


def set_market_run_backup(run_id: int, backup_path: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE market_collection_runs SET backup_path=?, message=? WHERE id=?",
            (backup_path, "Pre-run backup verified; collection running.", run_id),
        )


def finish_market_run(
    run_id: int,
    *,
    status: str,
    message: str,
    error: str | None = None,
    stats: dict[str, Any] | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE market_collection_runs
               SET status=?, finished_at=?, message=?, error=?, stats_json=?
             WHERE id=?
            """,
            (
                status,
                utc_now(),
                message,
                error,
                json.dumps(stats, ensure_ascii=False, sort_keys=True) if stats else None,
                run_id,
            ),
        )


def recover_stale_market_runs(*, reason: str = "collection process ended before finalisation") -> list[int]:
    """Close RUNNING market runs whose recorded runner process no longer exists.

    A host restart releases the filesystem lock before the runner can persist its
    normal terminal status. Without this recovery, readiness reports a false
    RUNNING state indefinitely and another source may be scheduled against it.
    """
    init_db()
    recovered: list[int] = []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, pid FROM market_collection_runs
             WHERE status='RUNNING' AND finished_at IS NULL
            ORDER BY id
            """
        ).fetchall()
        for row in rows:
            pid = int(row["pid"] or 0)
            alive = False
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    alive = True
                except (ProcessLookupError, PermissionError, OSError):
                    alive = False
            if alive:
                continue
            run_id = int(row["id"])
            conn.execute(
                """
                UPDATE market_collection_runs
                   SET status='INTERRUPTED', finished_at=?, message=?, error=?
                 WHERE id=? AND status='RUNNING' AND finished_at IS NULL
                """,
                (utc_now(), reason, reason, run_id),
            )
            recovered.append(run_id)
    return recovered


def _decode_market_run(row) -> dict | None:
    if not row:
        return None
    item = dict(row)
    if item.get("states_json"):
        try:
            item["states"] = json.loads(item.pop("states_json"))
        except json.JSONDecodeError:
            item["states"] = []
    if item.get("stats_json"):
        try:
            item["stats"] = json.loads(item.pop("stats_json"))
        except json.JSONDecodeError:
            item["stats"] = None
    else:
        item.pop("stats_json", None)
        item["stats"] = None
    return item


def latest_market_run() -> dict | None:
    recover_stale_market_runs()
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM market_collection_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _decode_market_run(row)


def latest_seek_market_run() -> dict | None:
    recover_stale_market_runs()
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM market_collection_runs
             WHERE run_kind='normal'
               AND source_scope LIKE 'seek%'
             ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    return _decode_market_run(row)


def bootstrap_market_run() -> dict | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM market_collection_runs
             WHERE run_kind='bootstrap'
             ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    return _decode_market_run(row)


def latest_complete_fresh_seek_started_at(codes: list[str]) -> str | None:
    """Return the start of the latest trustworthy fresh SEEK cycle.

    Partial/stopped runs and cycles that only completed after a resume are not
    watermarks. Falling back to a full scan is cheaper than missing vacancies.
    """
    if not codes:
        return None
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM market_collection_runs
             WHERE run_kind='normal'
               AND mode='fresh'
               AND source_scope LIKE 'seek%'
               AND stats_json IS NOT NULL
             ORDER BY id DESC
            """
        ).fetchall()
    for row in rows:
        run = _decode_market_run(row)
        if str(run.get("status") or "") != "COMPLETE":
            continue
        stats = dict(run.get("stats") or {})
        coverage = dict(stats.get("coverage") or {})
        if all(
            code in coverage
            and str((coverage.get(code) or {}).get("status") or "").startswith("COMPLETE")
            for code in codes
        ):
            return str(run.get("started_at") or "") or None
    return None


def scheduler_state() -> dict:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM scheduler_state WHERE id=1").fetchone()
    return dict(row) if row else {"id": 1}


def update_scheduler_state(**fields: Any) -> dict:
    allowed = {
        "heartbeat_at",
        "last_attempt_local_date",
        "last_attempt_seek_slot",
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
