from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import db
from collector.posted_at_audit import audit_posted_at_completeness


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report source-backed JMM posting-date completeness without writing data."
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--db", type=Path, help="Explicit JMM SQLite database path.")
    args = parser.parse_args()
    if args.db:
        db.DB_PATH = args.db
    report = [audit.as_dict() for audit in audit_posted_at_completeness()]
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for audit in report:
            print(
                f"{audit['source']}: total={audit['total']} "
                f"with_posted_at={audit['with_posted_at']} "
                f"missing={audit['missing_posted_at']} "
                f"retryable={audit['retryable_repair_candidates']} "
                f"without_supported_repair_path={audit['missing_without_supported_repair_path']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
