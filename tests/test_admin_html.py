from pathlib import Path


def test_admin_page_exposes_helper_driven_settings_query_and_retention_controls():
    html = Path("api/admin.html").read_text(encoding="utf-8")
    assert "const api='/v3'" in html
    assert "api+'/admin/settings'" in html
    assert "help_text" in html
    assert "/admin/queries/" in html
    assert "run-retention" in html
    assert "add-query" in html
    assert "Optional source keyword queries (advanced)" in html
    assert "Normal SEEK daily coverage uses whole-state partitioning" in html


def test_admin_page_exposes_geography_and_seek_coverage_controls():
    html = Path("api/admin.html").read_text(encoding="utf-8")
    assert "/admin/geographies" in html
    assert "/coverage/seek" in html
    assert "Market scope" in html


def test_admin_page_exposes_collection_scheduler_and_backup_controls():
    html = Path("api/admin.html").read_text(encoding="utf-8")
    for token in (
        "run-now",
        "stop-collection",
        "pause-schedule",
        "resume-schedule",
        "backup-now",
        "schedule-time",
        "/admin/service/status",
        "/admin/collection/run",
        "/admin/collection/stop",
        "/admin/backup/run",
        "/admin/browser/seek",
        "/admin/log",
        "fmtTime",
        "dashboard",
        "Waiting for next run",
    ):
        assert token in html
    assert "data/market.db" in html
    assert "Open SEEK login browser" in html
    assert "SEEK sign-in is not verified here" in html
    assert "clearStatusError" in html
    assert "setInterval" in html


def test_admin_page_exposes_side_stats_for_bootstrap_and_latest_run():
    html = Path("api/admin.html").read_text(encoding="utf-8")
    assert "stats-sidebar" in html
    assert "3-day bootstrap" in html
    assert "Latest run" in html
    assert "Current market" in html
    assert "/admin/stats" in html
    assert "JD attempts" in html
    assert "partitions" in html
