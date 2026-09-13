from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from collector.promotion import (
    assert_local_cutover_safe,
    create_promotion_artifact,
    load_artifact,
)

DEFAULT_REGION = "ap-southeast-2"
DEFAULT_APP_DIR = "/home/ubuntu/job-market-map"
DEFAULT_PERSIST_ROOT = "/var/lib/job-hunter/job-market-map"
DEFAULT_JH_DIR = "/home/ubuntu/job-hunter-agent"
DEFAULT_JMM_URL = "http://127.0.0.1:8770/v3"
TERMINAL_COMMAND_STATUSES = {"Success", "Cancelled", "TimedOut", "Failed", "Cancelling"}


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, capture_output=True, text=True)


def _aws(region: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["aws", "--region", region, *args], check=check)


def _send_ssm(
    *, instance_id: str, region: str, comment: str, commands: list[str], timeout: int = 300
) -> dict[str, Any]:
    params = json.dumps({"commands": commands})
    sent = _aws(
        region,
        "ssm",
        "send-command",
        "--instance-ids",
        instance_id,
        "--document-name",
        "AWS-RunShellScript",
        "--comment",
        comment,
        "--parameters",
        params,
        "--timeout-seconds",
        str(timeout),
        "--query",
        "Command.CommandId",
        "--output",
        "text",
    )
    command_id = sent.stdout.strip()
    deadline = time.monotonic() + timeout + 60
    while time.monotonic() < deadline:
        result = _aws(
            region,
            "ssm",
            "get-command-invocation",
            "--command-id",
            command_id,
            "--instance-id",
            instance_id,
            "--output",
            "json",
            check=False,
        )
        if result.returncode == 0:
            payload = json.loads(result.stdout)
            if payload.get("Status") in TERMINAL_COMMAND_STATUSES:
                if payload.get("Status") != "Success":
                    raise RuntimeError(
                        f"AWS SSM command {command_id} failed: {payload.get('Status')}\n"
                        f"stdout:\n{payload.get('StandardOutputContent', '')}\n"
                        f"stderr:\n{payload.get('StandardErrorContent', '')}"
                    )
                return payload
        time.sleep(2)
    raise TimeoutError(f"AWS SSM command {command_id} did not finish in time")


def _remote_verify_python(manifest_b64: str, staged_path: str) -> str:
    return f"""python3 - <<'PY'\nimport base64,json,sqlite3\nmanifest=json.loads(base64.b64decode({manifest_b64!r}).decode())\npath={staged_path!r}\nconn=sqlite3.connect(path)\ntry:\n    integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]\n    if str(integrity).lower() != 'ok': raise SystemExit(f'integrity={{integrity}}')\n    q=lambda sql: int(conn.execute(sql).fetchone()[0])\n    actual={{\n      'integrity': str(integrity),\n      'jobs_total': q('SELECT COUNT(*) FROM jobs'),\n      'seek_jobs': q(\"SELECT COUNT(*) FROM jobs WHERE source='seek'\"),\n      'linkedin_jobs': q(\"SELECT COUNT(*) FROM jobs WHERE source='linkedin'\"),\n      'jobs_with_jd': q(\"SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL AND trim(full_description)<>''\"),\n      'jd_fetch_registry': q('SELECT COUNT(*) FROM jd_fetch_registry'),\n      'same_vacancy_links': q('SELECT COUNT(*) FROM same_vacancy_links'),\n      'card_captures': q('SELECT COUNT(*) FROM card_captures'),\n      'consumer_checkpoints': q('SELECT COUNT(*) FROM consumer_checkpoints'),\n    }}\nfinally:\n    conn.close()\nexpected=manifest['database_summary']\nif actual != expected:\n    raise SystemExit('summary mismatch expected='+json.dumps(expected,sort_keys=True)+' actual='+json.dumps(actual,sort_keys=True))\nprint('DB_SUMMARY_OK '+json.dumps(actual,sort_keys=True))\nPY"""


