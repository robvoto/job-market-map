from collector import db
from collector.ingest import ingest_card
from collector.models import CardObservation


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import seek_jd

    monkeypatch.setattr(seek_jd, "connect", db.connect)
    db.init_db()
    return seek_jd


def test_detail_snapshot_calculates_date_only_and_strips_personal_match():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from collector.seek_jd import parse_seek_detail_snapshot

    snap = {
        "url": "https://au.seek.com/job/12345678",
        "text": """Skip to content
SEEK
Business Systems Analyst
Example Co
Sydney NSW (Hybrid)
Business/Systems Analysts (Information & Communication Technology)
Contract/Temp
Salary undisclosed
Posted 1d ago
Quick apply
Save
How you match
5 skills and credentials match your profile
SQL
Troubleshooting
Process Improvement
Business Applications
+1 more
This is the complete source-backed SEEK job description and it is comfortably longer than eighty characters for validation.
The actual employer advertisement continues here with useful role information.
Employer questions
Your application will include questions.
""",
        "elements": [{"text": "Quick apply", "ariaLabel": "Apply for role"}],
    }
    detail = parse_seek_detail_snapshot(
        snap,
        expected_source_job_id="12345678",
        reference=datetime(2026, 9, 11, 0, 30, tzinfo=ZoneInfo("Australia/Sydney")),
    )

    assert detail.facts["posted_at"] == "2026-09-10"
    assert detail.facts["employment_type"] == "Contract/Temp"
    assert detail.facts["employment_basis"] == "Contract/Temp"
    assert detail.facts["workplace_type"] == "Hybrid"
    assert (
        detail.facts["classification_text"] == "Information & Communication Technology"
    )
    assert detail.facts["subclassification_text"] == "Business/Systems Analysts"
    assert detail.facts["easy_apply"] is True
    assert "How you match" not in detail.full_description
    assert "Employer questions" not in detail.full_description


def test_posted_date_uses_relative_duration_across_midnight():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from collector.seek_jd import derive_posted_at

    ref = datetime(2026, 9, 11, 0, 30, tzinfo=ZoneInfo("Australia/Sydney"))
    assert derive_posted_at("Posted 1d ago", reference=ref) == "2026-09-10"
    assert derive_posted_at("Posted 2h ago", reference=ref) == "2026-09-10"
    assert derive_posted_at("Listed four hours ago", reference=ref) == "2026-09-10"
    assert derive_posted_at("Listed ten minutes ago", reference=ref) == "2026-09-11"


def test_employment_basis_requires_explicit_job_context():
    from collector.seek_jd import _employment_basis

    assert (
        _employment_basis("Full time", "This is a permanent role based in Canberra.")
        == "Permanent"
    )
    assert (
        _employment_basis("Full time", "This is a 12 month contract role in Sydney.")
        == "Contract"
    )
    assert (
        _employment_basis(
            "Full time",
            "Applicants must be permanent residents. Manage contract updates.",
        )
        is None
    )


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
                "posted_at": "2026-09-10",
                "employment_type": "Contract/Temp",
                "employment_basis": "Contract/Temp",
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
            "SELECT posted_at,employment_type,employment_basis,workplace_type,location,salary_text FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    assert tuple(job) == (
        "2026-09-10",
        "Contract/Temp",
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
