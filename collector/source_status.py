"""Shared neutral source-status semantics for JMM collection and feeds."""

from __future__ import annotations

# Exact terminal statuses emitted from verified source evidence by supported
# adapters. They are product semantics, not an operator-tunable retention rule.
TERMINAL_SOURCE_STATUSES = frozenset(
    {
        "no_longer_accepting_applications",
        "no_longer_advertised",
        "not_found",
    }
)


def source_status_is_active_sql(column: str) -> str:
    """Return the SQL predicate that keeps only actionable source postings."""
    values = ", ".join(repr(status) for status in sorted(TERMINAL_SOURCE_STATUSES))
    return f"COALESCE({column}, '') NOT IN ({values})"
