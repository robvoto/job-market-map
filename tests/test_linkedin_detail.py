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
            "Sydney · Reposted 2 days ago · 187 applicants",
            apply_code='<code id="applyUrl">?url=https%3A%2F%2Fexample.com%2Fapply&amp;x=1</code>',
        ),
        canonical_url="https://www.linkedin.com/jobs/view/1234567890",
    )
    assert detail.reposted is True
    assert detail.applicant_count == 187
    assert detail.apply_method == "external_apply"
    assert detail.easy_apply is False
    assert detail.apply_url == "https://example.com/apply"
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
