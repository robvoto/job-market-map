from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from collector import db
from collector.backup import create_backup
from collector.run_lock import lock_status
from collector.settings import get_setting

PROMOTION_DIR = db.ROOT / "exports" / "aws-promotion"
SCHEMA_VERSION = 8


@dataclass(frozen=True)
class PromotionArtifact:
    promotion_id: str
    snapshot_path: str
    manifest_path: str
    sha256: str
    size_bytes: int
    git_commit: str
    schema_version: int
    created_at: str
    database_summary: dict[str, object]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def database_summary(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
        integrity = str(integrity_row[0] if integrity_row else "unknown")
        if integrity.casefold() != "ok":
            raise RuntimeError(f"database integrity_check failed: {integrity}")

        def count(sql: str, params: tuple[object, ...] = ()) -> int:
            return int(conn.execute(sql, params).fetchone()[0])

        return {
            "integrity": integrity,
            "jobs_total": count("SELECT COUNT(*) FROM jobs"),
            "seek_jobs": count("SELECT COUNT(*) FROM jobs WHERE source='seek'"),
            "linkedin_jobs": count("SELECT COUNT(*) FROM jobs WHERE source='linkedin'"),
            "jobs_with_jd": count(
                "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL AND trim(full_description)<>''"
            ),
            "jd_fetch_registry": count("SELECT COUNT(*) FROM jd_fetch_registry"),
            "same_vacancy_links": count("SELECT COUNT(*) FROM same_vacancy_links"),
            "card_captures": count("SELECT COUNT(*) FROM card_captures"),
            "consumer_checkpoints": count("SELECT COUNT(*) FROM consumer_checkpoints"),
        }
    finally:
        conn.close()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=db.ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def assert_repo_ready_for_promotion() -> str:
    """Require one clean, pushed main commit so AWS can reproduce the exact code."""
    if _git("status", "--porcelain"):
        raise RuntimeError("JMM repository must be clean before AWS promotion")
    _git("fetch", "origin")
    head = _git("rev-parse", "HEAD")
    origin_main = _git("rev-parse", "origin/main")
    branch = _git("branch", "--show-current")
    if branch != "main":
        raise RuntimeError(f"JMM promotion must run from main, not {branch or 'detached HEAD'}")
    if head != origin_main:
        raise RuntimeError(
            f"JMM main must be exactly synced with origin/main before promotion: {head} != {origin_main}"
        )
    return head


def assert_local_cutover_safe() -> None:
    lock = lock_status()
    if bool(lock.get("active")):
        raise RuntimeError(f"local JMM collection is active: {lock.get('metadata') or lock}")
    if bool(get_setting("scheduler.enabled")):
        raise RuntimeError(
            "local JMM scheduler must be disabled before creating an AWS promotion snapshot"
        )


def create_promotion_artifact(*, directory: Path | None = None) -> PromotionArtifact:
    """Create one consistent, integrity-checked local DB artifact for AWS staging."""
    assert_local_cutover_safe()
    git_commit = assert_repo_ready_for_promotion()
    root = (directory or PROMOTION_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    backup = create_backup(keep_count=50, directory=root)
    snapshot = Path(backup.path).resolve()
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    digest = sha256_file(snapshot)
    promotion_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{digest[:12]}"
    summary = database_summary(snapshot)
    artifact = PromotionArtifact(
        promotion_id=promotion_id,
        snapshot_path=str(snapshot),
        manifest_path=str(root / f"{promotion_id}.json"),
        sha256=digest,
        size_bytes=snapshot.stat().st_size,
        git_commit=git_commit,
        schema_version=SCHEMA_VERSION,
        created_at=created_at,
        database_summary=summary,
    )
    Path(artifact.manifest_path).write_text(
        json.dumps(asdict(artifact), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact


def load_artifact(path: Path) -> PromotionArtifact:
    payload = json.loads(path.read_text(encoding="utf-8"))
    artifact = PromotionArtifact(**payload)
    if artifact.schema_version != SCHEMA_VERSION:
        raise RuntimeError(
            f"promotion schema version {artifact.schema_version} != local {SCHEMA_VERSION}"
        )
    return artifact
