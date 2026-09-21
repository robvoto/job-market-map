"""Deterministic, fail-closed normalization for source salary text."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_AMOUNT = r"(?:\$\s*)?(\d+(?:[,.]\d+)?)(?:\s*([kKmM]))?"
_RANGE_RE = re.compile(rf"(?P<low>{_AMOUNT})\s*(?:-|–|to)\s*(?P<high>{_AMOUNT})", re.IGNORECASE)
_SINGLE_RE = re.compile(rf"(?P<amount>{_AMOUNT})", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"\b(AUD|NZD|USD|GBP|EUR)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SalaryNormalization:
    state: str
    minimum: float | None = None
    maximum: float | None = None
    period: str | None = None
    currency: str | None = None
    qualifier: str | None = None

    def as_dict(self) -> dict[str, object | None]:
        return {"state": self.state, "min_amount": self.minimum, "max_amount": self.maximum, "period": self.period, "currency": self.currency, "qualifier": self.qualifier}


def _number(value: str, suffix: str | None) -> float:
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(value) from exc
    if suffix and suffix.casefold() == "k":
        number *= 1000
    elif suffix and suffix.casefold() == "m":
        number *= 1_000_000
    return float(number)


def _period(text: str) -> str | None:
    lower = text.casefold()
    patterns = (("hour", r"(?:hourly|per\s+hour|/\s*hr?\b|\bhr\b)"), ("day", r"(?:p\.?\s*d\.?|per\s+day|daily|/\s*day\b|\bday\b)"), ("week", r"(?:p\.?\s*w\.?|per\s+week|weekly|/\s*week\b|\bweek\b)"), ("month", r"(?:p\.?\s*m\.?|per\s+month|monthly|/\s*month\b|\bmonth\b)"), ("year", r"(?:p\.?\s*a\.?|per\s+(?:annum|year)|annual(?:ly)?|/\s*year\b|\byear\b)"))
    matches = [name for name, pattern in patterns if re.search(pattern, lower)]
    return matches[0] if len(matches) == 1 else None


def _qualifier(text: str) -> str | None:
    lower = text.casefold()
    if re.search(r"\binclusive\s+of\s+super\b|\binc(?:lusive)?\s+super\b", lower):
        return "includes_super"
    if re.search(r"(?:\+|\bplus)\s+super\b", lower):
        return "plus_super"
    if re.search(r"\bpackage(?:d|\s+salary)?\b", lower):
        return "package"
    return None


def normalize_salary(text: str | None) -> SalaryNormalization:
    """Normalize only explicit numeric source formats; otherwise return unknown."""
    if text is None or not str(text).strip():
        return SalaryNormalization("not_present")
    raw = " ".join(str(text).split()).strip()
    if raw.casefold() in {"n/a", "na", "not applicable", "no salary", "unpaid"}:
        return SalaryNormalization("not_applicable")
    if raw.casefold() in {"competitive", "salary undisclosed", "undisclosed", "negotiable"}:
        return SalaryNormalization("unknown")
    match = _RANGE_RE.search(raw)
    if match:
        low = _number(match.group(2), match.group(3))
        high = _number(match.group(5), match.group(6))
    else:
        single = _SINGLE_RE.search(raw)
        if not single:
            return SalaryNormalization("unknown")
        low = high = _number(single.group(2), single.group(3))
    if low > high:
        return SalaryNormalization("unknown")
    currency = _CURRENCY_RE.search(raw)
    return SalaryNormalization("known", low, high, _period(raw), currency.group(1).upper() if currency else None, _qualifier(raw))


def backfill_salary_normalization(conn: sqlite3.Connection) -> None:
    """Normalize existing raw salary evidence once, without changing raw text."""
    rows = conn.execute("""SELECT j.id, j.salary_text, s.state AS field_state
                            FROM jobs j
                            LEFT JOIN job_field_states s
                              ON s.job_id=j.id AND s.field_name='salary'
                           WHERE j.salary_normalized_state IS NULL""").fetchall()
    for row in rows:
        value = normalize_salary(row["salary_text"])
        if value.state == "not_present" and row["field_state"] in {"unknown", "not_present", "not_applicable"}:
            value = SalaryNormalization(row["field_state"])
        conn.execute("""UPDATE jobs SET salary_normalized_state=?, salary_min_amount=?, salary_max_amount=?, salary_period=?, salary_currency=?, salary_qualifier=? WHERE id=?""", (value.state, value.minimum, value.maximum, value.period, value.currency, value.qualifier, int(row["id"])))
        if value.state != "not_present" and row["field_state"] == "known":
            conn.execute("""UPDATE job_field_states SET state=?, evidence_source='salary:deterministic', checked_at=datetime('now') WHERE job_id=? AND field_name='salary'""", (value.state, int(row["id"])))


def store_salary_normalization(conn: sqlite3.Connection, job_id: int, text: str | None) -> SalaryNormalization:
    value = normalize_salary(text)
    conn.execute("""UPDATE jobs SET salary_normalized_state=?, salary_min_amount=?, salary_max_amount=?, salary_period=?, salary_currency=?, salary_qualifier=? WHERE id=?""", (value.state, value.minimum, value.maximum, value.period, value.currency, value.qualifier, int(job_id)))
    return value
