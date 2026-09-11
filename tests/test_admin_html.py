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
    assert "SEEK uses whole-state partition coverage instead" in html


def test_admin_page_exposes_geography_and_seek_coverage_controls():
    html = Path("api/admin.html").read_text(encoding="utf-8")
    assert "/admin/geographies" in html
    assert "/coverage/seek" in html
    assert "Market scope" in html


def test_admin_page_exposes_collection_scheduler_and_backup_controls():
    html = Path("api/admin.html").read_text(encoding="utf-8")
    for token in (
        "run-now",
        "run-linkedin-now",
        "stop-collection",
        "pause-schedule",
        "resume-schedule",
        "backup-now",
        "schedule-time",
        "/admin/service/status",
        "/admin/collection/run",
        "/admin/linkedin/run",
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
    assert "Latest daily run" in html
    assert "Current market" in html
    assert "APSJobs" in html
    assert "Not run yet" in html
    assert "Accepted with small gap" in html
    assert "Saved geography progress. The next LinkedIn run resumes it." in html
    assert "/admin/stats" in html
    assert "JD attempts" in html
    assert "partitions" in html
    assert "LinkedIn geography cycle" in html
    assert "LinkedIn cards" in html
    assert "geographies done" in html
    assert "Cards-only discovery · no Chromium · no JD fetches." in html
    assert "await r.text()" in html
    assert "Stats unavailable" in html
    assert "Incremental SEEK window" in html
    assert "Incremental extra run" in html


def test_admin_daily_status_does_not_present_accepted_bootstrap_as_current_failure():
    html = Path("api/admin.html").read_text(encoding="utf-8")
    assert "bootstrapOnly=last.run_kind==='bootstrap'" in html
    assert "Accepted 3-day bootstrap is shown in Collection stats" in html
    assert "Last daily SEEK collection:" in html
    assert "LinkedIn cards-only geography refreshes every 4 hours" in html
    assert "daily whole-state SEEK run" in html
