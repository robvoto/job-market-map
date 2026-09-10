from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from collector.db import ROOT, connect, init_db

CATALOG_PATH = ROOT / "config" / "settings_catalog.json"


class SettingError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _catalog(path: Path = CATALOG_PATH) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("settings") or []
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("key") or "").strip()
        if not key or key in result:
            raise SettingError(f"invalid or duplicate setting key: {key!r}")
        result[key] = row
    return result


def seed_settings() -> int:
    init_db()
    catalog = _catalog()
    with connect() as conn:
        for key, spec in catalog.items():
            conn.execute(
                """
                INSERT INTO settings(
                    key, category, value_type, value_json, default_json,
                    min_value, max_value, help_text, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    category=excluded.category,
                    value_type=excluded.value_type,
                    default_json=excluded.default_json,
                    min_value=excluded.min_value,
                    max_value=excluded.max_value,
                    help_text=excluded.help_text
                """,
                (
                    key,
                    spec["category"],
                    spec["type"],
                    json.dumps(spec["default"]),
                    json.dumps(spec["default"]),
                    spec.get("min"),
                    spec.get("max"),
                    spec["help"],
                    _now(),
                ),
            )
    return len(catalog)


def _validate(spec: dict[str, Any], value: Any) -> Any:
    value_type = spec["type"]
    if value_type == "boolean":
        if type(value) is not bool:
            raise SettingError("value must be boolean")
        return value
    if value_type == "integer":
        if type(value) is bool or not isinstance(value, int):
            raise SettingError("value must be an integer")
    elif value_type == "number":
        if type(value) is bool or not isinstance(value, (int, float)):
            raise SettingError("value must be a number")
        value = float(value)
    else:
        raise SettingError(f"unsupported setting type: {value_type}")

    minimum = spec.get("min")
    maximum = spec.get("max")
    if minimum is not None and value < minimum:
        raise SettingError(f"value must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise SettingError(f"value must be <= {maximum}")
    return value


def list_settings() -> list[dict[str, Any]]:
    seed_settings()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM settings ORDER BY category, key").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["value"] = json.loads(item.pop("value_json"))
        item["default"] = json.loads(item.pop("default_json"))
        result.append(item)
    return result


def get_setting(key: str) -> Any:
    # Hot-path read: ingestion can call this thousands of times. Do not rewrite the
    # whole settings catalog on every lookup. Seed only when the requested row is absent.
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT value_json FROM settings WHERE key=?", (key,)
        ).fetchone()
    if not row:
        seed_settings()
        with connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM settings WHERE key=?", (key,)
            ).fetchone()
    if not row:
        raise KeyError(key)
    return json.loads(row[0])


def set_setting(key: str, value: Any, *, actor: str = "admin") -> dict[str, Any]:
    seed_settings()
    catalog = _catalog()
    if key not in catalog:
        raise KeyError(key)
    validated = _validate(catalog[key], value)

    # Cross-setting lifecycle guard: removal must happen after archival.
    if key == "retention.remove_archived_after_days":
        archive_days = int(get_setting("retention.archive_after_days"))
        if int(validated) <= archive_days:
            raise SettingError(
                "remove_archived_after_days must be greater than archive_after_days"
            )
    if key == "retention.archive_after_days":
        remove_days = int(get_setting("retention.remove_archived_after_days"))
        if int(validated) >= remove_days:
            raise SettingError(
                "archive_after_days must be less than remove_archived_after_days"
            )
    if key == "api.default_page_size":
        max_size = int(get_setting("api.max_page_size"))
        if int(validated) > max_size:
            raise SettingError("default_page_size cannot exceed max_page_size")
    if key == "api.max_page_size":
        default_size = int(get_setting("api.default_page_size"))
        if int(validated) < default_size:
            raise SettingError("max_page_size cannot be less than default_page_size")

    with connect() as conn:
        conn.execute(
            "UPDATE settings SET value_json=?, updated_at=?, updated_by=? WHERE key=?",
            (json.dumps(validated), _now(), actor, key),
        )
    return next(item for item in list_settings() if item["key"] == key)


def reset_setting(key: str, *, actor: str = "admin") -> dict[str, Any]:
    catalog = _catalog()
    if key not in catalog:
        raise KeyError(key)
    return set_setting(key, catalog[key]["default"], actor=actor)
