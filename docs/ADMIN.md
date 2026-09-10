# Admin Controls

## UI

Start the API and open:

```text
http://127.0.0.1:<configured-port>/admin
```

The page exposes runtime settings with helper text and discovery-query enable/disable controls.

## What is configurable without code

### Retention
- raw card capture days;
- archive-after days;
- remove-detailed-row-after days;
- whether jobs with meaningful Rob status are preserved indefinitely.

Default lifecycle:

```text
0–30 days             rich/current
>30 days stale         archived + bulky card text compacted
>120 days stale        detailed unimportant row removed -> tiny tombstone retained
shown/reviewed/etc.    preserved by default
```

The exact `30` and `120` values are defaults, not hard-coded policy. Change them in Admin.

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

`GET /v1/admin/geographies` and `PATCH /v1/admin/geographies/{code}` provide the same control for agents/admin tooling.

## SEEK completeness controls

- `collection.seek_partition_max_results` — default 450; oversized partitions must split and cannot be reported complete.
- `collection.seek_completion_count_tolerance` — default 5; small live-market count drift tolerated when reconciling a leaf or parent union.

These settings affect completeness semantics, so helper text is displayed in Admin. See `docs/SEEK_COVERAGE.md` before changing them.
