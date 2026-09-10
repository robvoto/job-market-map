# Data Model

## Neutral market domain

### `jobs`

One current canonical row per source vacancy identity. This table contains **market facts only**: source/source job ID, stable `identity_key`, URL, title, employer, location/geography, salary/work type, posting metadata, card evidence, classification, first/last seen, fingerprints and archive state.

It does **not** contain `shown`, `seen`, `reviewed`, `applied`, `rejected` or `dismissed`. Those facts describe a user's relationship with a vacancy, not the vacancy itself.

`identity_key` is stable across archive/tombstone/resurrection. It uses `source:id:<source_job_id>` where a source ID exists, otherwise a source+canonical-URL fallback.

### `card_captures`

Append-only recent source observations used for parser evidence/debugging. These are intentionally retained for less time than canonical identity.

### `queries` / `job_query_hits`

Neutral discovery provenance: which source/search/geography found a vacancy and how often. Query origin is discovery metadata, not fit judgement.

### `duplicate_links`

Non-destructive evidence that two source rows may represent the same vacancy. Rows are never destructively merged merely because their cards look alike.

### `job_tombstones`

Small neutral identity/history record retained after an old unimportant rich job row is removed. Tombstones contain no user activity.

## Per-user activity domain

### `user_job_activity_events`

Immutable activity ledger keyed by `user_key + job_identity_key`. Supported activity types currently include:

- `seen`
- `shown`
- `reviewed`
- `applied`
- `rejected`
- `dismissed`

Each event records value, timestamp, actor, note and optional idempotency key. This is explicitly separate from `jobs`.

### `user_job_activity_current`

A fast current-state projection of the event ledger, keyed by `user_key + job_identity_key + activity_type`. The event ledger remains the history; this table exists so consumers do not need expensive latest-event queries.

Activity uses stable `job_identity_key`, not temporary numeric `jobs.id`, so history survives rich-row archival/resurrection.

## Consumer progress domain

### `consumer_checkpoints`

Agent/workflow progress through the market feed. `job-hunter`, `plan-z`, and `reset-edge` can each have independent cursors. A checkpoint means "this consumer safely processed through this feed ID"; it does not mean Rob saw or reviewed the job.

## Boundary rule

Do not add user-specific state to neutral market rows for convenience. If a fact answers "what did this user/agent do with this vacancy?", it belongs in activity/progress, not `jobs`.
