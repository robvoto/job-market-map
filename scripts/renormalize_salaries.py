"""Preview or backup-and-apply deterministic salary re-normalization."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import db
from collector.salary import renormalize_salary_rows


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _verified_backup(path: Path) -> dict[str, object]:
    backup_dir = path.resolve().parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    target = backup_dir / f"{path.stem}.pre_salary_renormalization_{stamp}.db"
    source = _connect_read_only(path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        integrity = str(destination.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity.casefold() != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
    except Exception:
        destination.close()
        source.close()
        target.unlink(missing_ok=True)
        raise
    destination.close()
    source.close()
    return {"path": str(target), "size_bytes": target.stat().st_size, "integrity": integrity}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute salary facts from preserved raw salary text. Preview is read-only; "
            "apply requires a verified backup and an explicit stopped-runtime acknowledgement."
        )
    )
    parser.add_argument("--db", type=Path, required=True, help="Exact JMM SQLite DB path.")
    parser.add_argument("--apply", action="store_true", help="Write normalized salary facts.")
    parser.add_argument(
        "--acknowledge-quiescent",
        action="store_true",
        help="Confirm the JMM API and all collection processes are stopped.",
    )
    args = parser.parse_args()
    path = args.db.expanduser().resolve()
    if not path.is_file():
        parser.error(f"database does not exist: {path}")
    if args.apply and not args.acknowledge_quiescent:
        parser.error("--apply requires --acknowledge-quiescent")

    if not args.apply:
        with _connect_read_only(path) as conn:
            report = renormalize_salary_rows(conn)
            integrity = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        report.update({"mode": "preview_read_only", "db_integrity": integrity})
        print(json.dumps(report, sort_keys=True))
        return 0

    backup = _verified_backup(path)
    db.DB_PATH = path
    db.init_db()
    with db.connect() as conn:
        report = renormalize_salary_rows(conn, apply=True)
    with _connect_read_only(path) as conn:
        after = {
            str(row[0] or "<null>"): int(row[1])
            for row in conn.execute(
                "SELECT salary_normalized_state,COUNT(*) FROM jobs GROUP BY 1"
            )
        }
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    if integrity.casefold() != "ok" or after != report["after_by_state"]:
        raise RuntimeError("post-write salary verification failed")
    report.update(
        {
            "mode": "applied",
            "backup": backup,
            "verified_after_by_state": after,
            "db_integrity": integrity,
        }
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
