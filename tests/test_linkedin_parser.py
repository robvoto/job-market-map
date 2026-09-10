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
