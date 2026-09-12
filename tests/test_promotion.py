import json
from pathlib import Path

import pytest

from collector import db, promotion
from scripts import promote_to_aws


def _wire_db(tmp_path, monkeypatch):
    path = tmp_path / "market.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(promotion.db, "DB_PATH", path)
    db.init_db()
    return path


def test_database_summary_is_integrity_checked_and_counts_market_state(tmp_path, monkeypatch):
    path = _wire_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        seek_id = conn.execute(
            "INSERT INTO jobs(source,source_job_id,identity_key,canonical_url,title,employer,full_description) VALUES('seek','s1','seek:s1','https://seek.test/1','Role','Acme','JD')"
        ).lastrowid
        linkedin_id = conn.execute(
            "INSERT INTO jobs(source,source_job_id,identity_key,canonical_url,title,employer) VALUES('linkedin','l1','linkedin:l1','https://linkedin.test/1','Role','Acme')"
        ).lastrowid
        conn.execute(
            "INSERT INTO jd_fetch_registry(identity_key,source,source_job_id,canonical_url,fetched_at,jd_source) VALUES('seek:s1','seek','s1','https://seek.test/1','2026-09-12T00:00:00+00:00','seek_job_page')"
        )
        conn.execute(
            "INSERT INTO same_vacancy_links(job_id,primary_job_id,confidence,match_type,matching_signals_json,detected_at) VALUES(?,?,?,?,?,?)",
            (linkedin_id, seek_id, 0.96, "same_vacancy", "[]", "2026-09-12T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO consumer_checkpoints(consumer_key,last_job_id,updated_at) VALUES('jh:test',0,'2026-09-12T00:00:00+00:00')"
        )
    summary = promotion.database_summary(path)
    assert summary["integrity"] == "ok"
    assert summary["jobs_total"] == 2
    assert summary["seek_jobs"] == 1
    assert summary["linkedin_jobs"] == 1
    assert summary["jobs_with_jd"] == 1
    assert summary["jd_fetch_registry"] == 1
    assert summary["same_vacancy_links"] == 1
    assert summary["consumer_checkpoints"] == 1


def test_local_cutover_preflight_refuses_active_collection(monkeypatch):
    monkeypatch.setattr(promotion, "lock_status", lambda: {"active": True, "metadata": {"pid": 7}})
    monkeypatch.setattr(promotion, "get_setting", lambda _key: False)
    with pytest.raises(RuntimeError, match="collection is active"):
        promotion.assert_local_cutover_safe()


def test_local_cutover_preflight_refuses_enabled_scheduler(monkeypatch):
    monkeypatch.setattr(promotion, "lock_status", lambda: {"active": False})
    monkeypatch.setattr(promotion, "get_setting", lambda _key: True)
    with pytest.raises(RuntimeError, match="scheduler must be disabled"):
        promotion.assert_local_cutover_safe()


def test_go_live_requires_explicit_confirmation(tmp_path):
    parser = promote_to_aws.build_parser()
    args = parser.parse_args(
        ["--instance-id", "i-test", "go-live", "--manifest", str(tmp_path / "manifest.json")]
    )
    with pytest.raises(SystemExit, match="GO-LIVE-JMM"):
        promote_to_aws.go_live(args)


def test_ec2_installer_does_not_enable_services_by_default():
    text = Path("scripts/ec2/install-job-market-map.sh").read_text(encoding="utf-8")
    assert 'JMM_ENABLE_SERVICES:-0' in text
    assert 'scripts/promote_to_aws.py go-live' in text
    assert "systemctl enable job-market-map-browser.service job-market-map.service" in text
    conditional = text.index('if [[ "${JMM_ENABLE_SERVICES:-0}" == "1" ]]')
    enable = text.index("systemctl enable job-market-map-browser.service job-market-map.service")
    assert conditional < enable


def test_manifest_loader_rejects_wrong_schema(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "promotion_id": "x",
                "snapshot_path": "/tmp/x.db",
                "manifest_path": str(path),
                "sha256": "abc",
                "size_bytes": 1,
                "git_commit": "deadbeef",
                "schema_version": 999,
                "created_at": "2026-09-12T00:00:00+00:00",
                "database_summary": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="schema version"):
        promotion.load_artifact(path)


def test_promotion_schema_version_matches_api_contract():
    from api.main import SCHEMA_VERSION as api_schema_version

    assert promotion.SCHEMA_VERSION == api_schema_version


def test_promotion_script_has_fail_closed_stage_and_go_live_rollbacks():
    text = Path("scripts/promote_to_aws.py").read_text(encoding="utf-8")
    assert "stage_rollback()" in text
    assert "STAGE_ROLLBACK_RESTORED_PREVIOUS_AWS_DB" in text
    assert "go_live_rollback()" in text
    assert "JMM_GO_LIVE_ROLLED_BACK" in text
    assert "ServerSideEncryption" in text


def _artifact_for_shell_test(tmp_path):
    snapshot = tmp_path / "market.db"
    snapshot.write_bytes(b"test")
    manifest = tmp_path / "manifest.json"
    artifact = promotion.PromotionArtifact(
        promotion_id="20260912T000000Z-abc123",
        snapshot_path=str(snapshot),
        manifest_path=str(manifest),
        sha256="a" * 64,
        size_bytes=4,
        git_commit="b" * 40,
        schema_version=promotion.SCHEMA_VERSION,
        created_at="2026-09-12T00:00:00+00:00",
        database_summary={
            "integrity": "ok",
            "jobs_total": 2,
            "seek_jobs": 1,
            "linkedin_jobs": 1,
            "jobs_with_jd": 1,
            "jd_fetch_registry": 1,
            "same_vacancy_links": 1,
            "card_captures": 2,
            "consumer_checkpoints": 0,
        },
    )
    manifest.write_text(json.dumps(artifact.__dict__), encoding="utf-8")
    return artifact


def _assert_bash_syntax(commands, tmp_path):
    script = tmp_path / "generated.sh"
    script.write_text("\n".join(commands) + "\n", encoding="utf-8")
    import subprocess

    subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True, text=True)


