# Local Agent API

Default local base URL:

```text
http://127.0.0.1:8770/v3
```

`/v3` is the neutral consumer contract. `/v2` was retired before broad consumer integration because it temporarily exposed a personal-activity service that does not belong in Job Market Map.

## Neutral market endpoints

```text
GET /v3/health
GET /v3/readiness
GET /v3/stats
GET /v3/feed/jobs
GET /v3/jobs/new
GET /v3/jobs/search
GET /v3/jobs/lookup
GET /v3/jobs/{id}
GET /v3/jobs/{id}/jd
POST /v3/jobs/{id}/jd
GET /v3/coverage/seek
```

`GET /v3/readiness` is the read-only consumer readiness contract. It reports JMM-owned source-run facts for SEEK and LinkedIn (status plus available run timestamps) and mutually exclusive JD coverage counts across active canonical vacancies: `available`, `missing_not_cached`, and `failed`. It does not trigger collection, JD enrichment, retries, or any other mutation. Consumers may use explicit failed/partial/not-run source state to surface degradation, but the endpoint does not define an acceptable cache-miss percentage or invent a freshness threshold.

`GET /v3/feed/jobs?after_id=<cursor>&limit=<n>` is the incremental neutral feed. It can be filtered by source/geography. The first page returns `snapshot_max_id`; a multi-page caller may pass that value back as `through_id` on later pages to keep one run on a fixed market boundary. Job payloads contain `identity_key` for stable cross-service correlation.

`GET /v3/jobs/search` is the stateless, bounded filtered-search contract. It accepts repeated `q`, `source`, `geography_code`, `location`, `classification`, `subclassification`, `employment_type`, `workplace_type`, `apply_method`, and `company` parameters plus `posted_after`, `salary_min`, `salary_max`, `salary_period`, `salary_currency`, `after_id`, and `through_id`. Every response is one admin-bounded page, including an empty/browse-style search; `limit` cannot exceed `api.max_page_size`. Each filter is evaluated against any active linked source row; matching vacancies are returned once as their canonical primary. Each item also includes `matched_sources`, the exact source postings that satisfied all supplied filters, so a match found on an alias is auditable without copying alias fields onto the canonical payload. For salary searches, each matched source also returns its own structured salary facts, raw salary field state, and whether it matched by comparable overlap or was preserved because the facts were uncertain. The response's `total` is the count of those canonical vacancies within the fixed snapshot and is independent of the page cursor. Search does not create or advance a consumer checkpoint.

`posted_after` accepts an ISO date or timestamp. JMM compares parsed SQLite Julian dates, so date-only values and timestamps with offsets are handled as dates/times rather than text. A posting is returned only when its `posted_at` field state is `known` and its stored value is parseable; the response's `posted_at_basis` identifies exact source dates, capture-time conversions of source-relative labels, and conservative source-query freshness bounds. Unknown, not-present, malformed, and missing dates fail closed. Invalid request dates return HTTP 400. A supplied `geography_code` follows neutral field-state semantics: known values must match, while unknown, not-applicable, and legacy rows without a state remain eligible because their geography is not proven different; `not_present` values do not match.

Salary bounds must be accompanied by an explicit `salary_period` (`hour`, `day`, `week`, `month`, or `year`) and three-letter `salary_currency` such as `AUD`; orphan period/currency parameters return HTTP 400. This target period and currency define when numeric comparisons are safe, not a reason to discard other vacancies. JMM never converts or compares `$100/hour` with `$120,000/year`, and it never converts currencies. A known candidate with the same period and currency is tested for inclusive interval overlap. An exact salary has equal lower/upper values, a range has both bounds, `from` has only a lower bound, and `up_to` has only an upper bound. For example, a minimum of `130000 AUD/year` includes `From $150k`, `$140k–$160k`, and `Up to $150k`; it excludes `Up to $120k`. Unknown salary facts, missing/different period or currency, stale/non-known raw salary states, and unparseable legacy rows remain eligible for downstream review. JMM excludes a vacancy only when comparable canonical facts prove its interval cannot meet the requested bound.

Repeated filter values are limited to 100 per parameter and 300 characters per value; excess input returns HTTP 400 before SQL construction. `q` expressions retain their existing stricter expression and length limits.

Repeated `q` expressions are deterministic OR terms. Within one expression, `AND` binds more tightly than `OR`; parentheses, quoted phrases, and field scopes such as `title:"delivery manager"`, `company:acme`, and `description:automation` are supported. Search expressions are intentionally bounded and unsupported field names fail with HTTP 400. JMM does not interpret fit, skills, history, ranking, or LLM meaning.

When JMM has obtained a full JD, job payloads also expose the one current neutral JD as `full_description`, `jd_fetched_at`, and `jd_source`. JMM does not expose JD snapshot/version history.

`GET /v3/jobs/lookup?identity_key=<key>` or `GET /v3/jobs/lookup?source=<source>&source_job_id=<id>` is the exact-identity lookup contract (JMM-009). Exactly one form is required — `identity_key` cannot be combined with `source`/`source_job_id`, and `source`/`source_job_id` must both be present together. It never performs title/employer/raw-text search, fuzzy matching, URL similarity or duplicate inference; a successful lookup returns the same job-detail payload as `GET /v3/jobs/{id}` (job identity, captures, query hits, possible duplicate evidence and any auditable same-vacancy primary link). No exact match returns 404; malformed or conflicting inputs return 400. There is no fallback to `/v3/jobs/search` or direct SQLite access. Source postings assigned to a same vacancy remain independently resolvable — lookup never substitutes the primary for the requested source identity.

`GET /v3/jobs/{id}/jd` is the cached-only JD consumer contract. It never enriches. A cached canonical JD returns HTTP 200 with `full_description`, `jd_fetched_at`, and `jd_source`; a current job without a cached JD returns HTTP 409 `JD not cached yet`; a terminally unavailable vacancy returns HTTP 410. Job Hunter uses this GET-only path and treats an ordinary 409 as a per-job cache miss rather than a JMM outage. Transport/server failures remain infrastructure failures.

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
and `future` adapters. Job payloads include a complete `field_states` map and a
`salary_normalized` object. The latter is populated only from deterministic,
testable source text; `salary_text` remains the source evidence. Its `state` is
`known`, `not_present`, `unknown`, or `not_applicable`; `bound` is `exact`,
`range`, `from`, or `up_to` when a numeric shape is proven. Blank text alone is
`unknown`: `not_present` requires a positive source/card observation. The
`field_states.salary` value describes whether raw source salary evidence is
present; `salary_normalized.state` separately describes whether that evidence
could be normalized safely.

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
`not_present` row does not match, even if an older stored value is retained as
evidence.
