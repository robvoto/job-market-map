"""Deterministic, fail-closed normalization for source salary text."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

_NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_AMOUNT = rf"(?<![\w.])(?:\$\s*)?({_NUMBER})(?:\s*([kKmM]))?(?![\w.%])"
_AMOUNT_RE = re.compile(_AMOUNT)
_RANGE_RE = re.compile(
    rf"(?P<low>{_AMOUNT})\s*(?:-|–|to)\s*(?P<high>{_AMOUNT})",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(rf"(?P<amount>{_AMOUNT})", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"\b(AUD|NZD|USD|GBP|EUR)\b", re.IGNORECASE)
_UPPER_BOUND_RE = re.compile(
    r"\b(?:up\s*to|to|maximum|max(?:imum)?\s+of)\b", re.IGNORECASE
)
_LOWER_BOUND_RE = re.compile(
    r"\b(?:from|starting\s+(?:at|from)|minimum|min(?:imum)?\s+of)\b",
    re.IGNORECASE,
)
_PERIOD_PATTERNS = (
    (
        "hour",
        r"(?:\bp\s*/\s*h\.?(?=\W|$)|\bp\.?\s*h\.?(?=\W|$)|\bhourly\b|\bper\s+hour\b|/\s*(?:hour|hr)\b|\bhr\b)",
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
    "inc",
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
    bound: str | None = None

    def as_dict(self) -> dict[str, object | None]:
        return {
            "state": self.state,
            "min_amount": self.minimum,
            "max_amount": self.maximum,
            "period": self.period,
            "currency": self.currency,
            "qualifier": self.qualifier,
            "bound": self.bound,
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
    if re.search(
        r"\binclusive\s+of\s+super\b|\binc\.?\s+super\b|\bincl\.?\s+super\b",
        lower,
    ):
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


def _context_currency(
    *, source: str | None, canonical_url: str | None, geography_code: str | None
) -> str | None:
    """Infer AUD only when the canonical Australian SEEK host proves it."""
    source_name = str(source or "").strip().casefold()
    host = (urlparse(str(canonical_url or "")).hostname or "").casefold()
    australian_seek_host = host in {"au.seek.com", "seek.com.au"} or host.endswith(
        (".au.seek.com", ".seek.com.au")
    )
    if source_name == "seek" and australian_seek_host:
        return "AUD"
    return None


_PLAUSIBLE_RANGES = {
    # Intentionally wide hard bounds catch unit/suffix corruption without
    # trying to decide whether an otherwise valid salary is attractive.
    "hour": (1, 5_000),
    "day": (10, 50_000),
    "week": (25, 250_000),
    "month": (100, 1_000_000),
    "year": (1_000, 10_000_000),
}


def normalize_salary(
    text: str | None,
    *,
    field_state: str | None = None,
    default_currency: str | None = None,
) -> SalaryNormalization:
    """Normalize only mechanically provable salary expressions; otherwise unknown."""
    state = str(field_state or "").strip().casefold()
    if state in {"unknown", "not_present", "not_applicable"}:
        return SalaryNormalization(state)
    if text is None or not str(text).strip():
        # A blank observation does not prove that the source checked and found
        # no salary. Only an explicit field state can establish not_present.
        return SalaryNormalization("unknown")
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
    bound: str
    try:
        if match:
            if len(amount_matches) != 2:
                return SalaryNormalization("unknown")
            low_suffix = match.group(3)
            high_suffix = match.group(6)
            # The high endpoint's suffix commonly applies to both (100-120k).
            # A low-only suffix does not: "1k - 1100 p.d." means 1,000 to
            # 1,100 per day.
            if high_suffix and not low_suffix:
                low_suffix = high_suffix
            low = _number(match.group(2), low_suffix)
            high = _number(match.group(5), high_suffix)
            if low > high:
                return SalaryNormalization("unknown")
            bound = "range"
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
            bound = "up_to" if has_upper_bound else "from" if has_lower_bound else "exact"
    except ValueError:
        return SalaryNormalization("unknown")

    period = _period(raw)
    if period is not None:
        floor, ceiling = _PLAUSIBLE_RANGES[period]
        values = [amount for amount in (low, high) if amount is not None]
        if any(amount < floor or amount > ceiling for amount in values):
            return SalaryNormalization("unknown")
    currency = _CURRENCY_RE.search(raw)
    return SalaryNormalization(
        "known",
        low,
        high,
        period,
        currency.group(1).upper() if currency else default_currency,
        _qualifier(raw),
        bound,
    )


def backfill_salary_normalization(conn: sqlite3.Connection) -> None:
    """Normalize existing raw salary evidence once, without changing raw text/state."""
    rows = conn.execute(
        """SELECT j.id, j.salary_text, j.source, j.canonical_url,
                  j.geography_code, s.state AS field_state
             FROM jobs j
             LEFT JOIN job_field_states s
               ON s.job_id=j.id AND s.field_name='salary'
            WHERE j.salary_normalized_state IS NULL"""
    ).fetchall()
    for row in rows:
        value = normalize_salary(
            row["salary_text"],
            field_state=row["field_state"],
            default_currency=_context_currency(
                source=row["source"],
                canonical_url=row["canonical_url"],
                geography_code=row["geography_code"],
            ),
        )
        conn.execute(
            """UPDATE jobs SET salary_normalized_state=?, salary_min_amount=?,
                      salary_max_amount=?, salary_period=?, salary_currency=?,
                      salary_qualifier=?, salary_bound=? WHERE id=?""",
            (
                value.state,
                value.minimum,
                value.maximum,
                value.period,
                value.currency,
                value.qualifier,
                value.bound,
                int(row["id"]),
            ),
        )


def store_salary_normalization(
    conn: sqlite3.Connection,
    job_id: int,
    text: str | None,
    *,
    source: str | None = None,
    canonical_url: str | None = None,
    geography_code: str | None = None,
    field_state: str | None = None,
) -> SalaryNormalization:
    value = normalize_salary(
        text,
        field_state=field_state,
        default_currency=_context_currency(
            source=source,
            canonical_url=canonical_url,
            geography_code=geography_code,
        ),
    )
    conn.execute(
        """UPDATE jobs SET salary_normalized_state=?, salary_min_amount=?,
                  salary_max_amount=?, salary_period=?, salary_currency=?,
                  salary_qualifier=?, salary_bound=? WHERE id=?""",
        (
            value.state,
            value.minimum,
            value.maximum,
            value.period,
            value.currency,
            value.qualifier,
            value.bound,
            int(job_id),
        ),
    )
    return value


def renormalize_salary_rows(
    conn: sqlite3.Connection, *, apply: bool = False
) -> dict[str, object]:
    """Recompute every normalized salary field from raw text and source state.

    This is deliberately separate from startup migration: callers can preview
    all changes, take a verified backup, then apply once while collection is
    quiescent. Raw ``salary_text`` and ``field_states`` are never rewritten.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    has_bound = "salary_bound" in columns
    rows = conn.execute(
        """SELECT j.id,j.source,j.canonical_url,j.geography_code,j.salary_text,
                  j.salary_normalized_state,j.salary_min_amount,j.salary_max_amount,
                  j.salary_period,j.salary_currency,j.salary_qualifier,j.salary_bound,
                  s.state AS field_state
             FROM jobs j LEFT JOIN job_field_states s
               ON s.job_id=j.id AND s.field_name='salary'
            ORDER BY j.id"""
    ).fetchall()
    before = Counter(str(row["salary_normalized_state"] or "<null>") for row in rows)
    after: Counter[str] = Counter()
    changes = 0
    missing_period = 0
    missing_currency = 0
    missing_both = 0
    for row in rows:
        value = normalize_salary(
            row["salary_text"],
            field_state=row["field_state"],
            default_currency=_context_currency(
                source=row["source"],
                canonical_url=row["canonical_url"],
                geography_code=row["geography_code"],
            ),
        )
        after[value.state] += 1
        if value.state == "known":
            missing_period += value.period is None
            missing_currency += value.currency is None
            missing_both += value.period is None and value.currency is None
        old = (
            row["salary_normalized_state"], row["salary_min_amount"],
            row["salary_max_amount"], row["salary_period"],
            row["salary_currency"], row["salary_qualifier"],
            row["salary_bound"] if has_bound else None,
        )
        new = (
            value.state, value.minimum, value.maximum, value.period,
            value.currency, value.qualifier, value.bound,
        )
        if old != new:
            changes += 1
            if apply:
                conn.execute(
                    """UPDATE jobs SET salary_normalized_state=?,salary_min_amount=?,
                              salary_max_amount=?,salary_period=?,salary_currency=?,
                              salary_qualifier=?,salary_bound=? WHERE id=?""",
                    (*new, int(row["id"])),
                )
    if apply:
        persisted = Counter(
            str(row[0] or "<null>")
            for row in conn.execute(
                "SELECT salary_normalized_state FROM jobs"
            ).fetchall()
        )
        if persisted != after:
            raise RuntimeError(
                "persisted salary state counts differ from computed normalization"
            )
    return {
        "jobs_scanned": len(rows),
        "changed_rows": changes,
        "before_by_state": dict(sorted(before.items())),
        "after_by_state": dict(sorted(after.items())),
        "known_missing_period": missing_period,
        "known_missing_currency": missing_currency,
        "known_missing_both": missing_both,
        "missing_period_reason": "raw salary evidence contains no single supported period marker",
        "missing_currency_reason": "no explicit currency code and canonical source context does not prove AUD",
    }
