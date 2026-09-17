from collector.linkedin_detail import LinkedInDetailEvidence


def test_linkedin_adapter_reuses_http_detail_helper_and_never_browser(monkeypatch):
    from collector import linkedin_jd_adapter

    calls = []

    def fetch(url):
        calls.append(url)
        return LinkedInDetailEvidence(
            full_description="Validated LinkedIn description " * 8,
            apply_url="https://ats.example/apply/123",
            apply_method="external_apply",
            easy_apply=False,
            reposted=True,
            source_status=None,
            applicant_count=12,
            header_text="Reposted 1 day ago · 12 applicants",
        )

    monkeypatch.setattr(linkedin_jd_adapter, "fetch_linkedin_detail", fetch)
    result = linkedin_jd_adapter.fetch_linkedin_jd_for_job(
        {
            "id": 7,
            "source_job_id": "li-1234567890",
            "canonical_url": "https://www.linkedin.com/jobs/view/1234567890",
        }
    )
    assert result.jd_source == "linkedin_public_job_page"
    assert result.facts["apply_method"] == "external_apply"
    assert result.facts["applicant_count"] == 12
    assert result.facts["reposted"] is True
    assert calls == ["https://www.linkedin.com/jobs/view/1234567890"]


def test_linkedin_adapter_marks_closed_posting_terminal(monkeypatch):
    import pytest
    from collector import linkedin_jd_adapter
    from collector.jd_enrichment import JDSourcePostingUnavailableError

    monkeypatch.setattr(
        linkedin_jd_adapter,
        "fetch_linkedin_detail",
        lambda _url: LinkedInDetailEvidence(
            full_description="Still visible description " * 8,
            apply_url=None,
            apply_method=None,
            easy_apply=None,
            reposted=False,
            source_status="no_longer_accepting_applications",
            applicant_count=None,
            header_text="No longer accepting applications",
        ),
    )
    with pytest.raises(JDSourcePostingUnavailableError) as exc_info:
        linkedin_jd_adapter.fetch_linkedin_jd_for_job(
            {"canonical_url": "https://www.linkedin.com/jobs/view/1"}
        )
    assert exc_info.value.source_status == "no_longer_accepting_applications"
