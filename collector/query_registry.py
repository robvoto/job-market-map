from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from collector.db import ROOT, connect, init_db
from collector.geographies import load_catalog

REGISTRY_PATH = ROOT / "queries" / "query_registry.json"


@dataclass(frozen=True)
class QuerySpec:
    key: str
    text: str
    origins: tuple[str, ...]
    sources: tuple[str, ...]
    geographies: tuple[str, ...]
    active: bool


def _default_geographies(payload: dict) -> tuple[str, ...]:
    configured = tuple(
        str(x).strip().upper()
        for x in payload.get("default_geographies") or []
        if str(x).strip()
    )
    if configured:
        return configured
    return tuple(row["code"] for row in load_catalog() if row.get("enabled", True))


def load_registry(path: Path = REGISTRY_PATH) -> list[QuerySpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    default_geographies = _default_geographies(payload)
    specs: list[QuerySpec] = []
    seen_keys: set[str] = set()
    for raw in payload.get("queries") or []:
        key = str(raw.get("key") or "").strip()
        text = " ".join(str(raw.get("text") or "").split()).strip()
        if not key or not text:
            raise ValueError("every registry query requires key and text")
        if key in seen_keys:
            raise ValueError(f"duplicate query key: {key}")
        seen_keys.add(key)
        specs.append(
            QuerySpec(
                key=key,
                text=text,
                origins=tuple(
                    dict.fromkeys(
                        str(x).strip()
                        for x in raw.get("origins") or []
                        if str(x).strip()
                    )
                ),
                sources=tuple(
                    dict.fromkeys(
                        str(x).strip().casefold()
                        for x in raw.get("sources") or []
                        if str(x).strip()
                    )
                ),
                geographies=tuple(
                    str(x).strip().upper()
                    for x in raw.get("geographies") or default_geographies
                    if str(x).strip()
                ),
                active=bool(raw.get("active", True)),
            )
        )
    return specs


def _location_for(source: str, geography: dict) -> str:
    if source == "seek":
        return geography["seek_location"]
    if source == "linkedin":
        return geography["linkedin_location"]
    return geography["label"]


def expanded_runs(path: Path = REGISTRY_PATH) -> list[dict]:
    geography_by_code = {row["code"]: row for row in load_catalog()}
    runs: list[dict] = []
    for spec in load_registry(path):
        if not spec.active:
            continue
        for source in spec.sources:
            for code in spec.geographies:
                if code not in geography_by_code:
                    raise ValueError(
                        f"query {spec.key!r} references unknown geography {code!r}"
                    )
                geography = geography_by_code[code]
                runs.append(
                    {
                        "registry_key": spec.key,
                        "query_text": spec.text,
                        "origins": list(spec.origins),
                        "source": source,
                        "geography_code": code,
                        "location": _location_for(source, geography),
                    }
                )
    return runs


def sync_registry(path: Path = REGISTRY_PATH) -> int:
    init_db()
    count = 0
    desired_runs = expanded_runs(path)
    seeded_keys = {spec.key for spec in load_registry(path)}
    with connect() as conn:
        # One-time/ongoing migration: old seed rows had no geography_code and were Sydney-only.
        # Do not delete them; deactivate so their history remains available without wasting searches.
        if seeded_keys:
            placeholders = ",".join("?" for _ in seeded_keys)
            conn.execute(
                f"UPDATE queries SET active=0 WHERE geography_code IS NULL AND registry_key IN ({placeholders})",
                tuple(sorted(seeded_keys)),
            )
        for run in desired_runs:
            conn.execute(
                """
                INSERT INTO queries(source, query_text, location, geography_code, active, created_at,
                                    registry_key, origins_json)
                VALUES (?, ?, ?, ?, 1, datetime('now'), ?, ?)
                ON CONFLICT(source, query_text, location) DO UPDATE SET
                    geography_code = excluded.geography_code,
                    registry_key = excluded.registry_key,
                    origins_json = excluded.origins_json
                """,
                (
                    run["source"],
                    run["query_text"],
                    run["location"],
                    run["geography_code"],
                    run["registry_key"],
                    json.dumps(run["origins"], ensure_ascii=False),
                ),
            )
            count += 1
    return count


if __name__ == "__main__":
    specs = load_registry()
    runs = expanded_runs()
    synced = sync_registry()
    print(f"query_specs={len(specs)} source_geography_runs={len(runs)} synced={synced}")
