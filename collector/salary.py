"""Deterministic, fail-closed normalization for source salary text."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_AMOUNT = rf"(?<![\w.])(?:\$\s*)?({_NUMBER})(?:\s*([kKmM]))?(?![\w%])"
_AMOUNT_RE = re.compile(_AMOUNT)
_RANGE_RE = re.compile(
    rf"(?P<low>{_AMOUNT})\s*(?:-|–|to)\s*(?P<high>{_AMOUNT})",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(rf"(?P<amount>{_AMOUNT})", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"\b(AUD|NZD|USD|GBP|EUR)\b", re.IGNORECASE)
_UPPER_BOUND_RE = re.compile(
    r"\b(?:up\s+to|to|maximum|max(?:imum)?\s+of)\b", re.IGNORECASE
)
_LOWER_BOUND_RE = re.compile(
    r"\b(?:from|starting\s+(?:at|from)|minimum|min(?:imum)?\s+of)\b",
    re.IGNORECASE,
)
_PERIOD_PATTERNS = (
    (
        "hour",
        r"(?:\bp\.?\s*h\.?(?=\W|$)|\bhourly\b|\bper\s+hour\b|/\s*(?:hour|hr)\b|\bhr\b)",
    ),
    (
        "day",
        r"(?:\bp\.?\s*d\.?(?=\W|$)|\bper\s+day\b|\bdaily\b|/\s*day\b|\bday\b)",
    ),
    (
        "week",
        r"(?:\bp\.?\s*w\.?(?=\W|$)|\bper\s+week\b|\bweekly\b|/\s*week\b|\bweek\b)",
    ),
    (
        "month",
        r"(?:\bp\.?\s*m\.?(?=\W|$)|\bper\s+month\b|\bmonthly\b|/\s*month\b|\bmonth\b)",
    ),
    (
        "year",
        r"(?:\bp\.?\s*a\.?(?=\W|$)|\bper\s+(?:annum|year)\b|\bannum\b|\bannual(?:ly)?\b|/\s*year\b|\byear\b)",
    ),
)
_ALLOWED_CONTEXT_WORDS = {
    "a",
    "annual",
    "annually",
    "annum",
    "approx",
    "approximately",
    "at",
    "aud",
    "base",
    "based",
    "c",
    "circa",
    "compensation",
    "d",
    "daily",
    "day",
    "depending",
    "experience",
    "from",
    "fte",
    "gbp",
    "h",
    "hour",
    "hourly",
    "hr",
    "includes",
    "inclusive",
    "incl",
    "m",
    "max",
    "maximum",
    "min",
    "minimum",
    "month",
    "monthly",
    "neg",
    "negotiable",
    "nzd",
    "of",
    "on",
    "p",
    "package",
    "packaged",
    "pay",
    "per",
    "plus",
    "range",
    "ranges",
    "rate",
    "rates",
    "relative",
    "remuneration",
    "salary",
    "starting",
    "super",
    "superannuation",
    "to",
    "total",
    "up",
    "usd",
    "w",
    "week",
    "weekly",
    "year",
    "eur",
}


@dataclass(frozen=True)
class SalaryNormalization:
    state: str
    minimum: float | None = None
    maximum: float | None = None
    period: str | None = None
    currency: str | None = None
    qualifier: str | None = None

    def as_dict(self) -> dict[str, object | None]:
        return {
            "state": self.state,
            "min_amount": self.minimum,
            "max_amount": self.maximum,
            "period": self.period,
            "currency": self.currency,
            "qualifier": self.qualifier,
        }


def _number(value: str, suffix: str | None) -> float:
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(value) from exc
    if number <= 0:
        raise ValueError(value)
    if suffix and suffix.casefold() == "k":
        # Values such as 154231.0k are malformed source text, not $154.231m.
        if number >= 10_000:
            raise ValueError(value)
        number *= 1000
    elif suffix and suffix.casefold() == "m":
        if number >= 100:
            raise ValueError(value)
        number *= 1_000_000
    return float(number)


def _period(text: str) -> str | None:
    lower = text.casefold()
    matches = [name for name, pattern in _PERIOD_PATTERNS if re.search(pattern, lower)]
    return matches[0] if len(matches) == 1 else None


def _qualifier(text: str) -> str | None:
    lower = text.casefold()
    if re.search(r"\binclusive\s+of\s+super\b|\binc(?:lusive)?\s+super\b", lower):
        return "includes_super"
    if re.search(
        r"(?:\+|\bplus\b)\s*(?:(?:up\s+to\s+)?\d+(?:\.\d+)?\s*%\s*)?super(?:annuation)?\b",
        lower,
    ):
        return "plus_super"
    if re.search(r"\bpackage(?:d|\s+salary)?\b", lower):
        return "package"
    return None


def _supported_context(text: str) -> bool:
    """Reject prose/non-salary numbers instead of trying to infer their meaning."""
    residual = _AMOUNT_RE.sub(" ", text.casefold())
    residual = _CURRENCY_RE.sub(" ", residual)
    # Explicit super percentages are qualifiers, not salary amounts.
    residual = re.sub(
        r"(?:\+|\bplus\b)?\s*(?:up\s+to\s+)?\d+(?:\.\d+)?\s*%\s*super(?:annuation)?(?:\s*&\s*leave\s+loading)?",
        " ",
        residual,
    )
    if re.search(r"\d+(?:\.\d+)?\s*%", residual):
        return False
    residual = re.sub(r"[^a-z]+", " ", residual)
    words = [word for word in residual.split() if word]
    return all(word in _ALLOWED_CONTEXT_WORDS for word in words)


def normalize_salary(text: str | None) -> SalaryNormalization:
    """Normalize only mechanically provable salary expressions; otherwise unknown."""
    if text is None or not str(text).strip():
        return SalaryNormalization("not_present")
    raw = " ".join(str(text).split()).strip()
    if raw.casefold() in {
        "n/a",
        "na",
        "not applicable",
        "no salary",
        "unpaid",
    }:
        return SalaryNormalization("not_applicable")
    if raw.casefold() in {
        "competitive",
        "salary undisclosed",
        "undisclosed",
        "negotiable",
    }:
        return SalaryNormalization("unknown")
    if not _supported_context(raw):
        return SalaryNormalization("unknown")

    amount_matches = list(_AMOUNT_RE.finditer(raw))
    match = _RANGE_RE.search(raw)
    try:
        if match:
            if len(amount_matches) != 2:
                return SalaryNormalization("unknown")
            low_suffix = match.group(3)
            high_suffix = match.group(6)
            if bool(low_suffix) != bool(high_suffix):
                shared_suffix = low_suffix or high_suffix
                low_suffix = high_suffix = shared_suffix
            low = _number(match.group(2), low_suffix)
            high = _number(match.group(5), high_suffix)
            if low > high:
                return SalaryNormalization("unknown")
        else:
            if len(amount_matches) != 1:
                return SalaryNormalization("unknown")
            single = _SINGLE_RE.search(raw)
            if not single:
                return SalaryNormalization("unknown")
            amount = _number(single.group(2), single.group(3))
            prefix = raw[: single.start()]
            has_upper_bound = bool(_UPPER_BOUND_RE.search(prefix))
            has_lower_bound = bool(_LOWER_BOUND_RE.search(prefix))
            if has_upper_bound and has_lower_bound:
                return SalaryNormalization("unknown")
            low = None if has_upper_bound else amount
            high = None if has_lower_bound else amount
    except ValueError:
        return SalaryNormalization("unknown")

    currency = _CURRENCY_RE.search(raw)
    return SalaryNormalization(
        "known",
        low,
        high,
        _period(raw),
        currency.group(1).upper() if currency else None,
        _qualifier(raw),
    )


def backfill_salary_normalization(conn: sqlite3.Connection) -> None:
    """Normalize existing raw salary evidence once, without changing raw text/state."""
    rows = conn.execute(
        """SELECT j.id, j.salary_text, s.state AS field_state
             FROM jobs j
             LEFT JOIN job_field_states s
               ON s.job_id=j.id AND s.field_name='salary'
            WHERE j.salary_normalized_state IS NULL"""
    ).fetchall()
    for row in rows:
        value = normalize_salary(row["salary_text"])
        if value.state == "not_present" and row["field_state"] in {
            "unknown",
            "not_present",
            "not_applicable",
        }:
            value = SalaryNormalization(row["field_state"])
        conn.execute(
            """UPDATE jobs SET salary_normalized_state=?, salary_min_amount=?,
                      salary_max_amount=?, salary_period=?, salary_currency=?,
                      salary_qualifier=? WHERE id=?""",
            (
                value.state,
                value.minimum,
                value.maximum,
                value.period,
                value.currency,
                value.qualifier,
                int(row["id"]),
            ),
        )


def store_salary_normalization(
    conn: sqlite3.Connection, job_id: int, text: str | None
) -> SalaryNormalization:
    value = normalize_salary(text)
    conn.execute(
        """UPDATE jobs SET salary_normalized_state=?, salary_min_amount=?,
                  salary_max_amount=?, salary_period=?, salary_currency=?,
                  salary_qualifier=? WHERE id=?""",
        (
            value.state,
            value.minimum,
            value.maximum,
            value.period,
            value.currency,
            value.qualifier,
            int(job_id),
        ),
    )
    return value
