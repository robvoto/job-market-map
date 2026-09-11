import json
from pathlib import Path

import pytest

from sources.seek import SeekParseError, parse_seek_dom_cards, parse_seek_snapshot


def test_real_benchmark_snapshot_parses_all_seek_cards():
    snapshot = json.loads(
        Path("tests/fixtures/seek_snapshot.json").read_text(encoding="utf-8")
    )
    cards = parse_seek_snapshot(
        snapshot, query_text="technical implementation", query_location="Sydney NSW"
    )
    assert len(cards) == 32
    assert cards[0].source_job_id == "94548768"
    assert cards[0].title == "M365 Solution Consultant"
    assert cards[0].employer == "Green Light PS Pty Ltd"
    assert cards[0].employment_type == "Contract/Temp"
    assert cards[0].workplace_type == "Hybrid"
    assert cards[0].salary_text == "Competitive"
    assert "Strong applicant" in (cards[0].raw_json or {})["card_tags"]

    fde = next(
        card for card in cards if card.title == "Forward Deployed Engineer, Scams"
    )
    assert fde.source_job_id == "94544633"
    assert fde.employer == "Australian Financial Complaints Authority Limited"
    assert fde.classification_text == "Information & Communication Technology"
    assert fde.subclassification_text == "Engineering - Software"
    assert "software, AI, automation and data solutions" in (fde.teaser_text or "")


def test_seek_parser_fails_on_link_block_count_mismatch():
    snapshot = {
        "text": "Listed one hour ago\nRole\nat\nEmployer\nThis is a Full time job\nSydney NSW\n1h ago",
        "elements": [],
    }
    with pytest.raises(SeekParseError, match="no parseable cards"):
        parse_seek_snapshot(snapshot, query_text="x", query_location="Sydney")


def test_seek_dom_card_preserves_quick_apply_flag():
    cards = parse_seek_dom_cards(
        [
            {
                "source_job_id": "94581234",
                "canonical_url": "https://au.seek.com/job/94581234",
                "title": "Business Analyst",
                "employer": "Example",
                "easy_apply": True,
                "apply_method": "quick_apply",
                "posted_at": "2026-09-11T09:37:10.000Z",
            }
        ],
        query_text=None,
        query_location=None,
    )
    assert cards[0].easy_apply is True
    assert cards[0].apply_method == "quick_apply"
    assert cards[0].posted_at == "2026-09-11T09:37:10.000Z"
