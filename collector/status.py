from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from collector.db import connect, init_db

STATUS_FIELDS = {"shown_to_rob", "reviewed", "applied", "rejected", "dismissed"}


@dataclass(frozen=True)
class StatusWriteResult:
    changed: bool
    event_id: int


def set_status(
    job_id: int,
    field: str,
    value: bool,
    *,
    actor: str | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> StatusWriteResult:
    """Set shared Rob-status and append one immutable event; retries can be idempotent."""
    if field not in STATUS_FIELDS:
        raise ValueError(f"unsupported status field: {field}")
    init_db()
    occurred_at = datetime.now(UTC).isoformat(timespec="seconds")
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not exists:
            raise KeyError(job_id)
        if idempotency_key:
            prior = conn.execute(
                "SELECT id FROM job_status_events WHERE actor IS ? AND idempotency_key=?",
                (actor, idempotency_key),
            ).fetchone()
            if prior:
                return StatusWriteResult(changed=False, event_id=int(prior[0]))
        conn.execute(f"UPDATE jobs SET {field}=? WHERE id=?", (int(value), job_id))
        cur = conn.execute(
            """
            INSERT INTO job_status_events(
                job_id, event_type, event_value, occurred_at, actor, note, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, field, int(value), occurred_at, actor, note, idempotency_key),
        )
        return StatusWriteResult(changed=True, event_id=int(cur.lastrowid))
