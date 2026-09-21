import pytest

from collector import db
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


def test_legacy_field_state_backfill_repairs_only_missing_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()
    with db.connect() as conn:
        job_id = conn.execute(
            """INSERT INTO jobs(source,source_job_id,canonical_url,title,workplace_type)
               VALUES('seek','legacy-1','https://seek.test/legacy-1','Legacy role','Hybrid')"""
        ).lastrowid
        db._backfill_field_states(conn)
        states = {
            row["field_name"]: row["state"]
            for row in conn.execute(
                "SELECT field_name,state FROM job_field_states WHERE job_id=?", (job_id,)
            ).fetchall()
        }
        assert len(states) == len(NEUTRAL_FIELDS)
        assert states["title"] == "known"
        assert states["workplace_type"] == "known"
        assert states["salary"] == "unknown"

        conn.execute(
            "DELETE FROM job_field_states WHERE job_id=? AND field_name='workplace_type'",
            (job_id,),
        )
        db._backfill_field_states(conn)
        repaired = conn.execute(
            "SELECT state FROM job_field_states WHERE job_id=? AND field_name='workplace_type'",
            (job_id,),
        ).fetchone()
        assert repaired["state"] == "known"

        traced: list[str] = []
        conn.set_trace_callback(traced.append)
        db._backfill_field_states(conn)
        conn.set_trace_callback(None)
        assert not any(
            "INSERT OR IGNORE INTO job_field_states" in statement
            for statement in traced
        )
