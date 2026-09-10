from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any


class BrowserBrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrokerResponse:
    result: Any
    elapsed_seconds: float


def _powershell_exe() -> str:
    exe = shutil.which("powershell.exe")
    if not exe:
        raise BrowserBrokerError(
            "powershell.exe is unavailable; Windows interop is required from WSL"
        )
    return exe


def browser_command(
    command: str,
    payload: dict[str, Any] | None = None,
    *,
    page_id: int | None = None,
    timeout_seconds: int = 30,
    profile: str = "rob",
) -> BrokerResponse:
    """Call the Windows-local Human MCP browser broker from WSL without exposing it on the LAN."""
    request = {
        "command": command,
        "payload": payload or {},
        "timeout_seconds": timeout_seconds,
        "page_id": page_id,
    }
    request_json = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    # Base64 avoids PowerShell quoting/Unicode problems with arbitrary payloads.
    import base64

    encoded = base64.b64encode(request_json.encode("utf-8")).decode("ascii")
    ps = rf"""
$ErrorActionPreference='Stop'
$bytes=[Convert]::FromBase64String('{encoded}')
$body=[Text.Encoding]::UTF8.GetString($bytes)
$r=Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8766/command?profile={profile}' -ContentType 'application/json; charset=utf-8' -Body $body -TimeoutSec {timeout_seconds + 5}
$json=$r | ConvertTo-Json -Compress -Depth 20
$out=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
Write-Output $out
"""
    started = time.perf_counter()
    proc = subprocess.run(
        [_powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", ps],
        text=True,
        capture_output=True,
        timeout=timeout_seconds + 15,
        check=False,
    )
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        raise BrowserBrokerError(
            (proc.stderr or proc.stdout or "browser broker call failed").strip()
        )
    try:
        response_bytes = base64.b64decode(proc.stdout.strip(), validate=True)
        response = json.loads(response_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrowserBrokerError(
            f"invalid base64 broker response: {proc.stdout[:200]!r}"
        ) from exc
    if not response.get("ok"):
        raise BrowserBrokerError(str(response.get("error") or "browser command failed"))
    return BrokerResponse(result=response.get("result"), elapsed_seconds=elapsed)


def list_pages() -> BrokerResponse:
    return browser_command("list_pages")


def open_tab(url: str, *, active: bool = False) -> BrokerResponse:
    return browser_command("open_tab", {"url": url, "active": active})


def navigate(page_id: int, url: str) -> BrokerResponse:
    return browser_command("navigate", {"url": url}, page_id=page_id)


def snapshot(page_id: int, *, verbose: bool = True) -> BrokerResponse:
    return browser_command("snapshot", {"verbose": verbose}, page_id=page_id)
