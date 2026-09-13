from collector import db


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    import collector.seek_cycle as cycle

    monkeypatch.setattr(cycle, "connect", db.connect)
    monkeypatch.setattr(cycle, "init_db", db.init_db)
    db.init_db()
    return cycle


def test_completed_coverage_is_snapshotted_then_reset_without_deleting_jobs(
    tmp_path, monkeypatch
):
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
        root = conn.execute(
            "SELECT * FROM seek_partitions WHERE id=?", (root_id,)
        ).fetchone()
        assert root["status"] == "PENDING"
        assert root["reported_results"] is None
        assert (
            conn.execute("SELECT COUNT(*) FROM seek_partition_jobs").fetchone()[0] == 0
        )
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        history = conn.execute("SELECT * FROM seek_coverage_history").fetchone()
        assert history["root_status"] == "COMPLETE_BY_PARTITION"
        assert history["covered_unique_jobs"] == 1


def test_rollover_deletes_stale_child_partitions_and_keeps_root(tmp_path, monkeypatch):
    """JMM-015: yesterday's classification/work-type split must not survive rollover.

    A prior cycle that had to split NSW/QLD into many child partitions leaves those
    children behind if rollover only resets status to PENDING. The next cycle may
    resume the reused root without ever revisiting those children (see the
    end-to-end regression below), so they must be deleted, not reset in place.
    """
    cycle = _wire(tmp_path, monkeypatch)
    with db.connect() as conn:
        root_id = conn.execute(
            """INSERT INTO seek_partitions(geography_code,parent_id,level,label,url,status,reported_results,collected_unique_jobs,max_results_threshold,first_seen_at,updated_at,completed_at)
               VALUES('NSW',NULL,'state','NSW','https://seek.test/nsw','INCOMPLETE_CHILD_COVERAGE',2381,2136,450,'x','x',NULL)"""
        ).lastrowid
        child_ids = [
            conn.execute(
                """INSERT INTO seek_partitions(geography_code,parent_id,level,label,url,status,reported_results,collected_unique_jobs,max_results_threshold,first_seen_at,updated_at)
                   VALUES('NSW',?,'classification',?,?,?,100,100,450,'x','x')""",
                (root_id, f"Classification {i}", f"https://seek.test/nsw/c{i}", "COMPLETE" if i else "PENDING"),
            ).lastrowid
            for i in range(3)
        ]
        conn.execute(
            "INSERT INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?,?)",
            (
                child_ids[1],
                conn.execute(
                    "INSERT INTO jobs(source,source_job_id,canonical_url,geography_code) VALUES('seek','1','https://seek.test/1','NSW')"
                ).lastrowid,
                "x",
            ),
        )

    cycle.snapshot_and_reset_coverage(["NSW"])

    with db.connect() as conn:
        remaining = [dict(row) for row in conn.execute("SELECT * FROM seek_partitions")]
        assert len(remaining) == 1
        root = remaining[0]
        assert root["id"] == root_id
        assert root["status"] == "PENDING"
        assert root["reported_results"] is None
        assert root["collected_unique_jobs"] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM seek_partition_jobs").fetchone()[0] == 0
        )
        history = conn.execute("SELECT * FROM seek_coverage_history").fetchone()
        assert history["incomplete_partitions"] == 2


