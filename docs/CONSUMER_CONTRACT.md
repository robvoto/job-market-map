# Consumer Contract

## Purpose

Job Market Map is a neutral discovery/feed service for Job Hunter, Reset / Edge, Plan Z and future agents. Consumers depend on HTTP, not SQLite or mapper Python modules.

Base URL:

```text
http://127.0.0.1:8770/v2
```

## Ownership boundary

Job Market Map neutral market storage owns source identity/URL, card evidence, first/last seen, query provenance, duplicate evidence, geography/coverage and archive/tombstone lifecycle.

Per-user interaction is a separate activity ledger. Job Hunter/Reset/Plan Z still own fit, blockers, title/domain rules, JD opening, scoring, recommendation and application workflow.

## Incremental feed

```text
GET /v2/feed/jobs?after_id=<cursor>&limit=<n>
```

Persist `next_cursor` only after safely processing all returned items. This provides at-least-once delivery. A feed read is **not** a `seen` or `shown` event.

Named consumers may instead use:

```text
GET  /v2/consumers/job-hunter/feed
POST /v2/consumers/job-hunter/checkpoint
GET  /v2/consumers/plan-z/feed
POST /v2/consumers/plan-z/checkpoint
GET  /v2/consumers/reset-edge/feed
POST /v2/consumers/reset-edge/checkpoint
```

Each consumer checkpoint is independent.

## User activity

When an actual user interaction occurs, write it separately:

```text
POST /v2/users/rob/jobs/{job_id}/activity
```

Examples: `shown`, `seen`, `reviewed`, `applied`, `rejected`, `dismissed`. Use a stable actor + idempotency key when retries are possible.

Do not infer user activity from collection, feed retrieval, consumer checkpoint progress or duplicate detection.

## Duplicate semantics

`duplicate_link_count` indicates strong possible-duplicate evidence. Source rows stay separate. Consumers may collapse presentation but should retain source identities.

## Coverage semantics

Useful jobs may be available before an exhaustive crawl finishes. For SEEK completeness use:

```text
GET /v2/coverage/seek
```

Do not equate a non-empty feed with complete state coverage.

## Failure behaviour

If the API is unavailable, report the feed failure. Do not silently switch to direct SQLite access because that bypasses lifecycle, API versioning and activity boundaries.


## Efficient activity-aware retrieval

For high-volume consumers, avoid one activity lookup per job. Use:

```text
GET /v2/users/rob/feed/jobs?exclude_activity=shown&exclude_activity=applied&exclude_activity=rejected
```

Or combine the same filtering with a named consumer checkpoint:

```text
GET /v2/consumers/reset-edge/feed?user_key=rob&exclude_activity=shown&exclude_activity=rejected
```

The returned `items` remain neutral market-job payloads; activity is used only to select which rows are returned.
