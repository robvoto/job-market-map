from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import db
from collector.posted_at_repair import repair_linkedin_posted_at


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill missing JMM posting dates from exact current source evidence."
    )
    parser.add_argument("--source", choices=("linkedin",), required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--db", type=Path, help="Explicit JMM SQLite database path.")
    args = parser.parse_args()
    if args.db:
        db.DB_PATH = args.db
    print(repair_linkedin_posted_at(limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
