from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from collector.campaign import registry_runs, run_steps_in_one_browser_tab
from collector.settings import get_setting

p = argparse.ArgumentParser(description="Run neutral Job Market Map collection steps.")
p.add_argument("--source", action="append", choices=["seek", "linkedin", "apsjobs"])
p.add_argument(
    "--max-runs",
    type=int,
    default=None,
    help="Override admin campaign chunk size. This bounds one invocation, not total market coverage.",
)
p.add_argument(
    "--linkedin-max-offsets",
    type=int,
    default=None,
    help="Override admin setting for this run only.",
)
p.add_argument(
    "--days",
    type=int,
    default=None,
    help="Override admin freshness setting for this run only.",
)
a = p.parse_args()

max_runs = int(
    a.max_runs
    if a.max_runs is not None
    else get_setting("collection.campaign_query_chunk_size")
)
runs = registry_runs(sources=set(a.source) if a.source else None)[: max(0, max_runs)]
steps = run_steps_in_one_browser_tab(
    runs,
    linkedin_max_offsets=a.linkedin_max_offsets,
    days=a.days,
)
for step in steps:
    print(json.dumps(asdict(step), ensure_ascii=False))
print(
    json.dumps(
        {
            "steps": len(steps),
            "observed": sum(step.observed for step in steps),
            "new_jobs": sum(step.new_jobs for step in steps),
        },
        indent=2,
    )
)
