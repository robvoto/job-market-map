import json
from pathlib import Path

from sources.linkedin import parse_linkedin_snapshot


def test_real_linkedin_snapshot_parses_seven_card_only_results():
    snapshot = json.loads(
        Path("tests/fixtures/linkedin_snapshot.json").read_text(encoding="utf-8")
    )
    cards, total = parse_linkedin_snapshot(
        snapshot, query_text="technical implementation", query_location="Sydney NSW"
    )
    assert total == 645
    assert len(cards) == 7
    assert cards[0].source_job_id == "4464190406"
    assert cards[0].title == "Project Coordinator – Tier 2 Builder"
    assert cards[0].employer == "Linktal Recruitment"
    assert cards[0].location == "Sydney, New South Wales, Australia"
    assert cards[0].workplace_type == "On-site"
    assert cards[1].title == "F5 Platforms Engineer"
    assert cards[1].workplace_type == "Hybrid"


def test_linkedin_parser_keeps_easy_apply_but_discards_personal_viewed_and_ui_badges():
    snapshot = {
        "text": (
            "Example Role\n"
            "Example Role\n"
            "Example Co\n"
            "Sydney, New South Wales, Australia\n"
            "Viewed\n"
            "Promoted\n"
            "Easy Apply\n"
            "Actively reviewing applicants"
        ),
        "elements": [
            {
                "href": "https://www.linkedin.com/jobs/view/example-role-4464190406",
                "text": "Example Role",
                "ariaLabel": "Example Role",
            }
        ],
    }
    cards, _ = parse_linkedin_snapshot(
        snapshot, query_text="example", query_location="Sydney NSW"
    )
    card = cards[0]
    assert card.easy_apply is True
    assert card.card_tags is None
    assert "Viewed" not in (card.raw_card_text or "")
    assert "Viewed" not in str(card.raw_json)
    assert card.teaser_text is None
