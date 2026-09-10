from sources.seek import seek_refinement_links, seek_result_count
from sources.seek_market_map import _page_url, state_url


def test_seek_result_count_parses_whole_state_and_classification():
    assert seek_result_count("12,048 jobs in New South Wales\nSkip") == 12048
    assert (
        seek_result_count(
            "668 information & communication technology jobs in New South Wales\nSkip"
        )
        == 668
    )
    assert seek_result_count("914 jobs in Australian Capital Territory\nSkip") == 914


def test_state_partition_discovers_top_level_classifications_only():
    snap = {
        "elements": [
            {
                "text": "Engineering",
                "href": "https://au.seek.com/jobs-in-engineering/in-New-South-Wales-NSW?daterange=7",
            },
            {
                "text": "ICT",
                "href": "https://au.seek.com/jobs-in-information-communication-technology/in-New-South-Wales-NSW?daterange=7",
            },
            {
                "text": "Architects",
                "href": "https://au.seek.com/jobs-in-information-communication-technology/architects/in-New-South-Wales-NSW?daterange=7",
            },
        ]
    }
    rows = seek_refinement_links(
        snap,
        state_slug="New-South-Wales-NSW",
        level="state",
        current_url="https://au.seek.com/jobs/in-New-South-Wales-NSW",
    )
    assert [r["label"] for r in rows] == ["Engineering", "ICT"]
    assert all(r["level"] == "classification" for r in rows)


def test_classification_partition_discovers_its_subclassifications_only():
    snap = {
        "elements": [
            {
                "text": "Consultants",
                "href": "https://au.seek.com/jobs-in-information-communication-technology/consultants/in-New-South-Wales-NSW?daterange=7",
            },
            {
                "text": "Engineering",
                "href": "https://au.seek.com/jobs-in-engineering/in-New-South-Wales-NSW?daterange=7",
            },
        ]
    }
    rows = seek_refinement_links(
        snap,
        state_slug="New-South-Wales-NSW",
        level="classification",
        current_url="https://au.seek.com/jobs-in-information-communication-technology/in-New-South-Wales-NSW?daterange=7",
    )
    assert rows == [
        {
            "level": "subclassification",
            "label": "Consultants",
            "url": snap["elements"][0]["href"],
        }
    ]


def test_subclassification_partition_discovers_four_work_types():
    base = "https://au.seek.com/jobs-in-information-communication-technology/consultants/in-New-South-Wales-NSW"
    snap = {
        "elements": [
            {"text": "Full time", "href": base + "/full-time?daterange=7"},
            {"text": "Part time", "href": base + "/part-time?daterange=7"},
            {"text": "Contract/Temp", "href": base + "/contract-temp?daterange=7"},
            {"text": "Casual/Vacation", "href": base + "/casual-vacation?daterange=7"},
        ]
    }
    rows = seek_refinement_links(
        snap,
        state_slug="New-South-Wales-NSW",
        level="subclassification",
        current_url=base + "?daterange=7",
    )
    assert len(rows) == 4
    assert all(r["level"] == "work_type" for r in rows)


def test_page_url_preserves_partition_filters():
    url = "https://au.seek.com/jobs-in-engineering/in-Queensland-QLD/full-time?daterange=7&sortmode=ListedDate"
    assert _page_url(url, 3).endswith("daterange=7&sortmode=ListedDate&page=3")
    assert "full-time" in _page_url(url, 3)


def test_verified_state_urls():
    assert "New-South-Wales-NSW" in state_url(
        {"seek_state_slug": "New-South-Wales-NSW"}, 7
    )
    assert "Australian-Capital-Territory-ACT" in state_url(
        {"seek_state_slug": "Australian-Capital-Territory-ACT"}, 7
    )
    assert "Queensland-QLD" in state_url({"seek_state_slug": "Queensland-QLD"}, 7)


def test_parent_union_deduplicates_same_job_across_child_partitions(
    tmp_path, monkeypatch
):
    import sources.seek_market_map as market
    from collector import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(market, "connect", db.connect)
    monkeypatch.setattr(market, "init_db", db.init_db)
    db.init_db()
    with db.connect() as conn:
        parent = conn.execute(
            """INSERT INTO seek_partitions(geography_code,parent_id,level,label,url,status,reported_results,max_results_threshold,first_seen_at,updated_at) VALUES('NSW',NULL,'classification','ICT','https://x/ict','INCOMPLETE_OVERSIZE',3,450,'x','x')"""
        ).lastrowid
        c1 = conn.execute(
            """INSERT INTO seek_partitions(geography_code,parent_id,level,label,url,status,max_results_threshold,first_seen_at,updated_at) VALUES('NSW',?,'subclassification','A','https://x/a','COMPLETE',450,'x','x')""",
            (parent,),
        ).lastrowid
        c2 = conn.execute(
            """INSERT INTO seek_partitions(geography_code,parent_id,level,label,url,status,max_results_threshold,first_seen_at,updated_at) VALUES('NSW',?,'subclassification','B','https://x/b','COMPLETE',450,'x','x')""",
            (parent,),
        ).lastrowid
        ids = []
        for i in range(3):
            ids.append(
                conn.execute(
                    "INSERT INTO jobs(source,source_job_id,canonical_url,first_seen_at,last_seen_at) VALUES('seek',?,?, 'x','x')",
                    (str(i), f"https://job/{i}"),
                ).lastrowid
            )
        for job_id in (ids[0], ids[1]):
            conn.execute(
                "INSERT INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?, 'x')",
                (c1, job_id),
            )
        for job_id in (ids[1], ids[2]):
            conn.execute(
                "INSERT INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?, 'x')",
                (c2, job_id),
            )
    status, count, children = market._aggregate_parent(parent, reported=3, tolerance=0)
    assert status == "COMPLETE_BY_PARTITION"
    assert count == 3
    assert children == 2


