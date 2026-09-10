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
- ACT: 914
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

## Running

```bash
cd /home/robvoto/projects/job-market-map
uv run python -m scripts.run_seek_market_map
```

This runs all enabled states using **one workflow-owned tab inside Rob's existing Chrome**.

Selected states:

```bash
uv run python -m scripts.run_seek_market_map --state NSW --state ACT
```

## Monitoring

```text
GET /v2/coverage/seek
```

Consumers should not assume SEEK is complete when a state status is `NOT_RUN`, `FAILED`, or starts with `INCOMPLETE`.


## Keyword searches are supplemental

The whole-state partitioner is the primary SEEK coverage mechanism. The 109-role query registry is retained for cross-source discovery and optional SEEK provenance, but `collection.seek_keyword_queries_enabled=false` by default prevents hundreds of redundant SEEK searches after the whole-state crawl exists. Rob can enable it in Admin if there is a specific reason to compare keyword-query behaviour.
