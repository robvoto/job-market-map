from collector.salary import normalize_salary


def test_normalizes_explicit_annual_range_and_super():
    value = normalize_salary("AUD $100,000 - $120,000 per annum + super")
    assert value.as_dict() == {
        "state": "known",
        "min_amount": 100000.0,
        "max_amount": 120000.0,
        "period": "year",
        "currency": "AUD",
        "qualifier": "plus_super",
    }


def test_normalizes_daily_range():
    value = normalize_salary("AUD 800 - 900 day")
    assert value.minimum == 800
    assert value.maximum == 900
    assert value.period == "day"


def test_keeps_period_unknown_when_not_proven():
    value = normalize_salary("$120k + super")
    assert value.state == "known"
    assert value.minimum == value.maximum == 120000
    assert value.period is None
    assert value.qualifier == "plus_super"


def test_one_sided_bounds_are_not_falsely_exact():
    upper = normalize_salary("Up to $150,000 per annum")
    lower = normalize_salary("From $120,000 per annum")
    assert (upper.minimum, upper.maximum, upper.period) == (None, 150000, "year")
    assert (lower.minimum, lower.maximum, lower.period) == (120000, None, "year")


def test_period_tokens_require_real_boundaries():
    assert normalize_salary("$150,000 package").period is None
    assert normalize_salary("$50/hour").period == "hour"
    assert normalize_salary("$900 p.d.").period == "day"
    assert normalize_salary("$120k p.a.").period == "year"


def test_amount_parser_supports_grouped_decimals_without_truncating():
    value = normalize_salary("AUD $1,200.50 per week")
    assert value.minimum == value.maximum == 1200.50
    assert value.period == "week"


def test_ambiguous_or_missing_salary_is_not_guessed():
    assert normalize_salary("Competitive").state == "unknown"
    assert normalize_salary("$100 - $200").period is None
    assert normalize_salary(None).state == "not_present"
    assert normalize_salary("N/A").state == "not_applicable"


def test_parser_state_does_not_overwrite_raw_salary_field_state(tmp_path, monkeypatch):
    from collector import db
    from collector.ingest import ingest_card
    from collector.models import CardObservation

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    result = ingest_card(
        CardObservation(
            source="seek",
            source_job_id="competitive-1",
            canonical_url="https://seek.test/competitive-1",
            title="Role",
            salary_text="Competitive",
        )
    )
    with db.connect() as conn:
        row = conn.execute(
            "SELECT salary_text,salary_normalized_state FROM jobs WHERE id=?",
            (result.observation_job_id,),
        ).fetchone()
        state = conn.execute(
            "SELECT state FROM job_field_states WHERE job_id=? AND field_name='salary'",
            (result.observation_job_id,),
        ).fetchone()["state"]
    assert row["salary_text"] == "Competitive"
    assert row["salary_normalized_state"] == "unknown"
    assert state == "known"


def test_descriptive_non_salary_numbers_fail_closed():
    samples = (
        "Good Rates + 12MO Rolling Contract",
        "$80 monthly credit and 25% off Optus products",
        "Up to 30 Months Contract - Rates Negotiable",
        "Competitive hourly rate, 12M FTC, Mon-Fri Shift",
        "The resort is undergoing a $20 million refurbishment",
        "Project: $15M Industrial Refurbishment",
        "Generous NFP Salary Packaging | 12% Super",
    )
    assert all(normalize_salary(text).state == "unknown" for text in samples)


def test_malformed_scaled_amounts_fail_closed():
    assert normalize_salary("$154231.0k - $178369.0k p.a.").state == "unknown"
    assert normalize_salary("$160,000 - 200,000k").state == "unknown"


def test_range_suffix_applies_to_both_compact_bounds():
    value = normalize_salary("$150-250K base plus super depending on experience")
    assert (value.minimum, value.maximum) == (150000, 250000)


def test_non_super_percentage_modifier_fails_closed():
    assert normalize_salary("80% of $95,961-$105,250 per annum").state == "unknown"
    super_value = normalize_salary("$127,105-$137,584 per annum + 12% Superannuation")
    assert super_value.state == "known"
    assert super_value.qualifier == "plus_super"
