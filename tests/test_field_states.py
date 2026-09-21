import pytest

from collector.field_states import (
    FIELD_STATES,
    NEUTRAL_FIELDS,
    capabilities_for_source,
    states_for_observation,
)
from collector.models import CardObservation


def test_blank_is_unknown_and_explicit_states_are_preserved():
    observation = CardObservation(source="seek", canonical_url="https://seek.test/1", easy_apply=False)
    states = states_for_observation(observation)
    assert states["title"] == "unknown"
    assert states["description"] == "unknown"
    observation.field_states = {"workplace_type": "not_present"}
    assert states_for_observation(observation)["workplace_type"] == "not_present"
    observation.field_states = {"workplace_type": "not_applicable"}
    assert states_for_observation(observation)["workplace_type"] == "not_applicable"


def test_capabilities_cover_current_and_future_sources():
    assert FIELD_STATES == ("known", "not_present", "unknown", "not_applicable")
    for source in ("seek", "linkedin", "apsjobs", "new-board"):
        assert set(capabilities_for_source(source)) == set(NEUTRAL_FIELDS)
    assert capabilities_for_source("apsjobs")["salary"] == "unknown"
    assert capabilities_for_source("new-board")["description"] == "unknown"


def test_invalid_state_is_rejected():
    with pytest.raises(ValueError, match="invalid field state"):
        states_for_observation(CardObservation(source="seek", canonical_url="https://seek.test/1", field_states={"title": "false"}))
