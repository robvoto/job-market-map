# SEEK Whole-State Coverage

## Current geography scope

The neutral market map currently targets these **whole states/territories**, not Sydney-only searches:

- NSW — New South Wales
- ACT — Australian Capital Territory
- QLD — Queensland

Rob can enable/disable these in `/admin`. The live SEEK URLs were verified on 10 September 2026.

## Why partitioning is mandatory

A raw whole-state SEEK search can exceed the board's safely pageable result space. On 10 September 2026, last-7-days headline counts were approximately:

- NSW: 12,048
- ACT: 917
- QLD: 11,032

Therefore **a successful page request is not evidence of complete market coverage**.

## Partition tree

Default `collection.seek_partition_max_results = 450`.

A partition with a reported count above that threshold is immediately `INCOMPLETE_OVERSIZE` and must split further:

```text
whole state
  -> SEEK classification
      -> SEEK subclassification
          -> work type
              -> Full time
              -> Part time
              -> Contract/Temp
              -> Casual/Vacation
```

The classification/subclassification/work-type links come from SEEK's own live refinement controls; the collector does not guess opaque classification IDs.

If a final work-type leaf still exceeds the threshold, it becomes `INCOMPLETE_OVERSIZE_UNSPLITTABLE`. It is **never** reported complete.

## Leaf completeness

A leaf is complete only when:

1. its reported result count is at or below the configured threshold;
2. paging terminates without hitting the safety-page limit;
3. collected unique SEEK job IDs are within the configured small count tolerance of SEEK's initial reported count.

Default tolerance is 5 jobs because postings can appear/disappear during a live crawl.

A shortfall becomes `INCOMPLETE_COUNT_MISMATCH`.

## Parent completeness

After children finish, their job IDs are **unioned and deduplicated**. A parent becomes `COMPLETE_BY_PARTITION` only when:

- every direct child is complete; and
- the union of child job IDs covers the parent's reported count within tolerance.

For an incremental extra run, a leaf that safely crosses the exact timestamp cutoff becomes `COMPLETE_INCREMENTAL`. That status propagates upward once every child is complete; the parent's full 24-hour reported count is intentionally not used as the target for that shorter window.

Otherwise the parent is `INCOMPLETE_CHILD_COVERAGE`.

This makes completeness fail closed: if exact card timestamps are missing or not newest-to-oldest, incremental stopping is disabled and normal full-count rules continue.

## Dedupe

SEEK source identity uses SEEK job ID. The same job found through multiple classification/work-type/query paths remains one canonical `jobs` row. `seek_partition_jobs` records every partition that contributed it.

Daily scans still have to read SEEK cards to discover which IDs exist, but known IDs use a cheap fast path. Unchanged known cards only refresh observation time. A known card is fully re-ingested only when currently visible canonical market evidence has changed; this preserves fresh market facts without creating thousands of duplicate captures every day. URL-host variation (`www.seek.com.au` versus `au.seek.com`) is not treated as a vacancy change because the SEEK job ID is the source identity.

Cross-board postings (for example SEEK + LinkedIn) remain separate source rows and identities. Strong same-vacancy evidence assigns the newer row to the oldest primary for JD and downstream processing; uncertain matches remain separate possible duplicates. Partition membership remains keyed to the contributing SEEK source row, not a non-SEEK processing primary, so a SEEK alias can still be selected to supply the first JD when its primary has no JD. JD candidate lookup also tolerates legacy memberships that point at a primary.

## Running / resumability

Use bounded resumable chunks. The admin default is one genuinely unfinished partition per state per invocation:

```bash
cd /home/robvoto/projects/job-market-map
uv run python -m scripts.run_seek_market_map --state ACT --max-partitions 1
```

Completed partitions are skipped without consuming the budget. Already-split parents delegate directly to unfinished children. If an interrupted leaf already persisted enough memberships to satisfy its reported count, the next run can finalize it as `COMPLETE_RECOVERED` without re-downloading it.

`--max-partitions` limits one execution window; it is **not** a coverage limit. Use `--fresh` only for an intentional re-crawl, not normal continuation.

JMM keeps its own visible long-lived Chromium service and profile (`data/playwright_jmm_seek_user_data`). It is separate from Rob's normal Chrome and Job Hunter's SEEK profile. Collection invocations attach to the same already-running JMM browser so human-verification/session state is preserved instead of triggering a fresh browser challenge on every retry.

Selected states:

```bash
uv run python -m scripts.run_seek_market_map --state NSW --state ACT
```

## Monitoring

```text
GET /v3/coverage/seek
```

Consumers should not assume SEEK is complete when a state status is `FAILED` or starts with `INCOMPLETE`.

`NOT_RUN` means there is no current coverage workspace. This is normal immediately after an accepted/archived cycle is rolled over for the next fresh run. `/v3/coverage/seek` also returns `has_current_cycle` and the latest archived `previous` summary; Admin displays this state as **Waiting for next run**.


## Bootstrap residual conclusion — JMM-010

The accepted 3-day bootstrap closed with three `INCOMPLETE_CHILD_COVERAGE` roots:

- ACT: 498 covered / 517 reported, 1 incomplete partition;
- NSW: 6,905 / 6,958, 59 incomplete partitions;
- QLD: 6,248 / 6,338, 33 incomplete partitions.

That is **93 residual partition rows** and a 162-count reported-versus-covered delta. Treat 162 as a source-count shortfall, not as proof of exactly 162 missing distinct vacancies. The individual old partition rows are no longer reconstructable: rollover retained only the three summary rows and the pre-rollover DB copy was later pruned by normal backup retention. Do not invent row-level detail.

This coverage residual is separate from JD completeness. At bootstrap close there were 15,020 SEEK jobs, 10,267 with JDs and 4,753 without; the final active JD sweep had 13,633 candidates, 9,938 cached and 3,695 remaining. Bulk JD completion is not required: JMM-003 fetches a missing JD on demand when a consumer actually needs it.

The first normal 1-day cycle started from **0 SEEK partitions and 0 partition memberships** while retaining the three archived summaries and all canonical jobs/JDs, proving the daily workspace was isolated from the accepted 3-day bootstrap. No JMM-010 runtime change is required.
