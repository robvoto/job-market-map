from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

from collector.models import CardObservation

LINKEDIN_ID_RE = re.compile(r"^(?:li-)?(\d{7,12})$", re.IGNORECASE)


def linkedin_numeric_job_id(value: object) -> str | None:
    text = str(value or "").strip()
    match = LINKEDIN_ID_RE.fullmatch(text)
    return match.group(1) if match else None


def normalize_linkedin_source_job_id(value: object) -> str:
    numeric = linkedin_numeric_job_id(value)
    return f"li-{numeric}" if numeric else str(value or "").strip()


def linkedin_identity_aliases(value: object) -> tuple[str, ...]:
    numeric = linkedin_numeric_job_id(value)
    if not numeric:
        text = str(value or "").strip()
        return (text,) if text else ()
    return (f"li-{numeric}", numeric)


def _value(row: Any, key: str) -> Any:
    value = row.get(key) if hasattr(row, "get") else getattr(row, key, None)
    try:
        if value is not None and bool(math.isnan(value)):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _text(row: Any, key: str) -> str | None:
    value = _value(row, key)
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    return text or None


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "_asdict"):
        return _json_safe(value._asdict())
    return str(value)


def _salary_text(row: Any) -> str | None:
    minimum = _value(row, "min_amount")
    maximum = _value(row, "max_amount")
    currency = _text(row, "currency")
    interval = _text(row, "interval")
    if minimum is None and maximum is None:
        return None

    def fmt(value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"

    if minimum is not None and maximum is not None:
        amount = f"{fmt(minimum)} - {fmt(maximum)}"
    else:
        amount = fmt(minimum if minimum is not None else maximum)
    parts = [part for part in (currency, amount, interval) if part]
    return " ".join(parts) or None


def _posted_at(row: Any) -> str | None:
    value = _value(row, "date_posted")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text or None


def _location_text(row: Any) -> str | None:
    value = _value(row, "location")
    if value is None:
        return None
    if isinstance(value, str):
        return " ".join(value.split()).strip() or None
    city = str(getattr(value, "city", "") or "").strip()
    state = str(getattr(value, "state", "") or "").strip()
    country = getattr(value, "country", None)
    country_text = str(getattr(country, "value", country) or "").strip()
    parts = [part for part in (city, state, country_text) if part and part != "None"]
    return ", ".join(parts) or str(value).strip() or None


def observation_from_jobspy_row(
    row: Any,
    *,
    query_text: str,
    query_location: str,
    geography_code: str | None,
    rank: int,
    offset: int,
    page_size: int,
    captured_at: str | None = None,
) -> CardObservation:
    source_job_id = normalize_linkedin_source_job_id(_value(row, "id"))
    numeric_id = linkedin_numeric_job_id(source_job_id)
    canonical_url = _text(row, "job_url")
    if not canonical_url and numeric_id:
        canonical_url = f"https://www.linkedin.com/jobs/view/{numeric_id}"
    if not source_job_id or not canonical_url:
        raise ValueError("LinkedIn JobSpy row is missing source identity or job URL")

    raw = _json_safe(dict(row) if hasattr(row, "keys") else {})
    title = _text(row, "title")
    employer = _text(row, "company") or _text(row, "company_name")
    location = _location_text(row)
    salary = _salary_text(row)
    employment_type = _text(row, "job_type")
    is_remote = _value(row, "is_remote")
    workplace_type = "Remote" if is_remote is True else None
    posted_at = _posted_at(row)
    raw_card_text = "\n".join(
        part for part in (title, employer, location, salary, posted_at) if part
    )

    return CardObservation(
        source="linkedin",
        source_job_id=source_job_id,
        canonical_url=canonical_url,
        title=title,
        employer=employer,
        location=location,
        geography_code=geography_code,
        salary_text=salary,
        employment_type=employment_type,
        workplace_type=workplace_type,
        posted_at=posted_at,
        raw_card_text=raw_card_text or None,
        raw_json={"jobspy": raw, "offset": offset},
        query_text=query_text,
        query_location=query_location,
        rank=rank,
        page_number=(offset // max(1, int(page_size))) + 1,
        captured_at=captured_at,
    )