def stage(args: argparse.Namespace) -> int:
    artifact = create_promotion_artifact()
    manifest = asdict(artifact)
    manifest_b64 = base64.b64encode(json.dumps(manifest).encode()).decode()
    snapshot = Path(artifact.snapshot_path)
    s3_key = f"{args.prefix.strip('/')}/{artifact.promotion_id}/market.db"
    s3_uri = f"s3://{args.bucket}/{s3_key}"
    print(f"Promotion artifact: {artifact.manifest_path}")
    print(f"Snapshot: {snapshot} ({artifact.size_bytes} bytes)")
    print(f"SHA-256: {artifact.sha256}")
    print(f"Uploading temporary encrypted transfer object: {s3_uri}")

    staged = f"{args.persist_root}/promotion/{artifact.promotion_id}.db"
    remote_manifest = f"{args.persist_root}/promotion/staged-manifest.json"
    db_path = f"{args.persist_root}/data/market.db"
    backup_path = f"{args.persist_root}/backups/pre_promotion_{artifact.promotion_id}.db"
    stage_rollback = (
        "stage_rollback() { rc=$?; trap - ERR; "
        "systemctl stop job-market-map.service >/dev/null 2>&1 || true; "
        "systemctl stop job-market-map-browser.service >/dev/null 2>&1 || true; "
        f"if [ \"${{STAGE_REPLACED:-0}}\" = 1 ]; then "
        f"rm -f {shlex.quote(db_path)} {shlex.quote(db_path + '-wal')} {shlex.quote(db_path + '-shm')}; "
        f"if [ -f {shlex.quote(backup_path)} ]; then cp -a {shlex.quote(backup_path)} {shlex.quote(db_path)}; "
        f"chown ubuntu:ubuntu {shlex.quote(db_path)}; echo STAGE_ROLLBACK_RESTORED_PREVIOUS_AWS_DB; "
        "else echo STAGE_ROLLBACK_REMOVED_FAILED_FIRST_STAGE_DB; fi; fi; exit $rc; }; trap stage_rollback ERR"
    )
    commands = [
        "#!/bin/bash",
        "set -euo pipefail",
        "STAGE_REPLACED=0",
        stage_rollback,
        "if systemctl is-active --quiet job-market-map.service; then echo 'JMM API must be inactive for stage' >&2; exit 20; fi",
        "if systemctl is-active --quiet job-market-map-browser.service; then echo 'JMM browser must be inactive for stage' >&2; exit 21; fi",
        "if systemctl is-enabled --quiet job-market-map.service; then echo 'JMM API must be disabled for stage' >&2; exit 22; fi",
        "if systemctl is-enabled --quiet job-market-map-browser.service; then echo 'JMM browser must be disabled for stage' >&2; exit 23; fi",
        "if grep -q '^JOB_HUNTER_MARKET_MAP_BASE_URL=' /etc/job-hunter/job-hunter.env; then echo 'Job Hunter must not depend on JMM before stage' >&2; exit 24; fi",
        f"install -d -o ubuntu -g ubuntu {shlex.quote(args.persist_root + '/promotion')} {shlex.quote(args.persist_root + '/backups')} {shlex.quote(args.persist_root + '/data')}",
        f"sudo -u ubuntu git -C {shlex.quote(args.app_dir)} fetch origin main",
        f"sudo -u ubuntu git -C {shlex.quote(args.app_dir)} checkout --detach {shlex.quote(artifact.git_commit)}",
        f"test \"$(sudo -u ubuntu git -C {shlex.quote(args.app_dir)} rev-parse HEAD)\" = {shlex.quote(artifact.git_commit)}",
        f"aws --region {shlex.quote(args.region)} s3 cp {shlex.quote(s3_uri)} {shlex.quote(staged)} --only-show-errors",
        f"echo {shlex.quote(artifact.sha256 + '  ' + staged)} | sha256sum -c -",
        _remote_verify_python(manifest_b64, staged),
        f"if [ -f {shlex.quote(db_path)} ]; then {shlex.quote(args.app_dir + '/.venv/bin/python')} - <<'PY'\nimport sqlite3\nsrc=sqlite3.connect({db_path!r})\ndst=sqlite3.connect({backup_path!r})\ntry:\n src.backup(dst)\n ok=dst.execute('PRAGMA integrity_check').fetchone()[0]\n if str(ok).lower()!='ok': raise SystemExit(f'pre-promotion backup integrity={{ok}}')\nfinally:\n dst.close(); src.close()\nprint('PRE_PROMOTION_BACKUP_OK')\nPY\nfi",
        f"rm -f {shlex.quote(db_path + '-wal')} {shlex.quote(db_path + '-shm')}",
        f"mv {shlex.quote(staged)} {shlex.quote(db_path)}",
        "STAGE_REPLACED=1",
        f"chown ubuntu:ubuntu {shlex.quote(db_path)}",
        f"echo {shlex.quote(manifest_b64)} | base64 -d > {shlex.quote(remote_manifest)}",
        f"chown ubuntu:ubuntu {shlex.quote(remote_manifest)}",
        "systemctl start job-market-map.service",
        "for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8770/v3/health && break; sleep 1; done",
        "curl -fsS http://127.0.0.1:8770/v3/health | python3 -c \"import json,sys; d=json.load(sys.stdin); assert d['ok'] and d['schema_version']==8; print('JMM_HEALTH_OK',d)\"",
        "curl -fsS http://127.0.0.1:8770/v3/admin/service/status | python3 -c \"import json,sys; d=json.load(sys.stdin); assert d['scheduler']['enabled'] is False; assert d['collection']['active'] is False; print('JMM_STAGE_SCHEDULER_OFF')\"",
        f"sudo -u ubuntu env --chdir={shlex.quote(args.jh_dir)} JOB_HUNTER_MARKET_MAP_BASE_URL={shlex.quote(DEFAULT_JMM_URL)} {shlex.quote(args.jh_dir + '/.venv/bin/python')} -c \"from job_hunter_agent.job_market_map_client import JobMarketMapClient; c=JobMarketMapClient.from_environment(); p=c.feed_page(after_id=0,limit=1); assert p['items']; print('JH_CLIENT_READ_OK', p['items'][0]['id'], p['items'][0]['source'])\"",
        "systemctl stop job-market-map.service",
        "systemctl stop job-market-map-browser.service",
        "test \"$(systemctl is-active job-market-map.service 2>/dev/null || true)\" = inactive",
        "test \"$(systemctl is-active job-market-map-browser.service 2>/dev/null || true)\" = inactive",
        "! ss -ltn | grep -q ':8770'",
        "! ss -ltn | grep -q ':9223'",
        "! grep -q '^JOB_HUNTER_MARKET_MAP_BASE_URL=' /etc/job-hunter/job-hunter.env",
        "trap - ERR",
        "echo JMM_STAGE_COMPLETE_AWS_REMAINS_OFF",
    ]
    try:
        _aws(
            args.region,
            "s3",
            "cp",
            str(snapshot),
            s3_uri,
            "--sse",
            "AES256",
            "--only-show-errors",
        )
        encryption = _aws(
            args.region,
            "s3api",
            "head-object",
            "--bucket",
            args.bucket,
            "--key",
            s3_key,
            "--query",
            "ServerSideEncryption",
            "--output",
            "text",
        ).stdout.strip()
        if encryption != "AES256":
            raise RuntimeError(f"temporary transfer object is not AES256 encrypted: {encryption!r}")
        result = _send_ssm(
            instance_id=args.instance_id,
            region=args.region,
            comment=f"Stage JMM promotion {artifact.promotion_id}",
            commands=commands,
            timeout=args.timeout,
        )
        print(result.get("StandardOutputContent", "").rstrip())
    finally:
        cleanup = _aws(
            args.region,
            "s3",
            "rm",
            s3_uri,
            "--only-show-errors",
            check=False,
        )
        if cleanup.returncode != 0:
            print(
                f"WARNING: temporary S3 object cleanup failed: {cleanup.stderr.strip()}",
                file=sys.stderr,
            )
        else:
            print("Temporary S3 transfer object deleted.")
    print("Stage complete. AWS JMM remains disabled/off. Do not run go-live until approved.")
    return 0


