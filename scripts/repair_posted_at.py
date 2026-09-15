from __future__ import annotations

import argparse

from collector.posted_at_repair import repair_linkedin_posted_at


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill missing JMM posting dates from exact current source evidence."
    )
    parser.add_argument("--source", choices=("linkedin",), required=True)
    parser.add_argument("--limit", type=int, required=True)
    args = parser.parse_args()
    print(repair_linkedin_posted_at(limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
