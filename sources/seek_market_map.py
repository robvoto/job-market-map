from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from collector.browser_broker import (
    BrowserBrokerError,
    click,
    navigate,
    open_tab,
    select_page,
    snapshot,
)
from collector.db import connect, init_db
from collector.geographies import get_geography, list_geographies
from collector.ingest import ingest_card
from collector.settings import get_setting
from sources.seek import (
    SeekParseError,
    parse_seek_snapshot,
    seek_refinement_links,
    seek_result_count,
)

TERMINAL_TEXT = (
    "no matching search results",
    "we couldn't find anything that matched your search",
)
CHALLENGE_TEXT = (
    "help us keep seek secure",
    "confirm you are human",
    "verify you are human",
    "captcha",
    "security check",
    "enable javascript and cookies to continue",
)
HUMAN_CHECK_WAIT_SECONDS = 900.0


@dataclass(frozen=True)
class MarketMapResult:
    geography_code: str
    status: str
    root_partition_id: int
    reported_results: int | None
    covered_unique_jobs: int
    incomplete_partitions: int
    partitions_processed: int = 0
    budget_exhausted: bool = False


@dataclass
class PartitionBudget:
    max_partitions: int | None = None
    processed: int = 0

    @property
    def exhausted(self) -> bool:
        return self.max_partitions is not None and self.processed >= self.max_partitions

    def consume(self) -> bool:
        if self.exhausted:
            return False
        self.processed += 1
        return True


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def state_url(geography: dict, days: int) -> str:
    return f"https://au.seek.com/jobs/in-{geography['seek_state_slug']}?daterange={days}&sortmode=ListedDate"


