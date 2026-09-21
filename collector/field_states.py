"""Neutral field-state and source-capability semantics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

FIELD_STATES = ("known", "not_present", "unknown", "not_applicable")
NEUTRAL_FIELDS = ("title", "company", "location", "geography_code", "posted_at", "classification", "subclassification", "employment_type", "workplace_type", "apply_method", "salary", "description")
FIELD_COLUMNS = {"title": "title", "company": "employer", "location": "location", "geography_code": "geography_code", "posted_at": "posted_at", "classification": "classification_text", "subclassification": "subclassification_text", "employment_type": "employment_type", "workplace_type": "workplace_type", "apply_method": "apply_method", "salary": "salary_text", "description": "full_description"}
SOURCE_CAPABILITIES: dict[str, dict[str, str]] = {
    "seek": {field: "supported" for field in NEUTRAL_FIELDS},
    "linkedin": {"title": "supported", "company": "supported", "location": "supported", "geography_code": "unknown", "posted_at": "supported", "classification": "unknown", "subclassification": "unknown", "employment_type": "supported", "workplace_type": "supported", "apply_method": "supported", "salary": "supported", "description": "supported"},
    "apsjobs": {field: "unknown" for field in NEUTRAL_FIELDS},
}

def capabilities_for_source(source: str) -> dict[str, str]:
    """Return every neutral field, safely defaulting future sources to unknown."""
    return dict(SOURCE_CAPABILITIES.get(str(source).strip().casefold(), {field: "unknown" for field in NEUTRAL_FIELDS}))

def validate_field_state(state: str) -> str:
    value = str(state).strip().casefold()
    if value not in FIELD_STATES:
        raise ValueError(f"invalid field state: {state!r}")
    return value

def _is_present(value: Any) -> bool:
    return True if isinstance(value, bool) else bool(value is not None and str(value).strip())

def states_for_observation(observation: Any) -> dict[str, str]:
    """Derive safe states without treating a blank as an omission."""
    explicit = getattr(observation, "field_states", None) or {}
    unknown_fields = set(explicit) - set(NEUTRAL_FIELDS)
    if unknown_fields:
        raise ValueError(f"unsupported field-state names: {sorted(unknown_fields)}")
    return {field: (validate_field_state(explicit[field]) if field in explicit else ("known" if _is_present(getattr(observation, FIELD_COLUMNS[field], None)) else "unknown")) for field in NEUTRAL_FIELDS}

def normalise_field_states(states: Mapping[str, str] | None) -> dict[str, str]:
    values = dict(states or {})
    unknown_fields = set(values) - set(NEUTRAL_FIELDS)
    if unknown_fields:
        raise ValueError(f"unsupported field-state names: {sorted(unknown_fields)}")
    return {field: validate_field_state(values.get(field, "unknown")) for field in NEUTRAL_FIELDS}
