from types import SimpleNamespace


def test_seek_adapter_reuses_existing_seek_fetch_and_closes_its_tab(monkeypatch):
    from collector import seek_jd_adapter

    closed = []
    calls = []

    monkeypatch.setattr(
        seek_jd_adapter,
        "open_tab",
        lambda url, active=False: SimpleNamespace(result={"pageId": 91}),
    )
    monkeypatch.setattr(
        seek_jd_adapter, "close_tab", lambda page_id: closed.append(page_id)
    )

    def fetch(page_id, url, *, expected_source_job_id, job_id):
        calls.append((page_id, url, expected_source_job_id, job_id))
        return SimpleNamespace(
            full_description="Validated SEEK description",
            facts={"source_job_id": "123", "location": "Sydney NSW"},
        )

    monkeypatch.setattr(
        seek_jd_adapter, "fetch_seek_detail_with_navigation_retry", fetch
    )

    result = seek_jd_adapter.fetch_seek_jd_for_job(
        {
            "id": 7,
            "source_job_id": "123",
            "canonical_url": "https://au.seek.com/job/123",
        }
    )

    assert result.full_description == "Validated SEEK description"
    assert result.jd_source == "seek_job_page"
    assert result.facts == {"location": "Sydney NSW"}
    assert calls == [(91, "https://au.seek.com/job/123", "123", 7)]
    assert closed == [91]
