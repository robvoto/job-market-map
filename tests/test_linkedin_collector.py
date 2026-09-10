from sources.linkedin_collector import build_linkedin_url


def test_linkedin_url_supports_arbitrary_offset_and_freshness():
    url = build_linkedin_url(
        "technical implementation", "Sydney NSW", offset=14, days=7
    )
    assert "f_TPR=r604800" in url
    assert "sortBy=DD" in url
    assert "start=14" in url


def test_linkedin_collector_tracks_last_id_explicitly():
    import inspect

    import sources.linkedin_collector as module

    source = inspect.getsource(module.collect_linkedin_chunk)
    assert 'last_id = cursor.get("last_job_id")' in source
    assert "next(reversed(seen_chunk))" not in source
