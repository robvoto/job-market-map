from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from collector.db import ROOT
from collector.run_lock import lock_status
from collector.run_logging import LOG_PATH
from collector.service_state import latest_market_run, latest_seek_market_run


class CollectionProcessError(RuntimeError):
    pass


class CollectionProcessManager:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._process: subprocess.Popen | None = None

    def _refresh(self) -> None:
        if self._process is not None and self._process.poll() is not None:
            self._process = None

    def status(self) -> dict:
        with self._guard:
            self._refresh()
            lock = lock_status()
            pid = (
                self._process.pid
                if self._process is not None
                else (lock.get("metadata") or {}).get("pid")
                if lock["active"]
                else None
            )
            return {
                "active": bool(lock["active"] or self._process is not None),
                "pid": pid,
                "lock": lock,
                "active_source": (lock.get("metadata") or {}).get("source") if lock["active"] else None,
                "latest_run": latest_market_run(),
                "latest_seek_run": latest_seek_market_run(),
                "log_path": str(LOG_PATH),
            }

    def start(self, *, trigger: str) -> dict:
        if trigger not in {"manual", "scheduled"}:
            raise ValueError("trigger must be manual or scheduled")
        with self._guard:
            self._refresh()
            current_lock = lock_status()
            if self._process is not None or current_lock["active"]:
                raise CollectionProcessError("A collection process is already running.")
            # The runner itself writes structured output to logs/collection.log and
            # stdout. Inherit stdout/stderr here so service/journal output and the
            # durable runner log contain the same messages without double-writing.
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "scripts.run_collection_cycle",
                    "--trigger",
                    trigger,
                ],
                cwd=ROOT,
                start_new_session=True,
            )
            self._process = process
            return {
                "started": True,
                "pid": process.pid,
                "trigger": trigger,
                "log_path": str(LOG_PATH),
            }

    def start_linkedin(self, *, hours_old: int, cycle_key: str) -> dict:
        if hours_old < 1:
            raise ValueError("hours_old must be >= 1")
        if not str(cycle_key or "").strip():
            raise ValueError("cycle_key is required")
        with self._guard:
            self._refresh()
            current_lock = lock_status()
            if self._process is not None or current_lock["active"]:
                raise CollectionProcessError("A collection process is already running.")
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "scripts.run_linkedin_market",
                    "--hours-old",
                    str(int(hours_old)),
                    "--cycle-key",
                    str(cycle_key),
                    "--trigger",
                    "linkedin-scheduled",
                ],
                cwd=ROOT,
                start_new_session=True,
            )
            self._process = process
            return {
                "started": True,
                "pid": process.pid,
                "trigger": "linkedin-scheduled",
                "hours_old": int(hours_old),
                "cycle_key": str(cycle_key),
                "log_path": str(LOG_PATH),
            }

    @staticmethod
    def _managed_pid(pid: int) -> bool:
        try:
            cmdline = (
                Path(f"/proc/{pid}/cmdline")
                .read_bytes()
                .replace(b"\x00", b" ")
                .decode("utf-8", "replace")
            )
        except OSError:
            return False
        return (
            "scripts.run_collection_cycle" in cmdline
            or "scripts.run_linkedin_market" in cmdline
        )

    def stop(self) -> dict:
        with self._guard:
            self._refresh()
            pid = self._process.pid if self._process is not None else None
            if pid is None:
                lock = lock_status()
                pid = (
                    (lock.get("metadata") or {}).get("pid") if lock["active"] else None
                )
            if pid is None:
                return {
                    "stop_requested": False,
                    "message": "No collection process is running.",
                }
            pid = int(pid)
            if not self._managed_pid(pid):
                raise CollectionProcessError(
                    f"Refusing to signal PID {pid}: it is not a Job Market Map collection process."
                )
            os.kill(pid, signal.SIGTERM)
            return {
                "stop_requested": True,
                "pid": pid,
                "message": "Graceful stop requested. The current unit of collection work will finish safely before exit.",
            }


PROCESS_MANAGER = CollectionProcessManager()
