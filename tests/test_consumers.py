import pytest

from collector import db


def _wire(tmp_path, monkeypatch):
    from collector import consumers

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(consumers, "connect", db.connect)
    monkeypatch.setattr(consumers, "init_db", db.init_db)
    return consumers


def test_independent_consumer_checkpoints(tmp_path, monkeypatch):
    consumers = _wire(tmp_path, monkeypatch)
    consumers.advance_checkpoint("job-hunter", 100)
    consumers.advance_checkpoint("plan-z", 25)
    assert consumers.get_checkpoint("job-hunter")["last_job_id"] == 100
    assert consumers.get_checkpoint("plan-z")["last_job_id"] == 25


def test_checkpoint_cannot_move_backwards(tmp_path, monkeypatch):
    consumers = _wire(tmp_path, monkeypatch)
    consumers.advance_checkpoint("reset-edge", 50)
    with pytest.raises(ValueError, match="backwards"):
        consumers.advance_checkpoint("reset-edge", 49)