def _page_url(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if page > 1:
        query["page"] = str(page)
    else:
        query.pop("page", None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _same_seek_page(actual_url: str, expected_url: str) -> bool:
    actual = urlsplit(str(actual_url or ""))
    expected = urlsplit(str(expected_url or ""))
    if not actual.scheme or not actual.netloc:
        return True
    seek_hosts = {"au.seek.com", "seek.com.au", "www.seek.com.au"}
    if (
        actual.netloc.casefold() not in seek_hosts
        or expected.netloc.casefold() not in seek_hosts
    ):
        return False
    if actual.path.rstrip("/") != expected.path.rstrip("/"):
        return False
    actual_q = dict(parse_qsl(actual.query, keep_blank_values=True))
    expected_q = dict(parse_qsl(expected.query, keep_blank_values=True))
    return all(actual_q.get(key) == value for key, value in expected_q.items())


def _wait_snapshot(
    page_id: int, *, expected_url: str | None = None, timeout: float | None = None
) -> dict:
    timeout = float(
        timeout
        if timeout is not None
        else get_setting("collection.seek_parse_wait_seconds")
    )
    normal_deadline = time.monotonic() + timeout
    human_deadline: float | None = None
    brought_forward = False
    last = {}
    while True:
        last = snapshot(page_id, verbose=True).result or {}
        actual_url = str(last.get("url") or "")
        if (
            expected_url
            and actual_url
            and not _same_seek_page(actual_url, expected_url)
        ):
            raise BrowserBrokerError(
                f"SEEK tab ownership lost: expected {expected_url!r}, browser is on {actual_url!r}"
            )
        text = str(last.get("text") or "")
        low = text.casefold()
        if any(marker in low for marker in CHALLENGE_TEXT):
            if not brought_forward:
                select_page(page_id, bring_to_front=True)
                print(
                    "SEEK needs human confirmation; brought JMM tab to front.",
                    flush=True,
                )
                brought_forward = True
                human_deadline = time.monotonic() + HUMAN_CHECK_WAIT_SECONDS
            elif human_deadline is not None and time.monotonic() >= human_deadline:
                raise BrowserBrokerError("SEEK human-check wait expired")
            time.sleep(1.0)
            continue
        if seek_result_count(text) is not None or any(
            marker in low for marker in TERMINAL_TEXT
        ):
            return last
        if time.monotonic() >= normal_deadline:
            raise SeekParseError(
                f"SEEK partition page did not reach a result state: {last.get('url')!r}"
            )
        time.sleep(0.5)


def _ensure_partition(
    *,
    geography_code: str,
    parent_id: int | None,
    level: str,
    label: str,
    url: str,
    threshold: int,
) -> int:
    init_db()
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO seek_partitions(
                geography_code,parent_id,level,label,url,status,max_results_threshold,first_seen_at,updated_at
            ) VALUES(?,?,?,?,?,'PENDING',?,?,?)
            ON CONFLICT(url) DO UPDATE SET
                geography_code=excluded.geography_code,
                parent_id=excluded.parent_id,
                level=excluded.level,
                label=excluded.label,
                max_results_threshold=excluded.max_results_threshold,
                updated_at=excluded.updated_at
            """,
            (geography_code, parent_id, level, label, url, threshold, now, now),
        )
        row = conn.execute(
            "SELECT id FROM seek_partitions WHERE url=?", (url,)
        ).fetchone()
    return int(row[0])


def _update_partition(
    partition_id: int,
    *,
    status: str,
    reported: int | None = None,
    collected: int | None = None,
    child_count: int | None = None,
    error: str | None = None,
) -> None:
    fields = ["status=?", "updated_at=?", "last_error=?"]
    values: list[object] = [status, _now(), error]
    if reported is not None:
        fields.append("reported_results=?")
        values.append(reported)
    if collected is not None:
        fields.append("collected_unique_jobs=?")
        values.append(collected)
    if child_count is not None:
        fields.append("child_count=?")
        values.append(child_count)
    if status.startswith("COMPLETE"):
        fields.append("completed_at=?")
        values.append(_now())
    values.append(partition_id)
    with connect() as conn:
        conn.execute(
            f"UPDATE seek_partitions SET {', '.join(fields)} WHERE id=?", values
        )


def _expand_refinement(page_id: int, *, aria_label: str) -> dict:
    snap = _wait_snapshot(page_id)
    target = next(
        (
            e
            for e in snap.get("elements") or []
            if str(e.get("ariaLabel") or "").casefold() == aria_label.casefold()
        ),
        None,
    )
    if not target or not target.get("uid"):
        raise SeekParseError(f"SEEK refinement control unavailable: {aria_label}")
    click(page_id, str(target["uid"]))
    time.sleep(0.25)
    return snapshot(page_id, verbose=True).result or {}


def _discover_children(
    page_id: int, *, geography: dict, level: str, current_url: str
) -> list[dict[str, str]]:
    if level in {"state", "classification"}:
        expanded = _expand_refinement(page_id, aria_label="refine by classifications")
    elif level == "subclassification":
        expanded = _expand_refinement(page_id, aria_label="refine by work type")
    else:
        return []
    return seek_refinement_links(
        expanded,
        state_slug=geography["seek_state_slug"],
        level=level,
        current_url=current_url,
    )


def _partition_source_ids(partition_id: int) -> set[str]:
    with connect() as conn:
        return {
            str(row[0])
            for row in conn.execute(
                """
                SELECT j.source_job_id
                  FROM seek_partition_jobs spj
                  JOIN jobs j ON j.id=spj.job_id
                 WHERE spj.partition_id=? AND j.source_job_id IS NOT NULL
                """,
                (partition_id,),
            )
        }


def _partition_membership_count(partition_id: int) -> int:
    with connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM seek_partition_jobs WHERE partition_id=?",
                (partition_id,),
            ).fetchone()[0]
        )


def _collect_leaf(
    page_id: int,
    *,
    partition_id: int,
    url: str,
    geography_code: str,
    reported: int,
    tolerance: int,
) -> tuple[str, int]:
    seen = _partition_source_ids(partition_id)
    previous_page_ids: set[str] | None = None
    page = 1
    safety = int(get_setting("collection.seek_safety_page_limit"))
    while page <= safety:
        target_url = _page_url(url, page)
        navigate(page_id, target_url)
        time.sleep(float(get_setting("collection.seek_page_load_seconds")))
        snap = _wait_snapshot(page_id, expected_url=target_url)
        text = str(snap.get("text") or "")
        if any(marker in text.casefold() for marker in TERMINAL_TEXT):
            break
        cards = parse_seek_snapshot(
            snap,
            query_text=None,
            query_location=None,
            page_number=page,
            geography_code=geography_code,
        )
        page_ids = {card.source_job_id for card in cards if card.source_job_id}
        if previous_page_ids is not None and page_ids == previous_page_ids:
            return "INCOMPLETE_REPEATED_PAGE", len(seen)
        previous_page_ids = page_ids
        new_cards = [
            card
            for card in cards
            if card.source_job_id and card.source_job_id not in seen
        ]
        source_ids = [
            str(card.source_job_id) for card in new_cards if card.source_job_id
        ]
        existing_by_source_id: dict[str, int] = {}
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            with connect() as conn:
                existing_by_source_id = {
                    str(row["source_job_id"]): int(row["id"])
                    for row in conn.execute(
                        f"SELECT id,source_job_id FROM jobs WHERE source='seek' AND source_job_id IN ({placeholders})",
                        source_ids,
                    )
                }

        for card in new_cards:
            source_id = str(card.source_job_id)
            seen.add(source_id)
            job_id = existing_by_source_id.get(source_id)
            if job_id is None:
                job_id = ingest_card(card).job_id
            else:
                # Daily scans only need to prove this known identity is still present.
                # Avoid creating another raw card capture or rerunning duplicate work.
                with connect() as conn:
                    now = _now()
                    conn.execute(
                        """
                        INSERT INTO job_observation_state(
                            job_id,first_seen_at,last_seen_at,capture_count,archived,compacted_at
                        ) VALUES(?,?,?,1,0,NULL)
                        ON CONFLICT(job_id) DO UPDATE SET
                            last_seen_at=excluded.last_seen_at,
                            archived=0,
                            compacted_at=NULL
                        """,
                        (job_id, now, now),
                    )
            with connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?,?)",
                    (partition_id, job_id, _now()),
                )
        if len(seen) + tolerance >= reported:
            return "COMPLETE", len(seen)
        page += 1
    else:
        return "INCOMPLETE_PAGE_LIMIT", len(seen)
    status = (
        "COMPLETE" if len(seen) + tolerance >= reported else "INCOMPLETE_COUNT_MISMATCH"
    )
    return status, len(seen)


def _aggregate_parent(
    partition_id: int, reported: int, tolerance: int
) -> tuple[str, int, int]:
    with connect() as conn:
        children = conn.execute(
            "SELECT id,status FROM seek_partitions WHERE parent_id=?", (partition_id,)
        ).fetchall()
        ids = {
            int(row[0])
            for row in conn.execute(
                """SELECT DISTINCT spj.job_id FROM seek_partition_jobs spj
               JOIN seek_partitions child ON child.id=spj.partition_id
               WHERE child.parent_id=?""",
                (partition_id,),
            )
        }
        for job_id in ids:
            conn.execute(
                "INSERT OR IGNORE INTO seek_partition_jobs(partition_id,job_id,first_seen_at) VALUES(?,?,?)",
                (partition_id, job_id, _now()),
            )
    children_ok = bool(children) and all(
        str(c["status"]).startswith("COMPLETE") for c in children
    )
    count_ok = len(ids) + tolerance >= reported
    status = (
        "COMPLETE_BY_PARTITION"
        if children_ok and count_ok
        else "INCOMPLETE_CHILD_COVERAGE"
    )
    return status, len(ids), len(children)


def _partition_state(partition_id: int) -> tuple[dict, list[dict]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM seek_partitions WHERE id=?", (partition_id,)
        ).fetchone()
        if not row:
            raise KeyError(partition_id)
        children = [
            dict(child)
            for child in conn.execute(
                "SELECT * FROM seek_partitions WHERE parent_id=? ORDER BY id",
                (partition_id,),
            )
        ]
    return dict(row), children


def _resume_existing_children(
    page_id: int,
    *,
    geography: dict,
    partition_id: int,
    reported: int,
    threshold: int,
    tolerance: int,
    budget: PartitionBudget,
    resume: bool,
    children: list[dict],
) -> None:
    for child in children:
        _process_partition(
            page_id,
            geography=geography,
            partition_id=int(child["id"]),
            level=str(child["level"]),
            label=str(child["label"]),
            url=str(child["url"]),
            threshold=threshold,
            tolerance=tolerance,
            budget=budget,
            resume=resume,
        )
    status, collected, child_count = _aggregate_parent(
        partition_id, reported, tolerance
    )
    _update_partition(
        partition_id,
        status=status,
        reported=reported,
        collected=collected,
        child_count=child_count,
    )


def _process_partition(
    page_id: int,
    *,
    geography: dict,
    partition_id: int,
    level: str,
    label: str,
    url: str,
    threshold: int,
    tolerance: int,
    budget: PartitionBudget,
    resume: bool = True,
) -> None:
    try:
        current, existing_children = _partition_state(partition_id)
        current_status = str(current["status"])
        if resume and current_status.startswith("COMPLETE"):
            return
        if resume and current_status == "INCOMPLETE_OVERSIZE_UNSPLITTABLE":
            return
        if resume and not existing_children and current["reported_results"] is not None:
            persisted = _partition_membership_count(partition_id)
            reported = int(current["reported_results"])
            if persisted + tolerance >= reported:
                _update_partition(
                    partition_id,
                    status="COMPLETE_RECOVERED",
                    reported=reported,
                    collected=persisted,
                )
                return
        if resume and existing_children and current["reported_results"] is not None:
            _resume_existing_children(
                page_id,
                geography=geography,
                partition_id=partition_id,
                reported=int(current["reported_results"]),
                threshold=threshold,
                tolerance=tolerance,
                budget=budget,
                resume=resume,
                children=existing_children,
            )
            return
        if not budget.consume():
            return
        navigate(page_id, url)
        time.sleep(float(get_setting("collection.seek_page_load_seconds")))
        snap = _wait_snapshot(page_id, expected_url=url)
        text = str(snap.get("text") or "")
        if any(marker in text.casefold() for marker in TERMINAL_TEXT):
            _update_partition(partition_id, status="COMPLETE", reported=0, collected=0)
            return
        reported = seek_result_count(text)
        if reported is None:
            raise SeekParseError(
                f"SEEK did not expose result count for partition {label!r}"
            )
        _update_partition(partition_id, status="INSPECTED", reported=reported)
        if reported == 0:
            _update_partition(partition_id, status="COMPLETE", reported=0, collected=0)
            return
        if reported <= threshold:
            status, collected = _collect_leaf(
                page_id,
                partition_id=partition_id,
                url=url,
                geography_code=geography["code"],
                reported=reported,
                tolerance=tolerance,
            )
            _update_partition(
                partition_id, status=status, reported=reported, collected=collected
            )
            return
        _update_partition(partition_id, status="INCOMPLETE_OVERSIZE", reported=reported)
        children = _discover_children(
            page_id, geography=geography, level=level, current_url=url
        )
        if not children:
            _update_partition(
                partition_id,
                status="INCOMPLETE_OVERSIZE_UNSPLITTABLE",
                reported=reported,
                child_count=0,
            )
            return
        for child in children:
            child_id = _ensure_partition(
                geography_code=geography["code"],
                parent_id=partition_id,
                level=child["level"],
                label=child["label"],
                url=child["url"],
                threshold=threshold,
            )
            _process_partition(
                page_id,
                geography=geography,
                partition_id=child_id,
                level=child["level"],
                label=child["label"],
                url=child["url"],
                threshold=threshold,
                tolerance=tolerance,
                budget=budget,
                resume=resume,
            )
        status, collected, child_count = _aggregate_parent(
            partition_id, reported, tolerance
        )
        _update_partition(
            partition_id,
            status=status,
            reported=reported,
            collected=collected,
            child_count=child_count,
        )
    except Exception as exc:
        _update_partition(partition_id, status="FAILED", error=str(exc))
        raise


def collect_seek_state(
    geography_code: str,
    *,
    page_id: int | None = None,
    days: int | None = None,
    max_partitions: int | None = None,
    resume: bool = True,
) -> MarketMapResult:
    geography = get_geography(geography_code)
    days = int(
        days if days is not None else get_setting("collection.default_freshness_days")
    )
    threshold = int(get_setting("collection.seek_partition_max_results"))
    tolerance = int(get_setting("collection.seek_completion_count_tolerance"))
    if max_partitions is None:
        max_partitions = int(get_setting("collection.seek_partition_chunk_size"))
    if max_partitions < 1:
        raise ValueError("max_partitions must be >= 1")
    budget = PartitionBudget(max_partitions=max_partitions)
    root_url = state_url(geography, days)
    root_id = _ensure_partition(
        geography_code=geography["code"],
        parent_id=None,
        level="state",
        label=geography["label"],
        url=root_url,
        threshold=threshold,
    )
    if page_id is None:
        page_id = int(open_tab("about:blank", active=False).result["pageId"])
    _process_partition(
        page_id,
        geography=geography,
        partition_id=root_id,
        level="state",
        label=geography["label"],
        url=root_url,
        threshold=threshold,
        tolerance=tolerance,
        budget=budget,
        resume=resume,
    )
    with connect() as conn:
        root = conn.execute(
            "SELECT * FROM seek_partitions WHERE id=?", (root_id,)
        ).fetchone()
        incomplete = conn.execute(
            "SELECT COUNT(*) FROM seek_partitions WHERE geography_code=? AND status NOT LIKE 'COMPLETE%'",
            (geography["code"],),
        ).fetchone()[0]
    return MarketMapResult(
        geography_code=geography["code"],
        status=root["status"],
        root_partition_id=root_id,
        reported_results=root["reported_results"],
        covered_unique_jobs=root["collected_unique_jobs"],
        incomplete_partitions=int(incomplete),
        partitions_processed=budget.processed,
        budget_exhausted=budget.exhausted,
    )


def collect_states(
    codes: list[str],
    *,
    days: int | None = None,
    max_partitions: int | None = None,
    resume: bool = True,
) -> list[MarketMapResult]:
    if not codes:
        return []
    page_id = int(open_tab("about:blank", active=False).result["pageId"])
    return [
        collect_seek_state(
            code,
            page_id=page_id,
            days=days,
            max_partitions=max_partitions,
            resume=resume,
        )
        for code in codes
    ]


def collect_enabled_states(
    *,
    days: int | None = None,
    max_partitions: int | None = None,
    resume: bool = True,
) -> list[MarketMapResult]:
    return collect_states(
        [g["code"] for g in list_geographies(enabled_only=True)],
        days=days,
        max_partitions=max_partitions,
        resume=resume,
    )
