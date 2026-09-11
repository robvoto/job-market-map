from pathlib import Path


def test_supported_runtime_entrypoints_are_single_instance_guarded():
    api = Path("scripts/start-api.sh").read_text(encoding="utf-8")
    browser = Path("scripts/run_browser_service.sh").read_text(encoding="utf-8")
    collector = Path("scripts/run_collection_cycle.py").read_text(encoding="utf-8")

    assert "data/api-service.lock" in api
    assert "flock -n 9" in api
    assert "browser-service.lock" in browser
    assert "flock -n 8" in browser
    assert "collection_run_lock(args.trigger)" in collector
