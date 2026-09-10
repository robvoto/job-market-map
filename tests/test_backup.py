import sqlite3
from datetime import UTC, datetime, timedelta

from collector import db


def test_backup_is_consistent_integrity_checked_and_retained(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import backup

    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO jobs(source,source_job_id,canonical_url,title)
               VALUES('seek','42','https://seek.test/42','Important role')"""
        )

    root = tmp_path / "backups"
    base = datetime(2026, 9, 10, tzinfo=UTC)
    for index in range(3):
        result = backup.create_backup(
            keep_count=2,
            directory=root,
            now=base + timedelta(seconds=index),
        )
        assert result.integrity == "ok"

    files = sorted(root.glob("market_*.db"))
    assert len(files) == 2
    with sqlite3.connect(files[-1]) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT title FROM jobs WHERE source_job_id='42'").fetchone()[0] == "Important role"
