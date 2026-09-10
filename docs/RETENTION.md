# Retention and Staleness

## Principle

Retention is based on neutral market age only. Personal history must not change what Job Market Map considers market data.

## Default lifecycle

- **0–30 days since `last_seen_at`**: retain rich canonical job evidence and recent card captures.
- **More than 30 days stale**: archive/compact the row and clear bulky raw/teaser evidence while preserving market identity/history.
- **More than 120 days stale**: remove the detailed row and retain a small neutral tombstone.

A tombstoned source identity that reappears is resurrected as previously known market identity, not counted as genuinely new.

## Admin settings

- `retention.raw_capture_days`
- `retention.archive_after_days`
- `retention.remove_archived_after_days`

These are runtime settings exposed through Admin/API. There is deliberately no "preserve because Rob applied/rejected/viewed it" setting; that would reintroduce personal-state ownership into the neutral mapper.
