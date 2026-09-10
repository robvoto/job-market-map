# Admin Controls

## UI

Start the API and open:

```text
http://127.0.0.1:<configured-port>/admin
```

The page exposes runtime settings with helper text and discovery-query enable/disable controls.

## What is configurable without code

### Retention
Destructive retention is **OFF by default**. Admin exposes an explicit switch for each destructive phase plus its age threshold:

- `retention.prune_raw_captures_enabled` + `retention.raw_capture_days`;
- `retention.archive_jobs_enabled` + `retention.archive_after_days`;
- `retention.remove_archived_jobs_enabled` + `retention.remove_archived_after_days`.

Default behaviour is to preserve canonical job/card evidence indefinitely. The numeric defaults (`30`, `30`, `120`) are future cleanup thresholds only; they do nothing until the matching switch is turned on. This is deliberate because historical card evidence may later support audit, scam/phishing investigation, repost analysis and learning from application outcomes.

Personal applied/rejected/presented state is not a retention exception here because it is not owned by Job Market Map; Job Hunter/JH-305 owns personal activity.

### Collection
- default freshness horizon;
- SEEK page settling, parser wait and safety-page guard;
- LinkedIn settling, parser wait and resumable chunk size;
- campaign source/query chunk size (execution bound only, never a market-coverage limit).

### Duplicate detection
- near-match enable/disable;
- near-match confidence threshold.

### API
- default/max API page sizes;
- local API port.

### Query discovery
Queries are operational DB data. Admin can enable/disable them; API clients can also add a new source/query/location combination without Python changes. Registry sync does not silently re-enable a query Rob disabled.

## Safety semantics

A safety limit is not a market-coverage limit. If SEEK reaches its safety page limit before an explicit terminal state, the run is failed/incomplete.

Disabling a query stops future collection but does not delete jobs or query history already gathered through it.


## Geography scope

Whole-state collection scope is stored as admin data. Current enabled geographies are NSW, ACT and QLD. The Admin page can enable/disable a configured geography without changing Python.

`GET /v3/admin/geographies` and `PATCH /v3/admin/geographies/{code}` provide the same control for agents/admin tooling.

## SEEK completeness controls

- `collection.seek_partition_max_results` — default 450; oversized partitions must split and cannot be reported complete.
- `collection.seek_completion_count_tolerance` — default 5; small live-market count drift tolerated when reconciling a leaf or parent union.

These settings affect completeness semantics, so helper text is displayed in Admin. See `docs/SEEK_COVERAGE.md` before changing them.


`collection.seek_keyword_queries_enabled` is off by default. Whole-state partition coverage is the primary SEEK feed; turning this on adds the role-keyword SEEK registry as supplemental collection and substantially increases request volume.
