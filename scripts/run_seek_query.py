from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from sources.seek_collector import collect_seek_query

p = argparse.ArgumentParser(
    description="Collect one SEEK query to exhaustion using result cards only."
)
p.add_argument("query")
p.add_argument("--location", default="Sydney NSW")
p.add_argument(
    "--days",
    type=int,
    default=None,
    help="Override admin freshness setting for this run only.",
)
a = p.parse_args()
print(
    json.dumps(asdict(collect_seek_query(a.query, a.location, days=a.days)), indent=2)
)
