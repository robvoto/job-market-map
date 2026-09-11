from collector.duplicates import (
    core_fingerprint_from_values,
    duplicate_evidence,
    exact_card_fingerprint_from_mapping,
    same_vacancy_evidence,
    specific_locality,
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


def test_same_vacancy_accepts_strong_cross_board_teaser_without_secondary_fields():
    a = base_row(
        id=1,
        location=None,
        salary_text=None,
        employment_type=None,
        workplace_type=None,
        classification_text=None,
        subclassification_text=None,
        teaser_text=(
            "Lead enterprise customer onboarding, API integration, testing and "
            "launch activities for strategic clients."
        ),
    )
    b = base_row(
        id=2,
        location=None,
        salary_text=None,
        employment_type=None,
        workplace_type=None,
        classification_text=None,
        subclassification_text=None,
        teaser_text=(
            "Lead enterprise customer onboarding, API integration, testing and "
            "launch activities for strategic clients."
        ),
    )

    evidence = same_vacancy_evidence(
        a,
        b,
        teaser_min_similarity=0.90,
        teaser_min_chars=40,
        min_secondary_signals=2,
    )

    assert evidence is not None
    confidence, match_type, reasons = evidence
    assert confidence >= 0.90
    assert match_type == "strong_teaser"
    assert "substantial teaser similarity 1.00" in reasons


def test_same_vacancy_requires_two_secondary_signals():
    a = base_row(
        id=1,
        location=None,
        salary_text=None,
        employment_type=None,
        workplace_type="Remote",
        classification_text=None,
        subclassification_text=None,
        teaser_text=None,
    )
    b = base_row(
        id=2,
        teaser_text=None,
        location="Melbourne VIC",
        salary_text=None,
        employment_type=None,
        workplace_type="Remote",
        classification_text=None,
        subclassification_text=None,
    )

    evidence = same_vacancy_evidence(
        a,
        b,
        teaser_min_similarity=0.90,
        teaser_min_chars=40,
        min_secondary_signals=2,
    )

    assert evidence is None


def test_same_vacancy_accepts_two_agreeing_secondary_signals():
    a = base_row(
        id=1,
        teaser_text=None,
        location=None,
        salary_text=None,
        employment_type=None,
        workplace_type="Remote",
        classification_text="Information Technology",
        subclassification_text=None,
    )
    b = base_row(
        id=2,
        teaser_text=None,
        location="Melbourne VIC",
        salary_text=None,
        employment_type=None,
        workplace_type="Remote",
        classification_text="Information Technology",
        subclassification_text=None,
    )

    evidence = same_vacancy_evidence(
        a,
        b,
        teaser_min_similarity=0.90,
        teaser_min_chars=40,
        min_secondary_signals=2,
    )

    assert evidence is not None
    confidence, match_type, reasons = evidence
    assert confidence < 0.92
    assert match_type == "secondary_signals"
    assert reasons[-2:] == ["same workplace type", "same classification"]


def test_specific_locality_normalizes_cross_board_location_formats():
    assert specific_locality("Coffs Harbour, New South Wales, Australia") == "coffs harbour"
    assert specific_locality("Coffs Harbour, Coffs Harbour & North Coast NSW") == "coffs harbour"
    assert specific_locality("Sydney NSW") == "sydney"
    assert specific_locality("New South Wales, Australia") == ""


def test_same_vacancy_accepts_cross_source_specific_locality_without_rich_fields():
    a = base_row(
        id=1,
        source="seek",
        location="Coffs Harbour, Coffs Harbour & North Coast NSW",
        salary_text=None,
        employment_type=None,
        workplace_type=None,
        classification_text=None,
        subclassification_text=None,
        teaser_text=None,
    )
    b = base_row(
        id=2,
        source="linkedin",
        location="Coffs Harbour, New South Wales, Australia",
        salary_text=None,
        employment_type=None,
        workplace_type=None,
        classification_text=None,
        subclassification_text=None,
        teaser_text=None,
    )
    evidence = same_vacancy_evidence(
        a,
        b,
        teaser_min_similarity=0.90,
        teaser_min_chars=40,
        min_secondary_signals=2,
    )
    assert evidence is not None
    confidence, match_type, reasons = evidence
    assert confidence == 0.96
    assert match_type == "cross_source_locality"
    assert "same specific locality coffs harbour" in reasons


def test_same_vacancy_does_not_treat_state_only_location_as_specific_locality():
    a = base_row(
        id=1,
        source="seek",
        location="NSW",
        salary_text=None,
        employment_type=None,
        workplace_type=None,
        classification_text=None,
        subclassification_text=None,
        teaser_text=None,
    )
    b = base_row(
        id=2,
        source="linkedin",
        location="New South Wales, Australia",
        salary_text=None,
        employment_type=None,
        workplace_type=None,
        classification_text=None,
        subclassification_text=None,
        teaser_text=None,
    )
    evidence = same_vacancy_evidence(
        a,
        b,
        teaser_min_similarity=0.90,
        teaser_min_chars=40,
        min_secondary_signals=2,
    )
    assert evidence is None
