from collector import db
from collector.ingest import ingest_card
from collector.models import CardObservation


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import seek_jd

    monkeypatch.setattr(seek_jd, "connect", db.connect)
    db.init_db()
    return seek_jd


def test_fetch_detail_keeps_exact_source_timestamp_and_ignores_noncanonical_fields(monkeypatch):
    from collector import seek_jd

    monkeypatch.setattr(seek_jd, "navigate", lambda *_a, **_k: None)
    monkeypatch.setattr(
        seek_jd,
        "seek_job_detail",
        lambda *_a, **_k: type(
            "Response",
            (),
            {
                "result": {
                    "page_url": "https://au.seek.com/job/12345678",
                    "source_job_id": "12345678",
                    "title": "Business Systems Analyst",
                    "employer": "Example Co",
                    "location": "Sydney NSW",
                    "employment_type": "Contract/Temp",
                    "workplace_type": "Hybrid",
                    "salary_text": "$900 - $1000 p.d.",
                    "posted_at": "2026-09-09T01:02:03.456Z",
                    "posted_text": "Listed one day ago",
                    "employment_basis": "Contract",
                    "full_description": "This is the complete source-backed SEEK job description and it is comfortably longer than eighty characters for validation.",
                    "human_check": False,
                }
            },
        )(),
    )

    detail = seek_jd.fetch_seek_detail(
        1,
        "https://au.seek.com/job/12345678",
        expected_source_job_id="12345678",
    )
    assert detail.facts["posted_at"] == "2026-09-09T01:02:03.456Z"
    assert detail.facts["employment_type"] == "Contract/Temp"
    assert "posted_text" not in detail.facts
    assert "employment_basis" not in detail.facts
    assert len(detail.full_description) > 80


def test_enrichment_fetches_once_then_permanently_skips_same_job(tmp_path, monkeypatch):
    seek_jd = _wire(tmp_path, monkeypatch)
    obs = CardObservation(
        source="seek",
        source_job_id="12345678",
        canonical_url="https://au.seek.com/job/12345678",
        title="Business Analyst",
        geography_code="NSW",
        captured_at="2026-09-10T12:00:00+00:00",
    )
    job_id = ingest_card(obs).job_id
    with db.connect() as conn:
        partition_id = conn.execute(
            """INSERT INTO seek_partitions(
                geography_code,parent_id,level,label,url,status,reported_results,collected_unique_jobs,
                max_results_threshold,first_seen_at,updated_at,completed_at
            ) VALUES('NSW',NULL,'state','NSW',
                'https://au.seek.com/jobs/in-New-South-Wales-NSW?daterange=3&sortmode=ListedDate',
                'COMPLETE',1,1,450,'x','x','x')"""
        ).lastrowid
        conn.execute(
            "INSERT INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?,?)",
            (partition_id, job_id, "x"),
        )

    calls = {"count": 0}

    def fake_fetch(_page_id, _url, **_kwargs):
        calls["count"] += 1
        return seek_jd.SeekFetchedDetail(
            full_description="Full source-backed job description captured from SEEK for this role.",
            facts={
                "source_job_id": "12345678",
                "posted_at": "2026-09-10T01:02:03.456Z",
                "employment_type": "Contract/Temp",
                "workplace_type": "Hybrid",
                "location": "Sydney NSW",
                "salary_text": "$900 - $1000 p.d.",
            },
        )

    monkeypatch.setattr(seek_jd, "fetch_seek_detail", fake_fetch)
    first = seek_jd.enrich_seek_coverage_jds(
        page_id=1,
        codes=["NSW"],
        days=3,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
    )
    assert first.stored == 1
    assert first.remaining == 0
    assert calls["count"] == 1
    with db.connect() as conn:
        job = conn.execute(
            "SELECT posted_at,employment_type,workplace_type,location,salary_text FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    assert tuple(job) == (
        "2026-09-10T01:02:03.456Z",
        "Contract/Temp",
        "Hybrid",
        "Sydney NSW",
        "$900 - $1000 p.d.",
    )

    def should_never_fetch(*_args, **_kwargs):
        raise AssertionError("cached JD was fetched again")

    monkeypatch.setattr(seek_jd, "fetch_seek_detail", should_never_fetch)
    second = seek_jd.enrich_seek_coverage_jds(
        page_id=1,
        codes=["NSW"],
        days=3,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
    )
    assert second.cached == 1
    assert second.attempted == 0
    assert second.remaining == 0


def test_known_seek_card_is_not_reingested_on_daily_coverage(tmp_path, monkeypatch):
    import sources.seek_market_map as market

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(market, "connect", db.connect)
    db.init_db()
    existing = ingest_card(
        CardObservation(
            source="seek",
            source_job_id="77777777",
            canonical_url="https://au.seek.com/job/77777777",
            title="Existing role",
            geography_code="NSW",
            captured_at="2026-09-09T10:00:00+00:00",
        )
    )
    with db.connect() as conn:
        partition_id = conn.execute(
            """INSERT INTO seek_partitions(
                geography_code,parent_id,level,label,url,status,max_results_threshold,first_seen_at,updated_at
            ) VALUES('NSW',NULL,'work_type','All',
                'https://au.seek.com/jobs/in-New-South-Wales-NSW?daterange=1&sortmode=ListedDate',
                'PENDING',450,'x','x')"""
        ).lastrowid
        before_captures = conn.execute("SELECT COUNT(*) FROM card_captures").fetchone()[
            0
        ]

    monkeypatch.setattr(market, "navigate", lambda *_a, **_k: None)
    monkeypatch.setattr(market.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(
        market,
        "get_setting",
        lambda key: 0 if key == "collection.seek_page_load_seconds" else 10,
    )
    monkeypatch.setattr(
        market,
        "_wait_snapshot",
        lambda *_a, **_k: {"text": "1 job in New South Wales"},
    )
    monkeypatch.setattr(
        market,
        "parse_seek_snapshot",
        lambda *_a, **_k: [
            CardObservation(
                source="seek",
                source_job_id="77777777",
                canonical_url="https://au.seek.com/job/77777777",
                title="Existing role",
                geography_code="NSW",
                captured_at="2026-09-10T10:00:00+00:00",
            )
        ],
    )
    monkeypatch.setattr(
        market,
        "ingest_card",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("known job re-ingested")
        ),
    )

    status, count = market._collect_leaf(
        1,
        partition_id=partition_id,
        url="https://au.seek.com/jobs/in-New-South-Wales-NSW?daterange=1&sortmode=ListedDate",
        geography_code="NSW",
        reported=1,
        tolerance=0,
    )
    assert (status, count) == ("COMPLETE", 1)
    with db.connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM card_captures").fetchone()[0]
            == before_captures
        )
        membership = conn.execute(
            "SELECT job_id FROM seek_partition_jobs WHERE partition_id=?",
            (partition_id,),
        ).fetchone()
    assert membership[0] == existing.job_id
