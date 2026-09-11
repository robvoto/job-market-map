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
            canonical_url="https://www.seek.com.au/job/77777777",
            title="Existing role",
            apply_method="quick_apply",
            easy_apply=True,
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


def test_changed_known_seek_card_is_refreshed_once_on_daily_coverage(tmp_path, monkeypatch):
    import sources.seek_market_map as market

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(market, "connect", db.connect)
    db.init_db()
    existing = ingest_card(
        CardObservation(
            source="seek",
            source_job_id="77777778",
            canonical_url="https://au.seek.com/job/77777778",
            title="Existing role",
            employer="Example Pty Ltd",
            salary_text="$100k",
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
        before_captures = conn.execute("SELECT COUNT(*) FROM card_captures").fetchone()[0]

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
                source_job_id="77777778",
                canonical_url="https://au.seek.com/job/77777778",
                title="Existing role",
                employer="Example Pty Ltd",
                salary_text="$120k",
                apply_method="quick_apply",
                easy_apply=True,
                geography_code="NSW",
                captured_at="2026-09-10T10:00:00+00:00",
            )
        ],
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
        job = conn.execute(
            "SELECT salary_text,apply_method,easy_apply FROM jobs WHERE id=?",
            (existing.job_id,),
        ).fetchone()
        after_captures = conn.execute("SELECT COUNT(*) FROM card_captures").fetchone()[0]
    assert tuple(job) == ("$120k", "quick_apply", 1)
    assert after_captures == before_captures + 1


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

    navigations = []
    monkeypatch.setattr(
        seek_jd, "navigate", lambda *_a, **_k: navigations.append((_a, _k))
    )
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
    assert len(navigations) == 2


def test_short_seek_jd_reloads_once_and_recovers(monkeypatch):
    from collector import seek_jd

    navigations = []
    monkeypatch.setattr(
        seek_jd, "navigate", lambda *_a, **_k: navigations.append((_a, _k))
    )
    calls = {"detail": 0}

    def fake_detail(_page_id):
        calls["detail"] += 1
        description = ""
        if len(navigations) == 2:
            description = (
                "A complete source-backed job description that becomes available after "
                "the one bounded re-navigation of an incomplete SEEK render."
            )
        return type(
            "Response",
            (),
            {
                "result": {
                    "page_url": "https://au.seek.com/job/94511500",
                    "source_job_id": "94511500",
                    "full_description": description,
                    "human_check": False,
                }
            },
        )()

    monkeypatch.setattr(seek_jd, "seek_job_detail", fake_detail)
    now = [0.0]
    monkeypatch.setattr(seek_jd.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        seek_jd.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )

    detail = seek_jd.fetch_seek_detail(
        1,
        "https://au.seek.com/job/94511500",
        expected_source_job_id="94511500",
        timeout_seconds=1.0,
        human_wait_seconds=900.0,
    )

    assert len(navigations) == 2
    assert calls["detail"] > 1
    assert detail.full_description.startswith("A complete source-backed job description")


def test_technical_error_retries_once_then_stays_retryable(monkeypatch):
    from collector import seek_jd

    navigations = []
    monkeypatch.setattr(
        seek_jd, "navigate", lambda *_a, **_k: navigations.append((_a, _k))
    )
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
                    "transient_error": "technical_error",
                    "human_check": False,
                }
            },
        )(),
    )

    with pytest.raises(seek_jd.SeekJDTransientError, match="after retry"):
        seek_jd.fetch_seek_detail(
            1,
            "https://au.seek.com/job/94511500",
            expected_source_job_id="94511500",
            timeout_seconds=15.0,
        )

    assert len(navigations) == 2


