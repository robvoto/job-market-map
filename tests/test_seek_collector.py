from sources.seek_collector import build_seek_url


def test_seek_url_has_freshness_sort_and_page():
    assert build_seek_url("technical implementation", "Sydney NSW", page=1) == (
        "https://www.seek.com.au/technical-implementation-jobs/in-Sydney-NSW?daterange=7&sortmode=ListedDate"
    )
    assert build_seek_url("technical implementation", "Sydney NSW", page=2).endswith(
        "&page=2"
    )


def test_collector_uses_parseable_card_wait_not_single_snapshot():
    import inspect

    import sources.seek_collector as module

    source = inspect.getsource(module.collect_seek_query)
    assert "_wait_for_seek_cards" in source


def test_terminal_seek_page_is_completion_not_parse_failure(monkeypatch):
    import sources.seek_collector as module

    monkeypatch.setattr(
        module,
        "snapshot",
        lambda *a, **k: type(
            "R",
            (),
            {
                "result": {
                    "title": "Results (Page 8)",
                    "url": "https://seek.test?page=8",
                    "text": "No matching search results\nWe couldn't find anything that matched your search.",
                    "elements": [],
                }
            },
        )(),
    )
    assert (
        module._wait_for_seek_cards(
            1, query_text="x", location="Sydney", page_number=8, timeout_seconds=0.5
        )
        == []
    )