def test_rollover_then_below_threshold_run_leaves_no_stale_partitions(
    tmp_path, monkeypatch
):
    """JMM-015 regression: yesterday required classification partitions, today's
    state total is below the split threshold. The root must resolve to COMPLETE
    with zero incomplete partitions for that geography, not COMPLETE alongside
    leftover stale children.
    """
    cycle = _wire(tmp_path, monkeypatch)
    import sources.seek_market_map as market

    monkeypatch.setattr(market, "connect", db.connect)
    monkeypatch.setattr(market, "init_db", db.init_db)
    monkeypatch.setattr(market, "navigate", lambda *_a, **_k: None)
    monkeypatch.setattr(market.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(
        market,
        "_wait_snapshot",
        lambda *_a, **_k: {"text": "0 jobs in Queensland", "elements": []},
    )
    monkeypatch.setattr(
        market,
        "get_setting",
        lambda key: 0 if key == "collection.seek_page_load_seconds" else 450,
    )

    with db.connect() as conn:
        root_id = conn.execute(
            """INSERT INTO seek_partitions(geography_code,parent_id,level,label,url,status,reported_results,collected_unique_jobs,max_results_threshold,first_seen_at,updated_at)
               VALUES('QLD',NULL,'state','QLD','https://au.seek.com/jobs/in-Queensland-QLD?daterange=1','INCOMPLETE_CHILD_COVERAGE',2146,1871,450,'x','x')"""
        ).lastrowid
        for i in range(30):
            conn.execute(
                """INSERT INTO seek_partitions(geography_code,parent_id,level,label,url,status,max_results_threshold,first_seen_at,updated_at)
                   VALUES('QLD',?,'classification',?,?,'PENDING',450,'x','x')""",
                (root_id, f"Classification {i}", f"https://au.seek.com/jobs-in-c{i}/in-Queensland-QLD?daterange=1"),
            )

    cycle.snapshot_and_reset_coverage(["QLD"])

    market._process_partition(
        1,
        geography={"code": "QLD", "seek_state_slug": "Queensland-QLD"},
        partition_id=root_id,
        level="state",
        label="QLD",
        url="https://au.seek.com/jobs/in-Queensland-QLD?daterange=1",
        threshold=450,
        tolerance=0,
        budget=market.PartitionBudget(max_partitions=1),
        resume=True,
    )

    with db.connect() as conn:
        root = dict(
            conn.execute(
                "SELECT * FROM seek_partitions WHERE id=?", (root_id,)
            ).fetchone()
        )
        incomplete = conn.execute(
            "SELECT COUNT(*) FROM seek_partitions WHERE geography_code='QLD' AND status NOT LIKE 'COMPLETE%'"
        ).fetchone()[0]
    assert root["status"] == "COMPLETE"
    assert incomplete == 0


def test_cycle_invokes_progress_callback_after_partition_progress(monkeypatch):
    import collector.seek_cycle as cycle
    from sources.seek_market_map import MarketMapResult

    completion_checks = iter([False, True])
    monkeypatch.setattr(
        cycle, "all_states_complete", lambda _codes: next(completion_checks)
    )
    market_result = MarketMapResult(
        geography_code="ACT",
        status="COMPLETE",
        root_partition_id=1,
        reported_results=1,
        covered_unique_jobs=1,
        incomplete_partitions=0,
        partitions_processed=1,
    )
    monkeypatch.setattr(cycle, "collect_seek_state", lambda *_a, **_k: market_result)
    callbacks = []

    result = cycle.run_seek_cycle(
        page_id=1,
        codes=["ACT"],
        days=3,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
        after_progress=lambda progress: callbacks.append(progress.geography_code),
    )

    assert result.status == "COMPLETE"
    assert callbacks == ["ACT"]



def test_cycle_returns_stopped_when_partition_wait_is_interrupted(monkeypatch):
    import collector.seek_cycle as cycle

    monkeypatch.setattr(cycle, "all_states_complete", lambda _codes: False)
    monkeypatch.setattr(
        cycle,
        "collect_seek_state",
        lambda *_a, **_k: (_ for _ in ()).throw(
            InterruptedError("SEEK collection stopped")
        ),
    )

    result = cycle.run_seek_cycle(
        page_id=1,
        codes=["ACT"],
        days=1,
        should_stop=lambda: True,
        deadline_reached=lambda: False,
    )

    assert result.status == "STOPPED"
    assert result.partitions_processed == 0


def test_cycle_returns_blocked_human_without_failing_run(monkeypatch):
    import collector.seek_cycle as cycle
    from sources.seek_market_map import SeekHumanCheckRequired

    monkeypatch.setattr(cycle, "all_states_complete", lambda _codes: False)
    monkeypatch.setattr(
        cycle,
        "collect_seek_state",
        lambda *_a, **_k: (_ for _ in ()).throw(
            SeekHumanCheckRequired("SEEK human-check wait expired")
        ),
    )

    result = cycle.run_seek_cycle(
        page_id=1,
        codes=["ACT"],
        days=3,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
    )

    assert result.status == "BLOCKED_HUMAN"
    assert result.partitions_processed == 0