def test_transient_seek_error_ends_only_current_jd_sweep(monkeypatch):
    from collector import seek_jd

    monkeypatch.setattr(seek_jd, "_transient_sweep_cooldown_until", 0.0)

    monkeypatch.setattr(
        seek_jd,
        "coverage_seek_jobs",
        lambda **_kwargs: [
            {
                "id": 1,
                "identity_key": "seek:id:1",
                "source_job_id": "1",
                "canonical_url": "https://au.seek.com/job/1",
                "jd_fetch_completed": 0,
            },
            {
                "id": 2,
                "identity_key": "seek:id:2",
                "source_job_id": "2",
                "canonical_url": "https://au.seek.com/job/2",
                "jd_fetch_completed": 0,
            },
        ],
    )
    calls = []

    def transient_fetch(_page_id, url, **_kwargs):
        calls.append(url)
        raise seek_jd.SeekJDTransientError("technical error after retry")

    monkeypatch.setattr(seek_jd, "fetch_seek_detail", transient_fetch)
    events = []
    result = seek_jd.enrich_seek_coverage_jds(
        page_id=1,
        codes=["ACT"],
        days=3,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
        on_progress=events.append,
    )

    assert calls == ["https://au.seek.com/job/1"]
    assert result.attempted == 1
    assert result.failed == 1
    assert result.stored == 0
    assert result.remaining == 2
    assert events == ["attempted", "failed"]


def test_transient_seek_error_cools_down_followup_sweep(monkeypatch):
    from collector import seek_jd

    monkeypatch.setattr(seek_jd, "_transient_sweep_cooldown_until", 0.0)
    monkeypatch.setattr(seek_jd.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        seek_jd,
        "coverage_seek_jobs",
        lambda **_kwargs: [
            {
                "id": 1,
                "identity_key": "seek:id:1",
                "source_job_id": "1",
                "canonical_url": "https://au.seek.com/job/1",
                "jd_fetch_completed": 0,
            }
        ],
    )
    calls = []

    def transient_fetch(*_args, **_kwargs):
        calls.append(1)
        raise seek_jd.SeekJDTransientError("technical error after retry")

    monkeypatch.setattr(seek_jd, "fetch_seek_detail", transient_fetch)

    first = seek_jd.enrich_seek_coverage_jds(
        page_id=1,
        codes=["ACT"],
        days=3,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
    )
    second = seek_jd.enrich_seek_coverage_jds(
        page_id=1,
        codes=["ACT"],
        days=3,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
    )

    assert calls == [1]
    assert first.attempted == 1
    assert first.failed == 1
    assert second.attempted == 0
    assert second.failed == 0
    assert second.remaining == 1


