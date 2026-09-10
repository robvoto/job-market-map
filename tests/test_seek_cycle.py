from collector import db


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    import collector.seek_cycle as cycle

    monkeypatch.setattr(cycle, "connect", db.connect)
    monkeypatch.setattr(cycle, "init_db", db.init_db)
    db.init_db()
    return cycle


def test_completed_coverage_is_snapshotted_then_reset_without_deleting_jobs(tmp_path, monkeypatch):
    cycle = _wire(tmp_path, monkeypatch)
    with db.connect() as conn:
        job_id = conn.execute(
            """INSERT INTO jobs(source,source_job_id,canonical_url,geography_code)
               VALUES('seek','1','https://seek.test/1','ACT')"""
        ).lastrowid
        root_id = conn.execute(
            """INSERT INTO seek_partitions(geography_code,parent_id,level,label,url,status,reported_results,collected_unique_jobs,max_results_threshold,first_seen_at,updated_at,completed_at)
               VALUES('ACT',NULL,'state','ACT','https://seek.test/act','COMPLETE_BY_PARTITION',1,1,450,'x','x','x')"""
        ).lastrowid
        conn.execute(
            "INSERT INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?,?)",
            (root_id, job_id, "x"),
        )

    assert cycle.all_states_complete(["ACT"]) is True
    cycle.snapshot_and_reset_coverage(["ACT"])

    with db.connect() as conn:
        root = conn.execute("SELECT * FROM seek_partitions WHERE id=?", (root_id,)).fetchone()
        assert root["status"] == "PENDING"
        assert root["reported_results"] is None
        assert conn.execute("SELECT COUNT(*) FROM seek_partition_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        history = conn.execute("SELECT * FROM seek_coverage_history").fetchone()
        assert history["root_status"] == "COMPLETE_BY_PARTITION"
        assert history["covered_unique_jobs"] == 1
