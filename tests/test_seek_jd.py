import pytest

from collector import db
from collector.ingest import ingest_card
from collector.models import CardObservation


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    from collector import seek_jd

    monkeypatch.setattr(seek_jd, "connect", db.connect)
    db.init_db()
    return seek_jd


def test_detail_snapshot_keeps_relative_posted_text_noncanonical_and_strips_personal_match():
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
    detail = parse_seek_detail_snapshot(snap, expected_source_job_id="12345678")

    assert "posted_at" not in detail.facts
    assert "employment_basis" not in detail.facts
    assert detail.facts["employment_type"] == "Contract/Temp"
    assert detail.facts["workplace_type"] == "Hybrid"
    assert (
        detail.facts["classification_text"] == "Information & Communication Technology"
    )
    assert detail.facts["subclassification_text"] == "Business/Systems Analysts"
    assert detail.facts["easy_apply"] is True
    assert "How you match" not in detail.full_description
    assert "Employer questions" not in detail.full_description


def test_fetch_detail_accepts_only_exact_structured_posted_timestamp(monkeypatch):
    from collector import seek_jd
    from collector.browser_broker import BrokerResponse

    monkeypatch.setattr(seek_jd, "navigate", lambda *_a, **_k: None)
    monkeypatch.setattr(
        seek_jd,
        "seek_job_detail",
        lambda *_a, **_k: BrokerResponse(
            result={
                "source_job_id": "12345678",
                "full_description": "A source-backed job description that is deliberately longer than eighty characters so validation succeeds without ambiguity.",
                "posted_at": "2026-09-10T01:02:03.456Z",
                "employment_type": "Contract/Temp",
                "page_url": "https://au.seek.com/job/12345678",
                "human_check": False,
            },
            elapsed_seconds=0.1,
        ),
    )
    detail = seek_jd.fetch_seek_detail(
        1,
        "https://au.seek.com/job/12345678",
        expected_source_job_id="12345678",
    )
    assert detail.facts["posted_at"] == "2026-09-10T01:02:03.456Z"
    assert "employment_basis" not in detail.facts


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
        "2026-09-10",
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
        "seek_cards",
        lambda *_a, **_k: type("Response", (), {"result": [{}]})(),
    )
    monkeypatch.setattr(
        market,
        "parse_seek_dom_cards",
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


def test_enrichment_honours_max_attempts(tmp_path, monkeypatch):
    from collector import db, seek_jd

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(seek_jd, "connect", db.connect)
    db.init_db()
    with db.connect() as conn:
        partition_id = conn.execute(
            """INSERT INTO seek_partitions(
                geography_code,parent_id,level,label,url,status,max_results_threshold,first_seen_at,updated_at
            ) VALUES('ACT',NULL,'state','ACT',
                'https://au.seek.com/jobs/in-Australian-Capital-Territory-ACT?daterange=3',
                'COMPLETE',450,'x','x')"""
        ).lastrowid
        for index in range(12):
            source_id = str(90000000 + index)
            job_id = conn.execute(
                "INSERT INTO jobs(source,source_job_id,canonical_url,identity_key) VALUES('seek',?,?,?)",
                (
                    source_id,
                    f"https://au.seek.com/job/{source_id}",
                    f"seek:id:{source_id}",
                ),
            ).lastrowid
            conn.execute(
                "INSERT INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?,?)",
                (partition_id, job_id, "x"),
            )

    attempts = []

    def fake_fetch(_page_id, url, *, expected_source_job_id):
        attempts.append(expected_source_job_id)
        return seek_jd.SeekFetchedDetail(
            full_description=("Valid JD text " * 10).strip(),
            facts={"source_job_id": expected_source_job_id},
        )

    monkeypatch.setattr(seek_jd, "fetch_seek_detail", fake_fetch)
    monkeypatch.setattr(seek_jd, "update_job_source_facts", lambda *_a, **_k: None)
    monkeypatch.setattr(seek_jd, "store_job_jd_once", lambda *_a, **_k: None)

    result = seek_jd.enrich_seek_coverage_jds(
        page_id=1,
        codes=["ACT"],
        days=3,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
        max_attempts=10,
    )

    assert result.attempted == 10
    assert result.stored == 10
    assert len(attempts) == 10
    assert result.remaining == 2


def test_security_job_text_is_not_human_check():
    from collector.seek_jd import parse_seek_detail_snapshot

    snap = {
        "url": "https://au.seek.com/job/94535996",
        "text": """Security Clearance Administration Officer
Airservices Australia
Canberra ACT
Contract/Temp
Save
We coordinate personnel security checks, security clearances and background checking processes for employees and contractors. This is a legitimate job description and is deliberately longer than eighty characters.
Employer questions
""",
        "elements": [],
    }
    detail = parse_seek_detail_snapshot(snap, expected_source_job_id="94535996")
    assert "security checks" in detail.full_description


def test_unreadable_detail_is_not_treated_as_human_verification(monkeypatch):
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
                    "page_url": "https://au.seek.com/job/94511500",
                    "source_job_id": "94511500",
                    "full_description": "",
                    "human_check": False,
                }
            },
        )(),
    )

    start = [0.0]
    monkeypatch.setattr(seek_jd.time, "monotonic", lambda: start[0])
    monkeypatch.setattr(
        seek_jd.time, "sleep", lambda seconds: start.__setitem__(0, start[0] + seconds)
    )

    with pytest.raises(seek_jd.SeekJDFetchError, match="implausibly short JD"):
        seek_jd.fetch_seek_detail(
            1,
            "https://au.seek.com/job/94511500",
            expected_source_job_id="94511500",
            timeout_seconds=1.0,
            human_wait_seconds=900.0,
        )
