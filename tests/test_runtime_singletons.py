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


def test_service_start_is_disabled_for_local_background_runtime():
    service = Path("scripts/service.sh").read_text(encoding="utf-8")

    assert "JOB_MARKET_MAP_BACKGROUND_START_DISABLED" in service
    assert "run_JMM.ps1" in service


def test_jmm_service_owns_browser_lifecycle():
    start_api = Path("scripts/start-api.sh").read_text(encoding="utf-8")
    browser = Path("scripts/run_browser_service.sh").read_text(encoding="utf-8")
    service = Path("scripts/service.sh").read_text(encoding="utf-8")
    stop_browser = Path("scripts/stop_browser_service.sh").read_text(encoding="utf-8")

    assert 'JMM_BROWSER_OWNER_PID="$$"' in start_api
    assert 'OWNER_PID="${JMM_BROWSER_OWNER_PID:-}"' in browser
    assert 'owner_active()' in browser
    assert 'JMM browser owner exited; stopping Chromium.' in browser
    assert 'scripts/stop_browser_service.sh' in service
    assert '--user-data-dir=$PROFILE' in stop_browser
    assert 'scripts/run_browser_service.sh' in stop_browser


def test_local_jmm_cannot_start_hidden_or_bootstrap_browser_from_collection():
    service = Path("scripts/service.sh").read_text(encoding="utf-8")
    start_api = Path("scripts/start-api.sh").read_text(encoding="utf-8")
    collection = Path("scripts/start_collection_service.sh").read_text(encoding="utf-8")

    assert "JOB_MARKET_MAP_BACKGROUND_START_DISABLED" in service
    assert "JOB_MARKET_MAP_VISIBLE_TERMINAL_REQUIRED" in start_api
    assert "[[ ! -t 1 || ! -t 2 ]]" in start_api
    assert "JMM_VISIBLE_SERVICE_REQUIRED" in collection
    assert 'scripts/start_browser_service.sh' not in collection
