from collector.linkedin_detail import (
    classify_linkedin_apply_method,
    parse_linkedin_detail_html,
)


def _html(header: str, *, apply_code: str = "") -> str:
    description = " ".join(["Detailed role description with source-backed responsibilities."] * 5)
    return f"""
    <html><body>
      <div class="top-card-layout__second-subline">{header}</div>
      {apply_code}
      <div class="show-more-less-html__markup"><p>{description}</p></div>
    </body></html>
    """


def test_linkedin_detail_extracts_repost_external_apply_and_exact_applicant_count():
    detail = parse_linkedin_detail_html(
        _html(
            'Sydney · <time datetime="2026-09-15">2 days ago</time> · Reposted · 187 applicants',
            apply_code='<code id="applyUrl">?url=https%3A%2F%2Fexample.com%2Fapply&amp;x=1</code>',
        ),
        canonical_url="https://www.linkedin.com/jobs/view/1234567890",
    )
    assert detail.reposted is True
    assert detail.applicant_count == 187
    assert detail.apply_method == "external_apply"
    assert detail.easy_apply is False
    assert detail.apply_url == "https://example.com/apply"
    assert detail.posted_at == "2026-09-15"
    assert detail.facts["posted_at"] == "2026-09-15"
    assert detail.full_description


def test_linkedin_detail_keeps_threshold_applicant_language_unknown():
    first = parse_linkedin_detail_html(
        _html("2 days ago · Be among the first 25 applicants"),
        canonical_url="https://www.linkedin.com/jobs/view/1234567890",
    )
    over = parse_linkedin_detail_html(
        _html("2 days ago · Over 100 applicants"),
        canonical_url="https://www.linkedin.com/jobs/view/1234567890",
    )
    assert first.applicant_count is None
    assert over.applicant_count is None


def test_linkedin_detail_detects_closed_and_easy_apply_without_external_url():
    detail = parse_linkedin_detail_html(
        _html("No longer accepting applications · 30 applicants"),
        canonical_url="https://www.linkedin.com/jobs/view/1234567890",
    )
    assert detail.source_status == "no_longer_accepting_applications"
    assert detail.applicant_count == 30
    assert detail.apply_method == "easy_apply"
    assert detail.easy_apply is True


def test_apply_classifier_matches_job_hunter_rule():
    canonical = "https://www.linkedin.com/jobs/view/1234567890"
    assert classify_linkedin_apply_method(None, canonical) == "easy_apply"
    assert classify_linkedin_apply_method("https://ats.example/123", canonical) == "external_apply"


def _detail_html() -> bytes:
    description = " ".join(["Detailed source-backed LinkedIn role description."] * 8)
    return (
        '<html><body><div class="top-card-layout__second-subline">Sydney · 4 hours ago</div>'
        f'<div class="show-more-less-html__markup"><p>{description}</p></div>'
        '</body></html>'
    ).encode()


class _FakeResponse:
    def __init__(self, body: bytes, url: str):
        self._body = body
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self._url

    def read(self):
        return self._body


def test_linkedin_detail_client_uses_cookie_jar_session():
    from urllib.request import HTTPCookieProcessor
    from collector import linkedin_detail

    assert any(
        isinstance(handler, HTTPCookieProcessor)
        for handler in linkedin_detail._LINKEDIN_OPENER.handlers
    )


def test_linkedin_detail_spaces_requests(monkeypatch):
    from collector import linkedin_detail

    now = [100.0]
    sleeps: list[float] = []
    opens: list[str] = []

    class FakeOpener:
        def open(self, request, timeout):
            opens.append(request.full_url)
            return _FakeResponse(_detail_html(), request.full_url)

    def fake_setting(key):
        return {
            "collection.linkedin_detail_timeout_seconds": 20,
            "collection.linkedin_detail_min_interval_seconds": 1.0,
            "collection.linkedin_detail_rate_limit_cooldown_seconds": 60.0,
        }[key]

    monkeypatch.setattr(linkedin_detail, "get_setting", fake_setting)
    monkeypatch.setattr(linkedin_detail, "_LINKEDIN_OPENER", FakeOpener())
    monkeypatch.setattr(linkedin_detail.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        linkedin_detail.time,
        "sleep",
        lambda seconds: (sleeps.append(seconds), now.__setitem__(0, now[0] + seconds)),
    )
    monkeypatch.setattr(linkedin_detail, "_LINKEDIN_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(linkedin_detail, "_LINKEDIN_RATE_LIMIT_UNTIL", 0.0)

    linkedin_detail.fetch_linkedin_detail("https://www.linkedin.com/jobs/view/1")
    linkedin_detail.fetch_linkedin_detail("https://www.linkedin.com/jobs/view/2")

    assert len(opens) == 2
    assert sleeps == [1.0]


def test_linkedin_detail_429_activates_cooldown_without_rehitting_source(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError
    from collector import linkedin_detail

    now = [100.0]
    calls = [0]

    class FakeOpener:
        def open(self, request, timeout):
            calls[0] += 1
            if calls[0] == 1:
                raise HTTPError(request.full_url, 429, "Too Many Requests", {}, BytesIO(b"rate limited"))
            return _FakeResponse(_detail_html(), request.full_url)

    def fake_setting(key):
        return {
            "collection.linkedin_detail_timeout_seconds": 20,
            "collection.linkedin_detail_min_interval_seconds": 0.0,
            "collection.linkedin_detail_rate_limit_cooldown_seconds": 60.0,
        }[key]

    monkeypatch.setattr(linkedin_detail, "get_setting", fake_setting)
    monkeypatch.setattr(linkedin_detail, "_LINKEDIN_OPENER", FakeOpener())
    monkeypatch.setattr(linkedin_detail.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(linkedin_detail, "_LINKEDIN_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(linkedin_detail, "_LINKEDIN_RATE_LIMIT_UNTIL", 0.0)

    import pytest

    with pytest.raises(linkedin_detail.LinkedInDetailError, match="HTTP 429"):
        linkedin_detail.fetch_linkedin_detail("https://www.linkedin.com/jobs/view/1")
    assert calls[0] == 1

    with pytest.raises(linkedin_detail.LinkedInDetailError, match="cooldown active"):
        linkedin_detail.fetch_linkedin_detail("https://www.linkedin.com/jobs/view/2")
    assert calls[0] == 1

    now[0] = 160.0
    detail = linkedin_detail.fetch_linkedin_detail("https://www.linkedin.com/jobs/view/2")
    assert detail.full_description
    assert calls[0] == 2
