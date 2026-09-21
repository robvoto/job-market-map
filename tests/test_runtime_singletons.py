from pathlib import Path


def test_supported_runtime_entrypoints_are_single_instance_guarded():
    api = Path("scripts/start-api.sh").read_text(encoding="utf-8")
    browser = Path("scripts/run_browser_service.sh").read_text(encoding="utf-8")
    browser_launcher = Path("scripts/start_browser_service.sh").read_text(
        encoding="utf-8"
    )
    collector = Path("scripts/run_collection_cycle.py").read_text(encoding="utf-8")

    assert "data/api-service.lock" in api
    assert "flock -n 9" in api
    assert "browser-service.lock" in browser
    assert "flock -n 8" in browser
    assert "9>&-" in browser_launcher
    assert 'collection_run_lock(args.trigger, source="seek")' in collector


def test_service_stop_does_not_require_mutating_settings_lookup():
    service = Path("scripts/service.sh").read_text(encoding="utf-8")

    assert "from collector.settings import get_setting" not in service
    assert "mode=ro" in service
    assert "port_from_pid" in service
    assert "find_managed_api_pid" in service
    assert "JOB_MARKET_MAP_SERVICE_RUNNING_UNHEALTHY" in service


def test_service_start_allows_bounded_migration_startup_window():
    service = Path("scripts/service.sh").read_text(encoding="utf-8")

    assert "for _ in $(seq 1 120); do" in service
    assert "sleep 0.25" in service
