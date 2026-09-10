# Data Model

The SQLite schema is internal implementation. Other projects consume the versioned HTTP API, not these tables directly.

## `jobs`

One current canonical row per source vacancy identity.

Important evidence fields:
- `source`, `source_job_id`, `canonical_url`;
- `title`, `employer`, `location`;
- `salary_text`, `employment_type`, `workplace_type`;
- `posted_text`, `posted_at`, `reposted`;
- `applicant_count`, `easy_apply`;
- `teaser_text`, `raw_card_text`;
- `classification_text`, `subclassification_text`, `card_tags_json`;
- `first_seen_at`, `last_seen_at`, `capture_count`.

Identity/duplicate fields:
- `core_fingerprint`: normalized title + employer candidate key; location remains supporting evidence because boards format it differently;
- `exact_card_fingerprint`: rich normalized card evidence fingerprint;
- `possible_same_job_group`: retained compatibility field; `duplicate_links` is the active evidence model.

Shared Rob-state flags:
- `shown_to_rob`;
- `reviewed`;
- `applied`;
- `rejected`;
- `dismissed`.

Lifecycle fields:
- `archived`;
- `compacted_at`.

Flags are independent. A job can be shown and applied, for example.

## `card_captures`

Append-only observations of result cards while within the configured raw-capture retention horizon. Useful for parser debugging, source evidence and change analysis.

## `queries`

Operational discovery-query state. Registry JSON seeds this table; Admin can add/disable rows without changing code. A registry sync updates metadata but does not silently re-enable a query Rob disabled.

## `job_query_hits`

Many-to-many relationship between jobs and discovery queries, including first/last hit and hit count.

## `duplicate_links`

Non-destructive possible-duplicate evidence between two current job rows:
- confidence;
- match type (`exact_rich_card`, `near_rich_card`);
- explicit evidence reasons;
- detection timestamp.

Neither row is automatically deleted or merged. This avoids hiding genuinely separate vacancies while still making cross-posts easy for consumers to collapse visually.

## `job_status_events`

Immutable event history behind the fast status booleans. Supports `actor` + `idempotency_key` so multiple agents can safely retry writes.

## `collection_runs`

Auditable source/query execution state including pages, observations, unique new jobs, duplicate observations and errors.

## `collection_cursors`

Resumable source cursor state. Currently important for LinkedIn's virtualised result list.

## `settings`

Operational admin values plus type, bounds, defaults and helper text. Runtime code reads these values instead of requiring Python edits for normal tuning.

## `job_tombstones`

Minimal identity retained after an old, unimportant detailed job row is removed. Keeps source identity, URL, title/employer/location, fingerprints, historical first/last seen and compact query history so the same old vacancy is not later presented as newly discovered.

## Parsed field rule

Every parsed job/card value must come from source-visible evidence. Missing data remains unknown. The neutral collector does not use an LLM to fill gaps or decide fit.
