from collector.posting_dates import normalize_posting_date


def test_exact_source_timestamp_wins_over_relative_and_window_evidence():
    result = normalize_posting_date(
        posted_at="2026-09-20T08:15:00+10:00",
        posted_text="3 days ago",
        captured_at="2026-09-22T00:00:00+00:00",
        search_window_hours=120,
    )
    assert result.value == "2026-09-19T22:15:00+00:00"
    assert result.basis == "source_exact"


def test_relative_words_and_compact_units_are_capture_anchored():
    expected = (
        ("Listed three hours ago", "2026-09-21T21:00:00+00:00"),
        ("2 days ago", "2026-09-20T00:00:00+00:00"),
        ("3h ago", "2026-09-21T21:00:00+00:00"),
        ("1w ago", "2026-09-15T00:00:00+00:00"),
    )
    for text, value in expected:
        result = normalize_posting_date(
            posted_at=None,
            posted_text=text,
            captured_at="2026-09-22T00:00:00+00:00",
        )
        assert result.value == value
        assert result.basis == "source_relative"


def test_unusable_label_uses_only_explicit_window_as_conservative_bound():
    result = normalize_posting_date(
        posted_at="garbage",
        posted_text="Recently posted",
        captured_at="2026-09-22T00:00:00+00:00",
        search_window_hours=120,
    )
    assert result.value == "2026-09-17T00:00:00+00:00"
    assert result.basis == "search_window_bound"


def test_no_date_without_usable_relative_or_proven_window_stays_unknown():
    assert normalize_posting_date(
        posted_at=None,
        posted_text="Recently posted",
        captured_at="2026-09-22T00:00:00+00:00",
    ) is None


def test_relative_multiword_numbers_are_not_truncated_to_last_digit_word():
    captured = "2026-09-22T04:00:00+00:00"
    cases = {
        "Listed seventeen hours ago": "2026-09-21T11:00:00+00:00",
        "Listed twenty four minutes ago": "2026-09-22T03:36:00+00:00",
        "Listed thirty four minutes ago": "2026-09-22T03:26:00+00:00",
        "Listed twenty-four minutes ago": "2026-09-22T03:36:00+00:00",
    }
    for text, expected in cases.items():
        result = normalize_posting_date(
            posted_at=None, posted_text=text, captured_at=captured, search_window_hours=24
        )
        assert result is not None
        assert result.basis == "source_relative"
        assert result.value == expected
