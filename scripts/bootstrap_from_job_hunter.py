from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector import db
from collector.backup import create_backup
from collector.bootstrap_jh import (
    apply_bootstrap,
    plan_bootstrap,
    require_matching_dry_run,
    write_report,
)

DRY_RUN_REPORT = ROOT / "exports" / "jmm006_job_hunter_bootstrap_dry_run.json"
APPLY_REPORT = ROOT / "exports" / "jmm006_job_hunter_bootstrap_apply.json"


def _print(report) -> None:
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))


def _run_normal_collection() -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.run_collection_cycle", "--trigger", "manual"],
        cwd=ROOT,
        check=False,
    )
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="JMM-006: one-off bootstrap from trustworthy Job Hunter neutral job/JD evidence."
    )
    parser.add_argument(
        "--job-hunter-db",
        required=True,
        type=Path,
        help="Path to the existing Job Hunter app.db. It is opened read-only.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the import. Refused until a matching dry-run report exists.",
    )
    args = parser.parse_args()

    plan = plan_bootstrap(args.job_hunter_db, jmm_db_path=db.DB_PATH)
    if not args.apply:
        write_report(DRY_RUN_REPORT, plan.report)
        _print(plan.report)
        print(f"Dry-run report: {DRY_RUN_REPORT}")
        print("No Job Hunter or Job Market Map database rows were changed.")
        return 0

    require_matching_dry_run(DRY_RUN_REPORT, args.job_hunter_db)
    backup = create_backup()
    report = apply_bootstrap(plan)
    report.backup_path = backup.path
    write_report(APPLY_REPORT, report)
    _print(report)
    print(f"Verified pre-import backup: {backup.path}")
    print(f"Apply report: {APPLY_REPORT}")

    collection_rc = _run_normal_collection()
    report.collection_status = "COMPLETE" if collection_rc == 0 else f"FAILED_RC_{collection_rc}"
    write_report(APPLY_REPORT, report)
    if collection_rc != 0:
        print(
            "JMM-006 import completed, but the required normal JMM collection cycle failed "
            f"with return code {collection_rc}."
        )
        return collection_rc
    print("Normal JMM collection cycle completed after the bootstrap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
