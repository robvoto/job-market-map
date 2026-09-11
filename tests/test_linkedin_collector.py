import pandas as pd

from collector import db
from collector.ingest import ingest_card
from collector.linkedin_detail import LinkedInDetailError, LinkedInDetailEvidence
from collector.models import CardObservation
from sources import linkedin_collector


def _row(job_id: str = "li-4464190406") -> pd.DataFrame:
    numeric = job_id.removeprefix("li-")
    return pd.DataFrame(
        [
            {
                "id": job_id,
                "title": "Business Analyst",
                "company": "Example Co",
                "location": "Sydney, NSW, Australia",
                "job_url": f"https://www.linkedin.com/jobs/view/{numeric}",
                "date_posted": "2026-09-11",
                "is_remote": False,
            }
        ]
    )


def _detail(*, description: str | None = None) -> LinkedInDetailEvidence:
    return LinkedInDetailEvidence(
        full_description=description or ("A detailed LinkedIn job description. " * 10),
        apply_url=None,
        apply_method="easy_apply",
        easy_apply=True,
        reposted=True,
        source_status=None,
        applicant_count=30,
        header_text="Reposted 1 day ago · 30 applicants",
    )


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "market.db")
    db.init_db()


def test_linkedin_chunk_uses_jobspy_then_one_detail_response_for_all_neutral_fields(
    tmp_path, monkeypatch
):
    _isolate(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        linkedin_collector,
        "_fetch_jobspy_isolated",
        lambda params, should_stop: calls.append(params) or _row(),
    )
    monkeypatch.setattr(linkedin_collector, "fetch_linkedin_detail", lambda _url: _detail())

    result = linkedin_collector.collect_linkedin_chunk(
        "business analyst",
        "New South Wales, Australia",
        geography_code="NSW",
        cycle_key="2026-09-11",
        days=1,
        results_wanted=25,
        should_stop=lambda: False,
    )

    assert result.status == "COMPLETE"
    assert result.detail_attempted == 1
    assert result.detail_stored == 1
    assert calls[0]["linkedin_fetch_description"] is False
    assert calls[0]["hours_old"] == 24
    assert calls[0]["offset"] == 0
    with db.connect() as conn:
        job = dict(conn.execute("SELECT * FROM jobs WHERE source='linkedin'").fetchone())
        assert job["source_job_id"] == "li-4464190406"
        assert job["full_description"]
        assert job["easy_apply"] == 1
        assert job["apply_method"] == "easy_apply"
        assert job["reposted"] == 1
        assert job["applicant_count"] == 30
        assert conn.execute("SELECT COUNT(*) FROM jd_fetch_registry").fetchone()[0] == 1


