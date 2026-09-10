from __future__ import annotations

from datetime import UTC, datetime

from collector.db import connect, init_db


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean_key(value: str) -> str:
    key = "-".join(value.strip().casefold().split())
    if not key or len(key) > 100:
        raise ValueError("consumer_key must be 1-100 characters")
    return key


def get_checkpoint(consumer_key: str) -> dict:
    key = _clean_key(consumer_key)
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM consumer_checkpoints WHERE consumer_key=?", (key,)
        ).fetchone()
    return (
        dict(row)
        if row
        else {"consumer_key": key, "last_job_id": 0, "updated_at": None, "note": None}
    )


def advance_checkpoint(
    consumer_key: str, last_job_id: int, *, note: str | None = None
) -> dict:
    key = _clean_key(consumer_key)
    if last_job_id < 0:
        raise ValueError("last_job_id must be >= 0")
    init_db()
    with connect() as conn:
        current = conn.execute(
            "SELECT last_job_id FROM consumer_checkpoints WHERE consumer_key=?", (key,)
        ).fetchone()
        if current and last_job_id < int(current[0]):
            raise ValueError("checkpoint cannot move backwards")
        conn.execute(
            """
            INSERT INTO consumer_checkpoints(consumer_key,last_job_id,updated_at,note)
            VALUES(?,?,?,?)
            ON CONFLICT(consumer_key) DO UPDATE SET
                last_job_id=excluded.last_job_id,
                updated_at=excluded.updated_at,
                note=excluded.note
            """,
            (key, last_job_id, _now(), note),
        )
        row = conn.execute(
            "SELECT * FROM consumer_checkpoints WHERE consumer_key=?", (key,)
        ).fetchone()
    return dict(row)
