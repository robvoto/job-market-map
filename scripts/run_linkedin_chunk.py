from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from sources.linkedin_collector import collect_linkedin_chunk

p = argparse.ArgumentParser(
    description="Run/resume one bounded LinkedIn card-only chunk."
)
p.add_argument("query")
p.add_argument("--location", default="Sydney NSW")
p.add_argument(
    "--days",
    type=int,
    default=None,
    help="Override admin freshness setting for this run only.",
)
p.add_argument(
    "--max-offsets",
    type=int,
    default=None,
    help="Override admin chunk-size setting for this run only.",
)
p.add_argument(
    "--reset",
    action="store_true",
    help="Intentionally restart this query from offset zero.",
)
a = p.parse_args()
print(
    json.dumps(
        asdict(
            collect_linkedin_chunk(
                a.query,
                a.location,
                days=a.days,
                max_offsets=a.max_offsets,
                reset=a.reset,
            )
        ),
        indent=2,
    )
)
