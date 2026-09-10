from pathlib import Path


def test_admin_page_exposes_helper_driven_settings_query_and_retention_controls():
    html = Path("api/admin.html").read_text(encoding="utf-8")
    assert "const api='/v1'" in html
    assert "api+'/admin/settings'" in html
    assert "help_text" in html
    assert "/admin/queries/" in html
    assert "run-retention" in html
    assert "add-query" in html
