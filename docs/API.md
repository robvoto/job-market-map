# Local Agent API

Default local base URL:

```text
http://127.0.0.1:8770/v2
```

`/v2` is the supported consumer contract. The earlier `/v1` contract was retired before consumer integration because it incorrectly embedded Rob-specific status on neutral job rows.

## Neutral market endpoints

```text
GET /v2/health
GET /v2/stats
GET /v2/feed/jobs
GET /v2/jobs/new
GET /v2/jobs/search
GET /v2/jobs/{id}
GET /v2/coverage/seek
```

`GET /v2/feed/jobs?after_id=<cursor>&limit=<n>` is the incremental neutral feed. It may be filtered by `source` and `geography_code`. Job payloads never contain user activity such as shown/applied/rejected.

`identity_key` is the stable market identity. Numeric `id` is the current DB/feed row ID and cursor key.

## Per-user activity

```text
GET  /v2/users/{user_key}/jobs/{job_id}/activity
POST /v2/users/{user_key}/jobs/{job_id}/activity
GET  /v2/users/{user_key}/feed/jobs?exclude_activity=shown&exclude_activity=rejected
```

Example write:

```json
{
  "activity_type": "shown",
  "value": true,
  "actor": "reset-edge",
  "note": "Presented to Rob",
  "idempotency_key": "reset-edge-show-123"
}
```

Supported activity types are `seen`, `shown`, `reviewed`, `applied`, `rejected`, and `dismissed`.

Activity is user-scoped. Writing `shown` for user `rob` does not alter the neutral job payload and has no effect on another user's activity ledger.

## Named consumer checkpoints

```text
GET  /v2/consumers/{consumer_key}/state
GET  /v2/consumers/{consumer_key}/feed
POST /v2/consumers/{consumer_key}/checkpoint
```

Fetching does not advance the checkpoint. Advance only after the consumer safely processes the returned page. Consumer progress is independent of user activity.

## Admin

```text
GET   /v2/admin/settings
PUT   /v2/admin/settings/{key}
POST  /v2/admin/settings/{key}/reset
GET   /v2/admin/queries
POST  /v2/admin/queries
PATCH /v2/admin/queries/{id}
GET   /v2/admin/geographies
PATCH /v2/admin/geographies/{code}
POST  /v2/admin/retention/run
```

The Admin UI is `/admin` and calls the same `/v2` contract.

## Compatibility

Internal SQLite tables/columns are not a public consumer contract. Breaking payload/semantic changes require another API version. Consumers should ignore unknown optional fields and should never fall back silently to direct SQLite writes when the API is unavailable.

The user-scoped feed applies activity filtering server-side while returning the same neutral job payload. It is useful when an agent wants high-volume "not already shown/applied/rejected" retrieval without N per-job activity calls.
