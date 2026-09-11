from datetime import date

from sources.linkedin import (
    linkedin_geography_code,
    linkedin_identity_aliases,
    normalize_linkedin_source_job_id,
    observation_from_jobspy_row,
)


def test_linkedin_identity_normalises_jobspy_and_accepts_old_numeric_alias():
    assert normalize_linkedin_source_job_id("li-4464190406") == "li-4464190406"
    assert normalize_linkedin_source_job_id("4464190406") == "li-4464190406"
    assert linkedin_identity_aliases("li-4464190406") == (
        "li-4464190406",
        "4464190406",
    )


def test_jobspy_row_maps_only_neutral_discovery_fields():
    row = {
        "id": "li-4464190406",
        "title": "Business Analyst",
        "company": "Example Co",
        "location": "Sydney, NSW, Australia",
        "job_url": "https://www.linkedin.com/jobs/view/4464190406",
        "date_posted": date(2026, 9, 11),
        "is_remote": True,
        "min_amount": 800,
        "max_amount": 900,
        "currency": "AUD",
        "interval": "day",
        "easy_apply": True,
    }
    observation = observation_from_jobspy_row(
        row,
        query_text="business analyst",
        query_location="New South Wales, Australia",
        geography_code="NSW",
        rank=1,
        offset=0,
        page_size=25,
    )
    assert observation.source_job_id == "li-4464190406"
    assert observation.title == "Business Analyst"
    assert observation.salary_text == "AUD 800 - 900 day"
    assert observation.posted_at == "2026-09-11"
    assert observation.workplace_type == "Remote"
    assert observation.geography_code == "NSW"
    # JobSpy's Easy Apply value is deliberately not canonicalised; the direct
    # vacancy response owns apply-method evidence.
    assert observation.easy_apply is None
    assert observation.card_tags is None


def test_linkedin_explicit_card_location_overrides_search_geography():
    assert linkedin_geography_code("Brisbane, Queensland, Australia", "NSW") == "QLD"
    assert linkedin_geography_code("Canberra, ACT, Australia", "NSW") == "ACT"
    assert linkedin_geography_code("Sydney, New South Wales, Australia", "QLD") == "NSW"
    assert linkedin_geography_code("Australia", "NSW") == "NSW"
