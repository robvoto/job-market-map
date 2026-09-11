from __future__ import annotations

from datetime import datetime

from collector.db import connect, init_db

TERMINAL_CYCLE_STATUSES = {"COMPLETE", "INCOMPLETE_CAP"}


def now_local_cycle_key() -> str:
    return datetime.now().astimezone().date().isoformat()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_or_start_cycle(source: str, *, desired_cycle_key: str | None = None) -> dict:
    init_db()
    source = str(source or "").strip().casefold()
    if not source:
        raise ValueError("source is required")
    desired = desired_cycle_key or now_local_cycle_key()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM source_campaign_state WHERE source=?", (source,)
        ).fetchone()
        if row is None:
            timestamp = _now()
            conn.execute(
                """
                INSERT INTO source_campaign_state(source,cycle_key,status,started_at,updated_at,completed_at)
                VALUES(?,?,'PARTIAL',?,?,NULL)
                """,
                (source, desired, timestamp, timestamp),
            )
        elif str(row["status"]) == "RUNNING":
            conn.execute(
                "UPDATE source_campaign_state SET status='PARTIAL',updated_at=? WHERE source=?",
                (_now(), source),
            )
        elif str(row["status"]) in TERMINAL_CYCLE_STATUSES and str(row["cycle_key"]) != desired:
            timestamp = _now()
            conn.execute(
                """
                UPDATE source_campaign_state
                   SET cycle_key=?,status='PARTIAL',started_at=?,updated_at=?,completed_at=NULL
                 WHERE source=?
                """,
                (desired, timestamp, timestamp, source),
            )
        current = conn.execute(
            "SELECT * FROM source_campaign_state WHERE source=?", (source,)
        ).fetchone()
    return dict(current)


def set_cycle_status(source: str, *, cycle_key: str, status: str) -> dict:
    init_db()
    source = str(source or "").strip().casefold()
    status = str(status or "").strip().upper()
    timestamp = _now()
    completed_at = timestamp if status in TERMINAL_CYCLE_STATUSES else None
    with connect() as conn:
        conn.execute(
            """
            UPDATE source_campaign_state
               SET status=?,updated_at=?,completed_at=?
             WHERE source=? AND cycle_key=?
            """,
            (status, timestamp, completed_at, source, cycle_key),
        )
        row = conn.execute(
            "SELECT * FROM source_campaign_state WHERE source=?", (source,)
        ).fetchone()
    if row is None:
        raise KeyError(source)
    return dict(row)


def get_cycle(source: str) -> dict | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM source_campaign_state WHERE source=?",
            (str(source or "").strip().casefold(),),
        ).fetchone()
    return dict(row) if row else None