def test_no_longer_advertised_is_terminal_not_failed(tmp_path, monkeypatch):
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
        job_id = conn.execute(
            "INSERT INTO jobs(source,source_job_id,canonical_url,identity_key) VALUES('seek','94511500','https://au.seek.com/job/94511500','seek:id:94511500')"
        ).lastrowid
        conn.execute(
            "INSERT INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?,?)",
            (partition_id, job_id, "x"),
        )

    monkeypatch.setattr(
        seek_jd,
        "fetch_seek_detail",
        lambda *_a, **_k: (_ for _ in ()).throw(
            seek_jd.SeekJDUnavailableError(
                "no longer advertised", source_status="no_longer_advertised"
            )
        ),
    )

    result = seek_jd.enrich_seek_coverage_jds(
        page_id=1,
        codes=["ACT"],
        days=3,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
    )

    assert result.attempted == 1
    assert result.stored == 0
    assert result.failed == 0
    assert result.unavailable == 1
    assert result.remaining == 0
    with db.connect() as conn:
        row = conn.execute(
            "SELECT source_status FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        assert row["source_status"] == "no_longer_advertised"
    assert seek_jd.coverage_seek_jobs(codes=["ACT"], days=3) == []


def test_navigation_timeout_retries_once_then_continues(tmp_path, monkeypatch):
    seek_jd = _wire(tmp_path, monkeypatch)
    from collector.browser_broker import BrowserBrokerTimeout

    for source_id in ("91000001", "91000002"):
        obs = CardObservation(
            source="seek",
            source_job_id=source_id,
            canonical_url=f"https://au.seek.com/job/{source_id}",
            title=f"Role {source_id}",
            geography_code="ACT",
            captured_at="2026-09-11T00:00:00+00:00",
        )
        job_id = ingest_card(obs).job_id
        with db.connect() as conn:
            partition = conn.execute(
                "SELECT id FROM seek_partitions WHERE geography_code='ACT' LIMIT 1"
            ).fetchone()
            if partition is None:
                partition_id = conn.execute(
                    """INSERT INTO seek_partitions(
                        geography_code,parent_id,level,label,url,status,reported_results,
                        collected_unique_jobs,max_results_threshold,first_seen_at,updated_at,completed_at
                    ) VALUES('ACT',NULL,'state','ACT',
                        'https://au.seek.com/jobs/in-Australian-Capital-Territory-ACT?daterange=3',
                        'COMPLETE',2,2,450,'x','x','x')"""
                ).lastrowid
            else:
                partition_id = partition[0]
            conn.execute(
                "INSERT INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?,?)",
                (partition_id, job_id, "x"),
            )

    calls = {"91000001": 0, "91000002": 0}

    def fake_fetch(_page_id, url, **_kwargs):
        source_id = url.rstrip("/").split("/")[-1]
        calls[source_id] += 1
        if source_id == "91000001":
            raise BrowserBrokerTimeout("navigate timed out")
        return seek_jd.SeekFetchedDetail(
            full_description="A valid source-backed job description that is comfortably longer than eighty characters for this test case.",
            facts={"source_job_id": source_id},
        )

    monkeypatch.setattr(seek_jd, "fetch_seek_detail", fake_fetch)
    events = []
    result = seek_jd.enrich_seek_coverage_jds(
        page_id=1,
        codes=["ACT"],
        days=3,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
        on_progress=events.append,
    )

    assert calls["91000001"] == 2
    assert calls["91000002"] == 1
    assert result.attempted == 2
    assert result.failed == 1
    assert result.stored == 1
    assert events.count("attempted") == 2
    assert events.count("failed") == 1
    assert events.count("stored") == 1


def test_not_found_is_terminal_and_excluded(tmp_path, monkeypatch):
    seek_jd = _wire(tmp_path, monkeypatch)
    obs = CardObservation(
        source="seek",
        source_job_id="94520983",
        canonical_url="https://au.seek.com/job/94520983",
        title="Fraud Manager",
        geography_code="ACT",
        captured_at="2026-09-11T00:00:00+00:00",
    )
    job_id = ingest_card(obs).job_id
    with db.connect() as conn:
        partition_id = conn.execute(
            """INSERT INTO seek_partitions(
                geography_code,parent_id,level,label,url,status,reported_results,collected_unique_jobs,
                max_results_threshold,first_seen_at,updated_at,completed_at
            ) VALUES('ACT',NULL,'state','ACT',
                'https://au.seek.com/jobs/in-Australian-Capital-Territory-ACT?daterange=3',
                'COMPLETE',1,1,450,'x','x','x')"""
        ).lastrowid
        conn.execute(
            "INSERT INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?,?)",
            (partition_id, job_id, "x"),
        )

    monkeypatch.setattr(
        seek_jd,
        "fetch_seek_detail",
        lambda *_a, **_k: (_ for _ in ()).throw(
            seek_jd.SeekJDUnavailableError("404", source_status="not_found")
        ),
    )
    result = seek_jd.enrich_seek_coverage_jds(
        page_id=1,
        codes=["ACT"],
        days=3,
        should_stop=lambda: False,
        deadline_reached=lambda: False,
    )
    assert result.failed == 0
    assert result.unavailable == 1
    assert result.remaining == 0
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT source_status FROM jobs WHERE id=?", (job_id,)
            ).fetchone()[0]
            == "not_found"
        )
    assert seek_jd.coverage_seek_jobs(codes=["ACT"], days=3) == []