def test_parent_remains_incomplete_if_any_child_incomplete(tmp_path, monkeypatch):
    import sources.seek_market_map as market
    from collector import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(market, "connect", db.connect)
    monkeypatch.setattr(market, "init_db", db.init_db)
    db.init_db()
    with db.connect() as conn:
        parent = conn.execute(
            """INSERT INTO seek_partitions(geography_code,parent_id,level,label,url,status,reported_results,max_results_threshold,first_seen_at,updated_at) VALUES('QLD',NULL,'state','QLD','https://x/qld','INCOMPLETE_OVERSIZE',1,450,'x','x')"""
        ).lastrowid
        child = conn.execute(
            """INSERT INTO seek_partitions(geography_code,parent_id,level,label,url,status,max_results_threshold,first_seen_at,updated_at) VALUES('QLD',?,'classification','ICT','https://x/qld/ict','INCOMPLETE_COUNT_MISMATCH',450,'x','x')""",
            (parent,),
        ).lastrowid
        job_id = conn.execute(
            "INSERT INTO jobs(source,source_job_id,canonical_url,first_seen_at,last_seen_at) VALUES('seek','1','https://job/1','x','x')"
        ).lastrowid
        conn.execute(
            "INSERT INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?, 'x')",
            (child, job_id),
        )
    status, count, children = market._aggregate_parent(parent, reported=1, tolerance=0)
    assert status == "INCOMPLETE_CHILD_COVERAGE"
    assert count == 1
    assert children == 1


def test_oversize_work_type_is_explicitly_unsplittable_not_complete(
    tmp_path, monkeypatch
):
    import sources.seek_market_map as market
    from collector import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(market, "connect", db.connect)
    monkeypatch.setattr(market, "init_db", db.init_db)
    monkeypatch.setattr(market, "navigate", lambda *a, **k: None)
    monkeypatch.setattr(market.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        market,
        "_wait_snapshot",
        lambda *_a, **_k: {"text": "600 jobs in New South Wales", "elements": []},
    )
    monkeypatch.setattr(
        market,
        "get_setting",
        lambda key: 0 if key == "collection.seek_page_load_seconds" else 450,
    )
    db.init_db()
    pid = market._ensure_partition(
        geography_code="NSW",
        parent_id=None,
        level="work_type",
        label="Full time",
        url="https://x/full-time",
        threshold=450,
    )
    market._process_partition(
        1,
        geography={"code": "NSW", "seek_state_slug": "New-South-Wales-NSW"},
        partition_id=pid,
        level="work_type",
        label="Full time",
        url="https://x/full-time",
        threshold=450,
        tolerance=0,
    )
    with db.connect() as conn:
        status = conn.execute(
            "SELECT status FROM seek_partitions WHERE id=?", (pid,)
        ).fetchone()[0]
    assert status == "INCOMPLETE_OVERSIZE_UNSPLITTABLE"


def test_zero_result_partition_completes_without_card_parser(tmp_path, monkeypatch):
    import sources.seek_market_map as market
    from collector import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(market, "connect", db.connect)
    monkeypatch.setattr(market, "init_db", db.init_db)
    monkeypatch.setattr(market, "navigate", lambda *a, **k: None)
    monkeypatch.setattr(market.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        market,
        "_wait_snapshot",
        lambda *_a, **_k: {"text": "0 jobs in New South Wales", "elements": []},
    )
    monkeypatch.setattr(
        market,
        "get_setting",
        lambda key: 0 if key == "collection.seek_page_load_seconds" else 450,
    )
    db.init_db()
    pid = market._ensure_partition(
        geography_code="NSW",
        parent_id=None,
        level="classification",
        label="Empty",
        url="https://x/empty",
        threshold=450,
    )
    market._process_partition(
        1,
        geography={"code": "NSW", "seek_state_slug": "New-South-Wales-NSW"},
        partition_id=pid,
        level="classification",
        label="Empty",
        url="https://x/empty",
        threshold=450,
        tolerance=0,
    )
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status,collected_unique_jobs FROM seek_partitions WHERE id=?",
            (pid,),
        ).fetchone()
    assert tuple(row) == ("COMPLETE", 0)
