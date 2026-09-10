from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from sources.seek_market_map import collect_enabled_states, collect_states

p = argparse.ArgumentParser(
    description="Collect whole-state SEEK market coverage with automatic partitions."
)
p.add_argument(
    "--state",
    action="append",
    choices=["NSW", "ACT", "QLD"],
    help="Repeat to run selected states; default is all enabled states.",
)
p.add_argument(
    "--days",
    type=int,
    default=None,
    help="Override admin freshness horizon for this run only.",
)
a = p.parse_args()
results = (
    collect_states(a.state, days=a.days)
    if a.state
    else collect_enabled_states(days=a.days)
)
print(json.dumps([asdict(r) for r in results], indent=2))
