from collector.salary import normalize_salary


def test_normalizes_explicit_annual_range_and_super():
    value = normalize_salary("AUD $100,000 - $120,000 per annum + super")
    assert value.as_dict() == {"state": "known", "min_amount": 100000.0, "max_amount": 120000.0, "period": "year", "currency": "AUD", "qualifier": "plus_super"}


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


def test_ambiguous_or_missing_salary_is_not_guessed():
    assert normalize_salary("Competitive").state == "unknown"
    assert normalize_salary("$100 - $200").period is None
    assert normalize_salary(None).state == "not_present"
    assert normalize_salary("N/A").state == "not_applicable"
