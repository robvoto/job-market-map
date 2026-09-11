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
