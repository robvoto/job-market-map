from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from collector.geographies import load_catalog
from collector.settings import get_setting
from sources.seek_market_map import collect_enabled_states, collect_states

p = argparse.ArgumentParser(
    description="Collect whole-state SEEK coverage in resumable partition chunks."
)
p.add_argument(
    "--state",
    action="append",
    choices=[row["code"] for row in load_catalog()],
    help="Repeat to run selected states; default is all enabled states.",
)
p.add_argument(
    "--days",
    type=int,
    default=None,
    help="Override admin freshness horizon for this run only.",
)
p.add_argument(
    "--max-partitions",
    type=int,
    default=None,
    help="Bound unfinished partitions processed per state in this invocation. Default comes from Admin.",
)
p.add_argument(
    "--fresh",
    action="store_true",
    help="Reprocess completed partitions instead of resuming them. Use for an intentional fresh coverage pass.",
)
a = p.parse_args()
max_partitions = (
    a.max_partitions
    if a.max_partitions is not None
    else int(get_setting("collection.seek_partition_chunk_size"))
)
kwargs = {
    "days": a.days,
    "max_partitions": max_partitions,
    "resume": not a.fresh,
}
results = (
    collect_states(a.state, **kwargs) if a.state else collect_enabled_states(**kwargs)
)
print(json.dumps([asdict(r) for r in results], indent=2))
