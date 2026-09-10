from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from collector.db import ROOT, connect, init_db

REGISTRY_PATH = ROOT / "queries" / "query_registry.json"


@dataclass(frozen=True)
class QuerySpec:
    key: str
    text: str
    origins: tuple[str, ...]
    sources: tuple[str, ...]
    locations: tuple[str, ...]
    active: bool


def load_registry(path: Path = REGISTRY_PATH) -> list[QuerySpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    default_locations = tuple(payload.get("default_locations") or [])
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
                locations=tuple(raw.get("locations") or default_locations),
                active=bool(raw.get("active", True)),
            )
        )
    return specs


def expanded_runs(path: Path = REGISTRY_PATH) -> list[dict]:
    runs: list[dict] = []
    for spec in load_registry(path):
        if not spec.active:
            continue
        for source in spec.sources:
            for location in spec.locations or ("",):
                runs.append(
                    {
                        "registry_key": spec.key,
                        "query_text": spec.text,
                        "origins": list(spec.origins),
                        "source": source,
                        "location": location,
                    }
                )
    return runs


def sync_registry(path: Path = REGISTRY_PATH) -> int:
    init_db()
    count = 0
    with connect() as conn:
        for run in expanded_runs(path):
            conn.execute(
                """
                INSERT INTO queries(source, query_text, location, active, created_at,
                                    registry_key, origins_json)
                VALUES (?, ?, ?, 1, datetime('now'), ?, ?)
                ON CONFLICT(source, query_text, location) DO UPDATE SET
                    registry_key = excluded.registry_key,
                    origins_json = excluded.origins_json
                """,
                (
                    run["source"],
                    run["query_text"],
                    run["location"],
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
    print(f"query_specs={len(specs)} source_location_runs={len(runs)} synced={synced}")
