# Retention and Staleness

## Principle

A stale vacancy should consume less storage without making the system forget its identity. User activity is separate from neutral market data and must not be copied into job/tombstone columns.

## Default lifecycle

### 0–30 days since `last_seen_at`
Keep rich canonical job evidence and recent card captures.

### More than 30 days stale
For neutral jobs with no protected activity history, archive/compact the row: clear bulky raw/teaser evidence while retaining identity, source, title/employer, dates, fingerprints and discovery history.

### More than 120 days stale
For old archived jobs with no protected activity history, remove the detailed row and retain a small neutral tombstone. Rediscovery resurrects the vacancy as previously seen by the market mapper rather than a genuinely new source identity.

## Activity preservation

Default `retention.preserve_activity_jobs_forever=true` prevents automatic archive/removal of a rich job row when **any user activity event** exists for its stable `identity_key`.

The activity itself remains in `user_job_activity_events`; it is not moved into the neutral job or tombstone.

## Admin settings

- `retention.raw_capture_days`
- `retention.archive_after_days`
- `retention.remove_archived_after_days`
- `retention.preserve_activity_jobs_forever`

These are runtime settings exposed through Admin/API.
