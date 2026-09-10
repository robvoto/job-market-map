# Local Agent API

Default local base URL:

```text
http://127.0.0.1:8770/v3
```

`/v3` is the neutral consumer contract. `/v2` was retired before broad consumer integration because it temporarily exposed a personal-activity service that does not belong in Job Market Map.

## Neutral market endpoints

```text
GET /v3/health
GET /v3/stats
GET /v3/feed/jobs
GET /v3/jobs/new
GET /v3/jobs/search
GET /v3/jobs/{id}
GET /v3/coverage/seek
```

`GET /v3/feed/jobs?after_id=<cursor>&limit=<n>` is the incremental neutral feed. It can be filtered by source/geography. Job payloads contain `identity_key` for stable cross-service correlation.

When JMM has obtained a full JD, job payloads also expose the one current neutral JD as `full_description`, `jd_fetched_at`, and `jd_source`. JMM does not expose JD snapshot/version history.

## Consumer checkpoints

```text
GET  /v3/consumers/{consumer_key}/state
GET  /v3/consumers/{consumer_key}/feed
POST /v3/consumers/{consumer_key}/checkpoint
```

Fetching does not advance a checkpoint. Advance only after safe processing. This state belongs here because it describes consumption of this feed, not user behaviour.

## Personal activity

There are intentionally no `/users/.../activity` endpoints. Job Hunter JH-305 is the intended canonical activity/outcome service.

## Admin

```text
GET   /v3/admin/settings
PUT   /v3/admin/settings/{key}
POST  /v3/admin/settings/{key}/reset
GET   /v3/admin/queries
POST  /v3/admin/queries
PATCH /v3/admin/queries/{id}
GET   /v3/admin/geographies
PATCH /v3/admin/geographies/{code}
POST  /v3/admin/retention/run
```

Admin UI: `/admin`.

## Compatibility

Internal SQLite tables are not a public contract. Breaking semantics require another API version. Consumers must not silently fall back to direct SQLite writes.
