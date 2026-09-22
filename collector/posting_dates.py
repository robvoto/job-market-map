"""Normalize source posting-date evidence without losing its precision basis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

_NUMBER_WORDS = {
    "a": 1, "an": 1,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_TOKEN = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_RELATIVE = re.compile(
    rf"\b(?P<count>\d+|(?:{_NUMBER_TOKEN})(?:[\s-]+(?:one|two|three|four|five|six|seven|eight|nine))?)\s*"
    r"(?P<unit>minutes?|mins?|hours?|hrs?|h|days?|d|weeks?|w)\s+ago\b",
    re.IGNORECASE,
)


def _relative_count(value: str) -> int:
    text = value.casefold().replace("-", " ").strip()
    if text.isdigit():
        return int(text)
    parts = text.split()
    if len(parts) == 1:
        return _NUMBER_WORDS[parts[0]]
    if len(parts) == 2 and parts[0] in {
        "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"
    } and parts[1] in {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine"}:
        return _NUMBER_WORDS[parts[0]] + _NUMBER_WORDS[parts[1]]
    raise ValueError(f"unsupported relative count: {value!r}")


@dataclass(frozen=True)
class PostingDate:
    value: str
    basis: str


def _exact(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return date.fromisoformat(text).isoformat()
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Normalize offsets to UTC while keeping any fractional precision. Using
    # timespec="seconds" here would silently discard milliseconds from evidence.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def normalize_posting_date(
    *,
    posted_at: str | None,
    posted_text: str | None,
    captured_at: str,
    search_window_hours: int | None = None,
) -> PostingDate | None:
    """Prefer exact source date, then capture-relative text, then a proven window bound."""
    exact = _exact(posted_at)
    if exact:
        return PostingDate(exact, "source_exact")
    try:
        captured = datetime.fromisoformat(captured_at)
    except ValueError:
        captured = None
    if captured is not None:
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=UTC)
        relative = _RELATIVE.search(str(posted_text or ""))
        if relative:
            count = _relative_count(relative.group("count"))
            unit = relative.group("unit").casefold()
            if unit.startswith(("minute", "min")):
                delta = timedelta(minutes=count)
            elif unit in {"h", "hr", "hrs"} or unit.startswith("hour"):
                delta = timedelta(hours=count)
            elif unit in {"d"} or unit.startswith("day"):
                delta = timedelta(days=count)
            else:
                delta = timedelta(weeks=count)
            return PostingDate(
                (captured.astimezone(UTC) - delta).isoformat(timespec="seconds"),
                "source_relative",
            )
        if search_window_hours is not None and search_window_hours > 0:
            boundary = captured.astimezone(UTC) - timedelta(hours=search_window_hours)
            return PostingDate(boundary.isoformat(timespec="seconds"), "search_window_bound")
    return None
