from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from collector.db import ROOT, connect, init_db

CATALOG_PATH = ROOT / "config" / "geographies.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_catalog(path: Path = CATALOG_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("geographies") or []
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in rows:
        code = str(raw.get("code") or "").strip().upper()
        required = (
            "country",
            "label",
            "seek_location",
            "seek_state_slug",
            "linkedin_location",
            "help",
        )
        if (
            not code
            or code in seen
            or any(not str(raw.get(k) or "").strip() for k in required)
        ):
            raise ValueError(f"invalid geography catalog row: {raw!r}")
        seen.add(code)
        result.append({**raw, "code": code})
    return result


def seed_geographies() -> int:
    init_db()
    rows = load_catalog()
    with connect() as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO geographies(
                    code, country, label, enabled, seek_location, seek_state_slug,
                    linkedin_location, help_text, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    country=excluded.country,
                    label=excluded.label,
                    seek_location=excluded.seek_location,
                    seek_state_slug=excluded.seek_state_slug,
                    linkedin_location=excluded.linkedin_location,
                    help_text=excluded.help_text
                """,
                (
                    row["code"],
                    row["country"],
                    row["label"],
                    int(bool(row.get("enabled", True))),
                    row["seek_location"],
                    row["seek_state_slug"],
                    row["linkedin_location"],
                    row["help"],
                    _now(),
                ),
            )
    return len(rows)


def list_geographies(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    seed_geographies()
    sql = "SELECT * FROM geographies"
    if enabled_only:
        sql += " WHERE enabled=1"
    sql += " ORDER BY code"
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql)]


def get_geography(code: str) -> dict[str, Any]:
    seed_geographies()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM geographies WHERE code=?", (code.upper(),)
        ).fetchone()
    if not row:
        raise KeyError(code)
    return dict(row)


def set_geography_enabled(
    code: str, enabled: bool, *, actor: str = "rob"
) -> dict[str, Any]:
    seed_geographies()
    with connect() as conn:
        result = conn.execute(
            "UPDATE geographies SET enabled=?, updated_at=?, updated_by=? WHERE code=?",
            (int(enabled), _now(), actor, code.upper()),
        )
        if result.rowcount != 1:
            raise KeyError(code)
        row = conn.execute(
            "SELECT * FROM geographies WHERE code=?", (code.upper(),)
        ).fetchone()
    return dict(row)