def test_linkedin_chunk_treats_old_numeric_id_as_same_native_job(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    ingest_card(
        CardObservation(
            source="linkedin",
            source_job_id="4464190406",
            canonical_url="https://www.linkedin.com/jobs/view/4464190406",
            title="Business Analyst",
            employer="Example Co",
        )
    )
    monkeypatch.setattr(
        linkedin_collector,
        "_fetch_jobspy_isolated",
        lambda _params, should_stop: _row("li-4464190406"),
    )
    monkeypatch.setattr(linkedin_collector, "fetch_linkedin_detail", lambda _url: _detail())

    result = linkedin_collector.collect_linkedin_chunk(
        "business analyst",
        "New South Wales, Australia",
        geography_code="NSW",
        cycle_key="2026-09-11",
        days=1,
        should_stop=lambda: False,
    )
    assert result.unique_new_jobs == 0
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE source='linkedin'").fetchone()[0] == 1
        assert conn.execute("SELECT source_job_id FROM jobs").fetchone()[0] == "4464190406"


def test_linkedin_cursor_resumes_same_cycle_and_resets_for_new_cycle(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    offsets = []

    def fake_fetch(params, should_stop):
        offsets.append(params["offset"])
        return _row()

    monkeypatch.setattr(linkedin_collector, "_fetch_jobspy_isolated", fake_fetch)
    monkeypatch.setattr(linkedin_collector, "fetch_linkedin_detail", lambda _url: _detail())

    first = linkedin_collector.collect_linkedin_chunk(
        "business analyst",
        "NSW",
        geography_code="NSW",
        cycle_key="2026-09-11",
        days=1,
        results_wanted=1,
        should_stop=lambda: False,
    )
    second = linkedin_collector.collect_linkedin_chunk(
        "business analyst",
        "NSW",
        geography_code="NSW",
        cycle_key="2026-09-11",
        days=1,
        results_wanted=1,
        should_stop=lambda: False,
    )
    third = linkedin_collector.collect_linkedin_chunk(
        "business analyst",
        "NSW",
        geography_code="NSW",
        cycle_key="2026-09-12",
        days=1,
        results_wanted=1,
        should_stop=lambda: False,
    )
    assert first.start_offset == 0
    assert second.start_offset == 1
    assert third.start_offset == 0
    assert offsets == [0, 1, 0]


def test_failed_detail_is_not_retried_for_duplicate_query_hit_in_same_campaign(
    tmp_path, monkeypatch
):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        linkedin_collector,
        "_fetch_jobspy_isolated",
        lambda _params, should_stop: _row(),
    )
    detail_calls = []

    def fail_detail(url):
        detail_calls.append(url)
        raise LinkedInDetailError("temporary")

    monkeypatch.setattr(linkedin_collector, "fetch_linkedin_detail", fail_detail)
    attempted: set[str] = set()
    for query in ("business analyst", "technical business analyst"):
        result = linkedin_collector.collect_linkedin_chunk(
            query,
            "NSW",
            geography_code="NSW",
            cycle_key="2026-09-11",
            days=1,
            results_wanted=25,
            should_stop=lambda: False,
            detail_attempted_ids=attempted,
        )
        assert result.status == "COMPLETE"
    assert len(detail_calls) == 1
    with db.connect() as conn:
        assert conn.execute("SELECT full_description FROM jobs").fetchone()[0] is None


def test_linkedin_cards_only_chunk_never_fetches_detail(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        linkedin_collector,
        "_fetch_jobspy_isolated",
        lambda params, should_stop: _row(),
    )
    monkeypatch.setattr(
        linkedin_collector,
        "fetch_linkedin_detail",
        lambda _url: (_ for _ in ()).throw(AssertionError("detail fetch must not run")),
    )

    result = linkedin_collector.collect_linkedin_chunk(
        "",
        "New South Wales, Australia",
        geography_code="NSW",
        cycle_key="2026-09-11",
        days=1,
        results_wanted=20,
        fetch_details=False,
        max_results=1000,
        should_stop=lambda: False,
    )
    assert result.detail_attempted == 0
    assert result.detail_stored == 0
    with db.connect() as conn:
        job = conn.execute(
            "SELECT full_description FROM jobs WHERE source='linkedin'"
        ).fetchone()
    assert job[0] is None


def test_linkedin_chunk_marks_result_cap_incomplete(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from collector.cursors import save_cursor

    save_cursor(
        "linkedin",
        "",
        "NSW",
        980,
        status="PARTIAL",
        cycle_key="2026-09-11",
    )
    rows = pd.concat([_row(f"li-{4465000000 + i}") for i in range(20)], ignore_index=True)
    monkeypatch.setattr(
        linkedin_collector,
        "_fetch_jobspy_isolated",
        lambda params, should_stop: rows,
    )

    result = linkedin_collector.collect_linkedin_chunk(
        "",
        "NSW",
        geography_code="NSW",
        cycle_key="2026-09-11",
        days=1,
        results_wanted=20,
        fetch_details=False,
        max_results=1000,
        should_stop=lambda: False,
    )
    assert result.start_offset == 980
    assert result.next_offset == 1000
    assert result.status == "INCOMPLETE_CAP"


def test_linkedin_chunk_records_interrupt_as_stopped_not_failed(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        linkedin_collector,
        "_fetch_jobspy_isolated",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(InterruptedError("stopped")),
    )

    import pytest

    with pytest.raises(InterruptedError, match="stopped"):
        linkedin_collector.collect_linkedin_chunk(
            "",
            "New South Wales, Australia",
            geography_code="NSW",
            cycle_key="2026-09-11",
            days=1,
            results_wanted=20,
            fetch_details=False,
            max_results=1000,
            should_stop=lambda: False,
        )

    with db.connect() as conn:
        run = conn.execute(
            "SELECT status,error FROM collection_runs WHERE source='linkedin' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert run["status"] == "STOPPED"
    assert run["error"] is None


def test_geography_page_uses_fixed_source_offset_not_parsed_row_count(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    row = _row().iloc[0].to_dict()
    monkeypatch.setattr(
        linkedin_collector,
        "_fetch_exact_page_resilient",
        lambda **_kwargs: ([row], True, 2),
    )

    result = linkedin_collector.collect_linkedin_geography_page(
        "New South Wales, Australia",
        geography_code="NSW",
        cycle_key="2026-09-11T20",
        hours_old=5,
        should_stop=lambda: False,
    )

    assert result.cards_observed == 1
    assert result.next_offset == 10
    assert result.status == "PARTIAL"
    with db.connect() as conn:
        cursor = conn.execute(
            "SELECT cursor_value,status FROM collection_cursors WHERE source='linkedin' AND query_text=''"
        ).fetchone()
    assert tuple(cursor) == (10, "PARTIAL")


def test_geography_short_page_with_later_rows_is_not_terminal(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    row = _row().iloc[0].to_dict()
    calls = []

    def fake_page(**kwargs):
        calls.append(kwargs["offset"])
        if kwargs["offset"] == 0:
            return ([row], False, 3)
        return ([row], True, 1)

    monkeypatch.setattr(linkedin_collector, "_fetch_exact_page_resilient", fake_page)
    result = linkedin_collector.collect_linkedin_geography_page(
        "New South Wales, Australia",
        geography_code="NSW",
        cycle_key="2026-09-11T20",
        hours_old=5,
        should_stop=lambda: False,
    )
    assert calls == [0, 10]
    assert result.status == "PARTIAL"
    assert result.next_offset == 10


def test_geography_short_page_requires_empty_forward_probes_before_complete(
    tmp_path, monkeypatch
):
    _isolate(tmp_path, monkeypatch)
    calls = []

    def fake_page(**kwargs):
        calls.append(kwargs["offset"])
        return ([], False, 2)

    monkeypatch.setattr(linkedin_collector, "_fetch_exact_page_resilient", fake_page)
    result = linkedin_collector.collect_linkedin_geography_page(
        "Australian Capital Territory, Australia",
        geography_code="ACT",
        cycle_key="2026-09-11T20",
        hours_old=5,
        should_stop=lambda: False,
    )
    assert calls == [0, 10, 20, 30]
    assert result.status == "COMPLETE"
    assert result.next_offset == 10


def test_geography_full_last_page_marks_cap_incomplete(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from collector.cursors import save_cursor

    save_cursor(
        "linkedin",
        "",
        "New South Wales, Australia",
        990,
        status="PARTIAL",
        cycle_key="2026-09-11T20",
    )
    rows = [
        _row(f"li-{4465000000 + i}").iloc[0].to_dict()
        for i in range(linkedin_collector.LINKEDIN_PAGE_SIZE)
    ]
    monkeypatch.setattr(
        linkedin_collector,
        "_fetch_exact_page_resilient",
        lambda **_kwargs: (rows, True, 1),
    )
    result = linkedin_collector.collect_linkedin_geography_page(
        "New South Wales, Australia",
        geography_code="NSW",
        cycle_key="2026-09-11T20",
        hours_old=5,
        should_stop=lambda: False,
    )
    assert result.start_offset == 990
    assert result.next_offset == 1000
    assert result.status == "INCOMPLETE_CAP"
