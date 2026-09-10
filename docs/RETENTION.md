# Retention and Staleness

## Principle

Stale market data should become progressively smaller without making the system forget that a source vacancy was seen before.

The lifecycle is **fresh -> archived/compacted -> detailed row removed -> tombstone identity retained**.

All horizons below are admin settings, not Python constants.

## Default lifecycle

### Fresh/current: up to 30 days since `last_seen_at`
Keep:
- canonical job fields;
- latest teaser/raw card text;
- recent card captures;
- query-hit history;
- status flags/events.

### Archive/compact: more than 30 days stale
For jobs with no meaningful Rob state by default:
- set `archived=1`;
- clear bulky canonical `teaser_text` and `raw_card_text`;
- prune raw captures older than the configured raw-capture horizon;
- retain source identity, title, employer, location, fingerprints, first/last seen and query history.

### Remove detailed row: more than 120 days stale
For an already stale/unimportant job:
- write a compact `job_tombstones` row;
- retain source/source job ID, URL, title/employer/location, fingerprints, first/last seen and a compact query-history summary;
- remove the detailed `jobs` row and cascading bulky relationship rows.

This is **not forgetting** the vacancy. If the same exact source identity reappears, ingestion recognises the tombstone, restores a current row, preserves the historical `first_seen_at`, and does not count the vacancy as newly discovered.

## Meaningful Rob state

Default `retention.preserve_status_jobs_forever=true` prevents automatic detailed-row removal when any of these are true:
- shown to Rob;
- reviewed;
- applied;
- rejected;
- dismissed.

This default can be changed by Rob in Admin, but preserving meaningful decision/application history is safer.

## Why not keep everything forever?

Repeated raw observations dominate storage and lose operational value quickly. A tiny identity/tombstone record is enough to prevent an old vacancy from returning as falsely new while keeping the working database lean.

## Admin settings

See `docs/ADMIN.md`. Current defaults:
- `retention.raw_capture_days = 30`
- `retention.archive_after_days = 30`
- `retention.remove_archived_after_days = 120`
- `retention.preserve_status_jobs_forever = true`

The admin layer validates that the removal horizon remains later than the archive horizon.
