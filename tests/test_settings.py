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
        settings.set_setting("collection.linkedin_detail_timeout_seconds", 0)
    with pytest.raises(settings.SettingError, match="greater than archive"):
        settings.set_setting("retention.remove_archived_after_days", 30)
    with pytest.raises(KeyError):
        settings.set_setting("does.not.exist", 1)
    with pytest.raises(settings.SettingError, match="window_hours must be greater"):
        settings.set_setting("collection.linkedin_window_hours", 4)
    with pytest.raises(settings.SettingError, match="interval_hours must be less"):
        settings.set_setting("scheduler.linkedin_interval_hours", 5)


def test_hot_get_setting_does_not_reseed_existing_catalog(tmp_path, monkeypatch):
    settings = _wire(tmp_path, monkeypatch)
    settings.seed_settings()
    calls = {"count": 0}
    original = settings.seed_settings

    def counted_seed():
        calls["count"] += 1
        return original()

    monkeypatch.setattr(settings, "seed_settings", counted_seed)
    assert settings.get_setting("collection.default_freshness_days") == 1
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


def test_seed_removes_obsolete_linkedin_browser_settings(tmp_path, monkeypatch):
    settings = _wire(tmp_path, monkeypatch)
    settings.seed_settings()
    with db.connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO settings(
                key,category,value_type,value_json,default_json,help_text,updated_at
            ) VALUES('collection.linkedin_chunk_offsets','Collection','integer','8','8','obsolete','x')"""
        )
    settings.seed_settings()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM settings WHERE key='collection.linkedin_chunk_offsets'"
        ).fetchone()
    assert row is None


def test_seed_removes_obsolete_linkedin_keyword_campaign_settings(tmp_path, monkeypatch):
    settings = _wire(tmp_path, monkeypatch)
    settings.seed_settings()
    with db.connect() as conn:
        for key in (
            "collection.linkedin_results_per_query",
            "collection.linkedin_max_consecutive_query_failures",
        ):
            conn.execute(
                """INSERT OR REPLACE INTO settings(
                    key,category,value_type,value_json,default_json,help_text,updated_at
                ) VALUES(?, 'Collection', 'integer', '25', '25', 'obsolete', 'x')""",
                (key,),
            )
    settings.seed_settings()
    with db.connect() as conn:
        for key in (
            "collection.linkedin_results_per_query",
            "collection.linkedin_max_consecutive_query_failures",
        ):
            assert conn.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone() is None


def test_scheduler_and_backup_settings_have_safe_defaults(tmp_path, monkeypatch):
    settings = _wire(tmp_path, monkeypatch)
    settings.seed_settings()
    assert settings.get_setting("scheduler.enabled") is True
    assert settings.get_setting("scheduler.daily_hour") == 2
    assert settings.get_setting("scheduler.daily_minute") == 0
    assert settings.get_setting("scheduler.linkedin_interval_hours") == 4
    assert settings.get_setting("collection.linkedin_window_hours") == 5
    assert settings.get_setting("collection.seek_incremental_overlap_minutes") == 120
    assert settings.get_setting("backup.before_collection_enabled") is True
    assert settings.get_setting("backup.keep_count") == 14
