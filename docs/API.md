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

`GET /v3/feed/jobs?after_id=<cursor>&limit=<n>` is the incremental neutral feed. It can be filtered by source/geography. The first page returns `snapshot_max_id`; a multi-page caller may pass that value back as `through_id` on later pages to keep one run on a fixed market boundary. Job payloads contain `identity_key` for stable cross-service correlation.

`GET /v3/jobs/search` is the stateless, bounded filtered-search contract. It accepts repeated `q`, `source`, `geography_code`, `location`, `classification`, `subclassification`, `employment_type`, `workplace_type`, `apply_method`, and `company` parameters plus `posted_after`, `after_id`, and `through_id`. Every response is one admin-bounded page, including an empty/browse-style search; `limit` cannot exceed `api.max_page_size`. Each filter is evaluated against any active linked source row; matching vacancies are returned once as their canonical primary. The response's `total` is the count of those canonical vacancies within the fixed snapshot and is independent of the page cursor. Search does not create or advance a consumer checkpoint.

Repeated `q` expressions are deterministic OR terms. Within one expression, `AND` binds more tightly than `OR`; parentheses, quoted phrases, and field scopes such as `title:"delivery manager"`, `company:acme`, and `description:automation` are supported. Search expressions are intentionally bounded and unsupported field names fail with HTTP 400. JMM does not interpret fit, skills, history, ranking, or LLM meaning.

When JMM has obtained a full JD, job payloads also expose the one current neutral JD as `full_description`, `jd_fetched_at`, and `jd_source`. JMM does not expose JD snapshot/version history.

`GET /v3/jobs/lookup?identity_key=<key>` or `GET /v3/jobs/lookup?source=<source>&source_job_id=<id>` is the exact-identity lookup contract (JMM-009). Exactly one form is required — `identity_key` cannot be combined with `source`/`source_job_id`, and `source`/`source_job_id` must both be present together. It never performs title/employer/raw-text search, fuzzy matching, URL similarity or duplicate inference; a successful lookup returns the same job-detail payload as `GET /v3/jobs/{id}` (job identity, captures, query hits, possible duplicate evidence and any auditable same-vacancy primary link). No exact match returns 404; malformed or conflicting inputs return 400. There is no fallback to `/v3/jobs/search` or direct SQLite access. Source postings assigned to a same vacancy remain independently resolvable — lookup never substitutes the primary for the requested source identity.

`POST /v3/jobs/{id}/jd` is the supported get-or-enrich operation. If the canonical JD already exists, JMM returns it without reopening the source. If it is missing, JMM selects the source adapter, fetches validated neutral source evidence, stores the JD once, records permanent successful-fetch memory, and returns it. SEEK uses JMM's persistent SEEK browser; LinkedIn uses a direct public-HTTP detail helper and has no browser dependency. LinkedIn discovery remains card-first, then newly discovered jobs are queued for JD enrichment. Unsupported sources fail explicitly; consumers must not fetch a JD themselves and write JMM storage directly.

When every linked source explicitly reports the vacancy unavailable, JMM records the terminal source status, removes that vacancy from normal active feeds, and returns HTTP 410 from this endpoint. Transient fetch failures remain HTTP 502 and are retryable.

## Consumer checkpoints

```text
GET  /v3/consumers/{consumer_key}/state
GET  /v3/consumers/{consumer_key}/feed
POST /v3/consumers/{consumer_key}/checkpoint
```

Fetching does not advance a checkpoint. Advance only after safe processing. The consumer feed also accepts optional `through_id`: capture `snapshot_max_id` from the first page of a run and send that same value on every later page in that run. The high-water is transient run state, not a persisted checkpoint field or personal activity. If a run fails, the next run starts a fresh snapshot boundary from the last safely saved checkpoint. This state belongs here because it describes consumption of this feed, not user behaviour.

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

## Field states and source capabilities

`GET /v3/capabilities/fields` exposes the neutral searchable fields, the four
field states, and source capability metadata for `seek`, `linkedin`, `apsjobs`
and `future` adapters. Job payloads include a complete `field_states` map.

The states are explicit:

- `known`: source-backed evidence supplied a value; boolean `false` is still known.
- `not_present`: the relevant source evidence was checked and did not provide a value.
- `unknown`: JMM cannot determine the value safely.
- `not_applicable`: the field does not apply to that source/adapter.

Blank or `NULL` is not a state and never automatically means false or
`not_present`. `unknown` and `not_applicable` remain available to neutral
cross-source search consumers; only an explicit `not_present` is treated as
source-checked absence. Capability `unknown` is conservative and does not mean
the source is unsupported.

This applies equally to structured filters and field-scoped keyword terms such
as `q=workplace_type:Hybrid`. An unresolved or not-applicable source row stays
in that field-scoped result for downstream interpretation; an explicit
`not_present` row does not match.
