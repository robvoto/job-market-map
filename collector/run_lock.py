from __future__ import annotations

import errno
import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from collector.db import ROOT

LOCK_PATH = ROOT / "data" / "collection.lock"


class CollectionAlreadyRunning(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_metadata(handle: TextIO) -> dict:
    try:
        handle.seek(0)
        text = handle.read().strip()
        return json.loads(text) if text else {}
    except (json.JSONDecodeError, OSError):
        return {}


def lock_status(path: Path | None = None) -> dict:
    lock_path = path or LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    metadata = _read_metadata(handle)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno not in {errno.EACCES, errno.EAGAIN}:
            raise
        return {"active": True, "metadata": metadata, "path": str(lock_path)}
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        return {"active": False, "metadata": metadata, "path": str(lock_path)}


@contextmanager
def collection_run_lock(
    trigger: str, *, source: str | None = None, path: Path | None = None
) -> Iterator[dict]:
    lock_path = path or LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        metadata = _read_metadata(handle)
        handle.close()
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise CollectionAlreadyRunning(
                f"collection already running: {metadata or 'lock held'}"
            ) from exc
        raise

    metadata = {
        "pid": os.getpid(),
        "trigger": trigger,
        "source": source,
        "started_at": _now(),
    }
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(metadata, sort_keys=True))
    handle.flush()
    try:
        yield metadata
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
