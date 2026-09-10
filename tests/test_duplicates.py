from collector.duplicates import (
    core_fingerprint_from_values,
    duplicate_evidence,
    exact_card_fingerprint_from_mapping,
)


def base_row(**overrides):
    row = {
        "id": 1,
        "title": "Implementation Consultant",
        "employer": "Example Co",
        "location": "Sydney NSW",
        "salary_text": "$120k + super",
        "employment_type": "Full time",
        "workplace_type": "Hybrid",
        "classification_text": "Information & Communication Technology",
        "subclassification_text": "Consultants",
        "teaser_text": "Lead enterprise customer onboarding, API integration, testing and launch activities.",
    }
    row.update(overrides)
    return row


def test_fingerprints_normalize_case_spacing_and_punctuation():
    assert core_fingerprint_from_values(
        " Role ", "ACME Pty Ltd", "Sydney, NSW"
    ) == core_fingerprint_from_values("role", "acme pty ltd", "Sydney NSW")


def test_exact_rich_card_match_is_confidence_one():
    a = base_row(id=1)
    b = base_row(id=2)
    a["exact_card_fingerprint"] = exact_card_fingerprint_from_mapping(a)
    b["exact_card_fingerprint"] = exact_card_fingerprint_from_mapping(b)
    confidence, match_type, reasons = duplicate_evidence(a, b)
    assert confidence == 1.0
    assert match_type == "exact_rich_card"
    assert reasons


def test_same_title_different_employer_is_not_duplicate_candidate():
    assert (
        duplicate_evidence(base_row(id=1), base_row(id=2, employer="Different Co"))
        is None
    )


def test_near_rich_card_uses_multiple_card_signals():
    a = base_row(id=1)
    b = base_row(
        id=2,
        teaser_text="Lead enterprise customer onboarding and API integration through testing and launch.",
    )
    evidence = duplicate_evidence(a, b)
    assert evidence is not None
    confidence, match_type, reasons = evidence
    assert confidence >= 0.92
    assert match_type == "near_rich_card"
    assert len(reasons) >= 5


def test_core_candidate_key_does_not_require_identical_location_format():
    assert core_fingerprint_from_values(
        "Implementation Consultant", "Acme", "Sydney NSW"
    ) == core_fingerprint_from_values(
        "Implementation Consultant", "Acme", "Sydney, New South Wales, Australia"
    )


def test_rich_evidence_can_link_cross_board_location_wording_without_merging():
    a = base_row(id=1, location="Sydney NSW")
    b = base_row(id=2, location="Sydney, New South Wales, Australia")
    evidence = duplicate_evidence(a, b)
    assert evidence is not None
    confidence, match_type, _ = evidence
    assert confidence >= 0.92
    assert match_type == "near_rich_card"