def test_stage_generated_ssm_commands_are_valid_bash(tmp_path, monkeypatch):
    artifact = _artifact_for_shell_test(tmp_path)
    captured = {}

    monkeypatch.setattr(promote_to_aws, "create_promotion_artifact", lambda: artifact)

    def fake_aws(_region, *args, **_kwargs):
        import subprocess

        output = "AES256\n" if args[:2] == ("s3api", "head-object") else ""
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(promote_to_aws, "_aws", fake_aws)

    def fake_ssm(**kwargs):
        captured["commands"] = kwargs["commands"]
        return {"StandardOutputContent": "STAGED"}

    monkeypatch.setattr(promote_to_aws, "_send_ssm", fake_ssm)
    parser = promote_to_aws.build_parser()
    args = parser.parse_args(
        ["--instance-id", "i-test", "stage", "--bucket", "private-test-bucket"]
    )
    assert promote_to_aws.stage(args) == 0
    _assert_bash_syntax(captured["commands"], tmp_path)


def test_go_live_generated_ssm_commands_are_valid_bash(tmp_path, monkeypatch):
    artifact = _artifact_for_shell_test(tmp_path)
    captured = {}
    monkeypatch.setattr(promote_to_aws, "assert_local_cutover_safe", lambda: None)
    monkeypatch.setattr(promote_to_aws, "load_artifact", lambda _path: artifact)

    def fake_ssm(**kwargs):
        captured["commands"] = kwargs["commands"]
        return {"StandardOutputContent": "LIVE"}

    monkeypatch.setattr(promote_to_aws, "_send_ssm", fake_ssm)
    parser = promote_to_aws.build_parser()
    args = parser.parse_args(
        [
            "--instance-id",
            "i-test",
            "go-live",
            "--manifest",
            artifact.manifest_path,
            "--confirm",
            "GO-LIVE-JMM",
        ]
    )
    assert promote_to_aws.go_live(args) == 0
    _assert_bash_syntax(captured["commands"], tmp_path)
