from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from collector.db import connect, init_db

ACTIVITY_TYPES = {"seen", "shown", "reviewed", "applied", "rejected", "dismissed"}


@dataclass(frozen=True)
class ActivityWriteResult:
    changed: bool
    event_id: int
    user_key: str
    job_identity_key: str
    activity_type: str
    value: bool


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _user_key(value: str) -> str:
    key = "-".join((value or "").strip().casefold().split())
    if not key or len(key) > 100:
        raise ValueError("user_key must be 1-100 characters")
    return key


def _activity_type(value: str) -> str:
    activity = "_".join((value or "").strip().casefold().split())
    if activity not in ACTIVITY_TYPES:
        raise ValueError(
            f"activity_type must be one of: {', '.join(sorted(ACTIVITY_TYPES))}"
        )
    return activity


def _identity_for_job(conn, job_id: int) -> str:
    row = conn.execute("SELECT identity_key FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise KeyError(job_id)
    return str(row[0])


def record_activity(
    job_id: int,
    *,
    user_key: str,
    activity_type: str,
    value: bool = True,
    actor: str | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> ActivityWriteResult:
    """Append per-user activity and update its current-state projection."""
    init_db()
    user = _user_key(user_key)
    activity = _activity_type(activity_type)
    actor_key = " ".join((actor or "unknown").split()).strip() or "unknown"
    occurred_at = _now()

    with connect() as conn:
        identity = _identity_for_job(conn, job_id)
        if idempotency_key:
            prior = conn.execute(
                """
                SELECT id, activity_value
                  FROM user_job_activity_events
                 WHERE user_key=? AND actor=? AND idempotency_key=?
                """,
                (user, actor_key, idempotency_key),
            ).fetchone()
            if prior:
                return ActivityWriteResult(
                    changed=False,
                    event_id=int(prior[0]),
                    user_key=user,
                    job_identity_key=identity,
                    activity_type=activity,
                    value=bool(prior[1]),
                )

        cur = conn.execute(
            """
            INSERT INTO user_job_activity_events(
                user_key, job_identity_key, activity_type, activity_value,
                occurred_at, actor, note, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user,
                identity,
                activity,
                int(value),
                occurred_at,
                actor_key,
                note,
                idempotency_key,
            ),
        )
        event_id = int(cur.lastrowid)
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
            (user, identity, activity, int(value), occurred_at, event_id),
        )

    return ActivityWriteResult(
        changed=True,
        event_id=event_id,
        user_key=user,
        job_identity_key=identity,
        activity_type=activity,
        value=bool(value),
    )


def get_job_activity(
    job_id: int, *, user_key: str, history_limit: int = 100
) -> dict[str, Any]:
    init_db()
    user = _user_key(user_key)
    if history_limit < 1 or history_limit > 1000:
        raise ValueError("history_limit must be between 1 and 1000")
    with connect() as conn:
        identity = _identity_for_job(conn, job_id)
        current = [
            {**dict(row), "active": bool(row["active"])}
            for row in conn.execute(
                """
                SELECT activity_type, active, updated_at, last_event_id
                  FROM user_job_activity_current
                 WHERE user_key=? AND job_identity_key=?
                 ORDER BY activity_type
                """,
                (user, identity),
            )
        ]
        events = [
            {**dict(row), "activity_value": bool(row["activity_value"])}
            for row in conn.execute(
                """
                SELECT id, activity_type, activity_value, occurred_at, actor, note, idempotency_key
                  FROM user_job_activity_events
                 WHERE user_key=? AND job_identity_key=?
                 ORDER BY occurred_at DESC, id DESC
                 LIMIT ?
                """,
                (user, identity, history_limit),
            )
        ]
    return {
        "user_key": user,
        "job_id": job_id,
        "job_identity_key": identity,
        "current": current,
        "events": events,
    }
