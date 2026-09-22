from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import db
from collector.posting_date_backfill import (
    backfill_linkedin_posting_dates,
    backfill_seek_posting_dates,
)
from scripts.renormalize_salaries import _verified_backup


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill posting dates from retained SEEK evidence or recheck LinkedIn."
    )
    parser.add_argument("--source", choices=("seek", "linkedin"), required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--db", type=Path, help="Explicit JMM SQLite database path.")
    parser.add_argument("--apply", action="store_true", help="Write dates and field-state evidence.")
    parser.add_argument(
        "--acknowledge-quiescent", action="store_true",
        help="Confirm the JMM API and all collection processes are stopped.",
    )
    args = parser.parse_args()
    if args.db:
        db.DB_PATH = args.db
    if args.apply:
        if not args.acknowledge_quiescent:
            parser.error("--apply requires --acknowledge-quiescent")
        backup = _verified_backup(db.DB_PATH)
        print(
            f"Verified pre-backfill backup: {backup['path']} "
            f"({backup['integrity']})"
        )
        db.init_db()
    if args.source == "seek":
        result = backfill_seek_posting_dates(apply=args.apply, limit=args.limit)
    else:
        result = backfill_linkedin_posting_dates(
            apply=args.apply, limit=args.limit
        )
    print(json.dumps(asdict(result) if hasattr(result, "__dataclass_fields__") else result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
