import pytest

from collector.run_lock import (
    CollectionAlreadyRunning,
    collection_run_lock,
    lock_status,
)


def test_collection_lock_blocks_second_process_path(tmp_path):
    path = tmp_path / "collection.lock"
    assert lock_status(path)["active"] is False
    with collection_run_lock("manual", path=path):
        status = lock_status(path)
        assert status["active"] is True
        assert status["metadata"]["trigger"] == "manual"
        with pytest.raises(CollectionAlreadyRunning), collection_run_lock("scheduled", path=path):
            pass
    assert lock_status(path)["active"] is False


def test_unlocked_status_does_not_mean_stale_metadata_is_active(tmp_path):
    path = tmp_path / "collection.lock"
    with collection_run_lock("manual", path=path):
        pass
    status = lock_status(path)
    assert status["active"] is False
    assert status["metadata"]["trigger"] == "manual"
