import pytest

from collector import db


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import settings

    monkeypatch.setattr(settings, "connect", db.connect)
    monkeypatch.setattr(settings, "init_db", db.init_db)
    return settings


def test_settings_seed_with_helper_text_and_can_change_without_code(
    tmp_path, monkeypatch
):
    settings = _wire(tmp_path, monkeypatch)
    rows = settings.list_settings()
    assert len(rows) >= 16
    retention = next(
        row for row in rows if row["key"] == "retention.archive_after_days"
    )
    assert retention["value"] == 30
    assert retention["help_text"]
    # Guardrail: age thresholds alone must never authorize destructive cleanup.
    assert settings.get_setting("retention.prune_raw_captures_enabled") is False
    assert settings.get_setting("retention.archive_jobs_enabled") is False
    assert settings.get_setting("retention.remove_archived_jobs_enabled") is False
    changed = settings.set_setting("retention.archive_after_days", 45, actor="rob")
    assert changed["value"] == 45
    assert changed["updated_by"] == "rob"


def test_setting_validation_and_cross_setting_order(tmp_path, monkeypatch):
    settings = _wire(tmp_path, monkeypatch)
    with pytest.raises(settings.SettingError):
        settings.set_setting("collection.linkedin_chunk_offsets", 0)
    with pytest.raises(settings.SettingError, match="greater than archive"):
        settings.set_setting("retention.remove_archived_after_days", 30)
    with pytest.raises(KeyError):
        settings.set_setting("does.not.exist", 1)


def test_hot_get_setting_does_not_reseed_existing_catalog(tmp_path, monkeypatch):
    settings = _wire(tmp_path, monkeypatch)
    settings.seed_settings()
    calls = {"count": 0}
    original = settings.seed_settings

    def counted_seed():
        calls["count"] += 1
        return original()

    monkeypatch.setattr(settings, "seed_settings", counted_seed)
    assert settings.get_setting("collection.default_freshness_days") == 7
    assert calls["count"] == 0


def test_seed_removes_obsolete_personal_activity_settings(tmp_path, monkeypatch):
    settings = _wire(tmp_path, monkeypatch)
    settings.seed_settings()
    with db.connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO settings(
                key,category,value_type,value_json,default_json,help_text,updated_at
            ) VALUES('retention.preserve_activity_jobs_forever','Retention','boolean','true','true','obsolete','x')"""
        )
    settings.seed_settings()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM settings WHERE key='retention.preserve_activity_jobs_forever'"
        ).fetchone()
    assert row is None