def go_live(args: argparse.Namespace) -> int:
    if args.confirm != "GO-LIVE-JMM":
        raise SystemExit("Refusing go-live: pass --confirm GO-LIVE-JMM explicitly")
    assert_local_cutover_safe()
    artifact = load_artifact(Path(args.manifest).resolve())
    manifest_b64 = base64.b64encode(json.dumps(asdict(artifact)).encode()).decode()
    remote_manifest = f"{args.persist_root}/promotion/staged-manifest.json"
    db_path = f"{args.persist_root}/data/market.db"
    env_file = "/etc/job-hunter/job-hunter.env"
    env_backup = f"/etc/job-hunter/job-hunter.env.pre-jmm-go-live-{artifact.promotion_id}"
    verify_current = _remote_verify_python(manifest_b64, db_path)
    scheduler_off = (
        f"sudo -u ubuntu env PYTHONPATH={shlex.quote(args.app_dir)} "
        f"{shlex.quote(args.app_dir + '/.venv/bin/python')} -c "
        + shlex.quote(
            'from collector.settings import set_setting; '
            'set_setting("scheduler.enabled", False, actor="aws-go-live-rollback")'
        )
    )
    go_live_rollback = (
        "go_live_rollback() { rc=$?; trap - ERR; "
        "systemctl disable --now job-market-map.service job-market-map-browser.service >/dev/null 2>&1 || true; "
        f"{scheduler_off} >/dev/null 2>&1 || true; "
        f"if [ -f {shlex.quote(env_backup)} ]; then cp -a {shlex.quote(env_backup)} {shlex.quote(env_file)}; "
        "systemctl restart job-hunter.service >/dev/null 2>&1 || true; fi; "
        "echo JMM_GO_LIVE_ROLLED_BACK >&2; exit $rc; }; trap go_live_rollback ERR"
    )
    scheduler_on = (
        f"sudo -u ubuntu env PYTHONPATH={shlex.quote(args.app_dir)} "
        f"{shlex.quote(args.app_dir + '/.venv/bin/python')} -c "
        + shlex.quote(
            'from collector.settings import set_setting; '
            'set_setting("scheduler.enabled", True, actor="aws-go-live")'
        )
    )
    commands = [
        "#!/bin/bash",
        "set -euo pipefail",
        go_live_rollback,
        "if systemctl is-active --quiet job-market-map.service || systemctl is-active --quiet job-market-map-browser.service; then echo 'JMM must be off before go-live' >&2; exit 30; fi",
        "if grep -q '^JOB_HUNTER_MARKET_MAP_BASE_URL=' /etc/job-hunter/job-hunter.env; then echo 'JH already configured for JMM before go-live' >&2; exit 31; fi",
        f"test -f {shlex.quote(remote_manifest)}",
        f"python3 - <<'PY'\nimport base64,json\nlocal=json.loads(base64.b64decode({manifest_b64!r}).decode())\nremote=json.load(open({remote_manifest!r}))\nassert remote['promotion_id']==local['promotion_id'], (remote['promotion_id'],local['promotion_id'])\nprint('PROMOTION_ID_OK',local['promotion_id'])\nPY",
        f"test \"$(sudo -u ubuntu git -C {shlex.quote(args.app_dir)} rev-parse HEAD)\" = {shlex.quote(artifact.git_commit)}",
        verify_current,
        f"cp -a {shlex.quote(env_file)} {shlex.quote(env_backup)}",
        scheduler_on,
        "systemctl enable --now job-market-map-browser.service job-market-map.service",
        "sleep 2",
        "test \"$(systemctl is-active job-market-map.service)\" = active",
        "test \"$(systemctl is-active job-market-map-browser.service)\" = active",
        "ss -ltn | grep -q ':9223'",
        "for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8770/v3/health >/dev/null && break; sleep 1; done",
        "curl -fsS http://127.0.0.1:8770/v3/admin/service/status | python3 -c \"import json,sys; d=json.load(sys.stdin); assert d['scheduler']['enabled'] is True; print('JMM_SCHEDULER_ON')\"",
        f"sed -i '/^JOB_HUNTER_MARKET_MAP_BASE_URL=/d' {shlex.quote(env_file)}",
        f"printf '%s\\n' {shlex.quote('JOB_HUNTER_MARKET_MAP_BASE_URL=' + DEFAULT_JMM_URL)} >> {shlex.quote(env_file)}",
        "systemctl restart job-hunter.service",
        "sleep 3",
        "test \"$(systemctl is-active job-hunter.service)\" = active",
        "curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/start | grep -Eq '^(200|302)$'",
        f"sudo -u ubuntu env --chdir={shlex.quote(args.jh_dir)} JOB_HUNTER_MARKET_MAP_BASE_URL={shlex.quote(DEFAULT_JMM_URL)} {shlex.quote(args.jh_dir + '/.venv/bin/python')} -c \"from job_hunter_agent.job_market_map_client import JobMarketMapClient; c=JobMarketMapClient.from_environment(); p=c.feed_page(after_id=0,limit=1); assert p['items']; print('JH_TO_JMM_GO_LIVE_OK',p['items'][0]['id'])\"",
        "trap - ERR",
        "echo JMM_GO_LIVE_COMPLETE",
    ]
    result = _send_ssm(
        instance_id=args.instance_id,
        region=args.region,
        comment=f"Go live JMM promotion {artifact.promotion_id}",
        commands=commands,
        timeout=args.timeout,
    )
    print(result.get("StandardOutputContent", "").rstrip())
    return 0


