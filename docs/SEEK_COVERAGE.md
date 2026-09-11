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

Otherwise the parent is `INCOMPLETE_CHILD_COVERAGE`.

This makes completeness fail closed.

## Dedupe

SEEK source identity uses SEEK job ID. The same job found through multiple classification/work-type/query paths remains one canonical `jobs` row. `seek_partition_jobs` records every partition that contributed it.

Cross-board duplicates (for example SEEK + LinkedIn) remain separate source rows connected by rich duplicate evidence links; they are not destructively merged.

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


## Live proof status — 10 September 2026

ACT is the first whole-state proof run. Current persisted state after a browser-extension disconnect:
- SEEK root count: **917**;
- **29/30** classifications complete;
- direct-child union: **859** distinct jobs;
- only unfinished classification: Trades & Services, **89** reported / **32** memberships persisted;
- ACT root remains `FAILED` until that classification and parent aggregation finish.

Do not call ACT exhaustive/complete yet. See local-only `docs/CURRENT_STATE.md` if present for the exact recovery point; the handoff is intentionally not tracked in Git.
