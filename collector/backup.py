from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from collector import db
from collector.settings import get_setting

BACKUP_DIR = db.ROOT / "backups"


@dataclass(frozen=True)
class BackupResult:
    path: str
    size_bytes: int
    integrity: str
    backups_retained: int


def _stamp(now: datetime | None = None) -> str:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return current.strftime("%Y%m%dT%H%M%S.%fZ")


def list_backups(limit: int = 20, *, directory: Path | None = None) -> list[dict]:
    root = directory or BACKUP_DIR
    if not root.exists():
        return []
    rows = sorted(root.glob("market_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "path": str(path),
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds"),
        }
        for path in rows[: max(0, limit)]
    ]


def create_backup(
    *,
    keep_count: int | None = None,
    directory: Path | None = None,
    now: datetime | None = None,
) -> BackupResult:
    """Create a transactionally consistent SQLite backup and verify it before pruning old backups."""
    db.init_db()
    root = directory or BACKUP_DIR
    root.mkdir(parents=True, exist_ok=True)
    keep = int(keep_count if keep_count is not None else get_setting("backup.keep_count"))
    if keep < 1:
        raise ValueError("keep_count must be >= 1")

    target = root / f"market_{_stamp(now)}.db"
    source = db.connect()
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        integrity = str(result[0] if result else "unknown")
        if integrity.casefold() != "ok":
            raise RuntimeError(f"backup integrity_check failed: {integrity}")
    except Exception:
        destination.close()
        source.close()
        target.unlink(missing_ok=True)
        raise
    else:
        destination.close()
        source.close()

    backups = sorted(root.glob("market_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        old.unlink(missing_ok=True)
    retained = len(list(root.glob("market_*.db")))
    return BackupResult(
        path=str(target),
        size_bytes=target.stat().st_size,
        integrity=integrity,
        backups_retained=retained,
    )