def status(args: argparse.Namespace) -> int:
    commands = [
        "echo api_active=$(systemctl is-active job-market-map.service 2>/dev/null || true)",
        "echo browser_active=$(systemctl is-active job-market-map-browser.service 2>/dev/null || true)",
        "echo api_enabled=$(systemctl is-enabled job-market-map.service 2>/dev/null || true)",
        "echo browser_enabled=$(systemctl is-enabled job-market-map-browser.service 2>/dev/null || true)",
        f"test -f {shlex.quote(args.persist_root + '/promotion/staged-manifest.json')} && cat {shlex.quote(args.persist_root + '/promotion/staged-manifest.json')} || true",
    ]
    result = _send_ssm(
        instance_id=args.instance_id,
        region=args.region,
        comment="JMM promotion status",
        commands=commands,
        timeout=60,
    )
    print(result.get("StandardOutputContent", "").rstrip())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage and explicitly promote the authoritative local JMM database to AWS."
    )
    parser.add_argument(
        "--instance-id",
        default=os.environ.get("JMM_AWS_INSTANCE_ID"),
        help="Target EC2 instance id (or JMM_AWS_INSTANCE_ID).",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", DEFAULT_REGION))
    parser.add_argument("--app-dir", default=DEFAULT_APP_DIR)
    parser.add_argument("--persist-root", default=DEFAULT_PERSIST_ROOT)
    parser.add_argument("--jh-dir", default=DEFAULT_JH_DIR)
    parser.add_argument("--timeout", type=int, default=300)
    sub = parser.add_subparsers(dest="command", required=True)

    stage_parser = sub.add_parser("stage", help="Copy/verify DB on AWS, smoke-test API, then leave JMM off.")
    stage_parser.add_argument("--bucket", default=os.environ.get("JMM_AWS_TRANSFER_BUCKET"))
    stage_parser.add_argument("--prefix", default="jmm-promotion")
    stage_parser.set_defaults(func=stage)

    live_parser = sub.add_parser("go-live", help="Explicitly enable a previously staged promotion.")
    live_parser.add_argument("--manifest", required=True)
    live_parser.add_argument("--confirm", default="")
    live_parser.set_defaults(func=go_live)

    status_parser = sub.add_parser("status", help="Read AWS JMM staged/live state without changing it.")
    status_parser.set_defaults(func=status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.instance_id:
        parser.error("--instance-id or JMM_AWS_INSTANCE_ID is required")
    if args.command == "stage" and not args.bucket:
        parser.error("stage requires --bucket or JMM_AWS_TRANSFER_BUCKET")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
