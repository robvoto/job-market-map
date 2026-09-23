from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from collector.db import ROOT
from collector.geographies import load_catalog

PROFILE_PATH = ROOT / "config" / "collection_profile.json"
SYDNEY = ZoneInfo("Australia/Sydney")


def load_profile(path: Path = PROFILE_PATH) -> dict:
    profile = json.loads(path.read_text(encoding="utf-8"))
    sources = {str(x).strip().casefold() for x in profile.get("enabled_sources", [])}
    states = [str(x).strip().upper() for x in profile.get("enabled_states", [])]
    configured = {row["code"] for row in load_catalog()}
    if not sources or not sources <= {"seek", "linkedin"}:
        raise ValueError("collection profile enabled_sources must use seek/linkedin")
    if not states or not set(states) <= configured:
        raise ValueError(f"collection profile enabled_states must use {sorted(configured)}")
    if int(profile.get("lookback_days", 0)) < 2:
        raise ValueError("lookback_days must be at least 2 for previous-midnight coverage")
    if profile.get("lookback_anchor") != "previous_midnight":
        raise ValueError("lookback_anchor must be previous_midnight")
    profile["enabled_sources"] = sorted(sources)
    profile["enabled_states"] = states
    return profile


def enabled_seek_states() -> list[str]:
    profile = load_profile()
    if "seek" not in profile["enabled_sources"]:
        return []
    return profile["enabled_states"]


def previous_midnight_cutoff(now: datetime | None = None) -> datetime:
    local_now = (now or datetime.now(SYDNEY)).astimezone(SYDNEY)
    previous_day = local_now.date() - timedelta(days=1)
    return datetime.combine(previous_day, time.min, tzinfo=SYDNEY)
