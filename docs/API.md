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
GET /v3/jobs/lookup
GET /v3/jobs/{id}
POST /v3/jobs/{id}/jd
GET /v3/coverage/seek
```

`GET /v3/feed/jobs?after_id=<cursor>&limit=<n>` is the incremental neutral feed. It can be filtered by source/geography. Job payloads contain `identity_key` for stable cross-service correlation.

When JMM has obtained a full JD, job payloads also expose the one current neutral JD as `full_description`, `jd_fetched_at`, and `jd_source`. JMM does not expose JD snapshot/version history.

`GET /v3/jobs/lookup?identity_key=<key>` or `GET /v3/jobs/lookup?source=<source>&source_job_id=<id>` is the exact-identity lookup contract (JMM-009). Exactly one form is required — `identity_key` cannot be combined with `source`/`source_job_id`, and `source`/`source_job_id` must both be present together. It never performs title/employer/raw-text search, fuzzy matching, URL similarity or duplicate inference; a successful lookup returns the same job-detail payload as `GET /v3/jobs/{id}` (job identity, captures, query hits and duplicate-link evidence). No exact match returns 404; malformed or conflicting inputs return 400. There is no fallback to `/v3/jobs/search` or direct SQLite access. Duplicate-linked source jobs remain independently resolvable — lookup never follows a duplicate link to substitute another job.

`POST /v3/jobs/{id}/jd` is the supported get-or-enrich operation. If the canonical JD already exists, JMM returns it without reopening the source. If it is missing, JMM selects the source adapter, fetches validated neutral source evidence, stores the JD once, records permanent successful-fetch memory, and returns it. SEEK uses JMM's persistent SEEK browser; LinkedIn uses a direct public-HTTP detail helper and has no browser dependency. LinkedIn market discovery itself remains cards-only. Unsupported sources fail explicitly; consumers must not fetch a JD themselves and write JMM storage directly.

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
GET   /v3/admin/service/status
GET   /v3/admin/stats
GET   /v3/admin/log?lines=500
POST  /v3/admin/browser/seek
POST  /v3/admin/collection/run
POST  /v3/admin/collection/stop
POST  /v3/admin/backup/run
```

Admin UI: `/admin`.

`GET /v3/coverage/seek` keeps the machine-readable current status (`NOT_RUN` when no current workspace exists) and also returns `has_current_cycle` plus the latest archived `previous` coverage summary when available.

## Compatibility

Internal SQLite tables are not a public contract. Breaking semantics require another API version. Consumers must not silently fall back to direct SQLite writes.
