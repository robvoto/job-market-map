from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from collector.settings import get_setting
from collector.source_campaign import get_or_start_cycle
from sources.linkedin_collector import collect_linkedin_chunk


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run/resume one LinkedIn JobSpy/HTTP query chunk."
    )
    parser.add_argument("query")
    parser.add_argument("--location", default="Sydney NSW")
    parser.add_argument("--geography-code", default=None)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument(
        "--results-wanted",
        type=int,
        default=None,
        help="Diagnostic override for this one query only; normal runs use the Admin setting.",
    )
    args = parser.parse_args(argv)
    cycle = get_or_start_cycle("linkedin")
    result = collect_linkedin_chunk(
        args.query,
        args.location,
        geography_code=args.geography_code,
        cycle_key=str(cycle["cycle_key"]),
        days=int(
            args.days
            if args.days is not None
            else get_setting("collection.default_freshness_days")
        ),
        should_stop=lambda: False,
        results_wanted=args.results_wanted,
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.status in {"COMPLETE", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
