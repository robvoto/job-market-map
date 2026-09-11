# Consumer Contract

## Purpose

Job Market Map is a neutral/global discovery and market-feed service for Job Hunter, Reset / Edge, Plan Z and future consumers. Consumers depend on HTTP, not SQLite or mapper Python modules.

Base URL:

```text
http://127.0.0.1:8770/v3
```

## Ownership boundary

Job Market Map owns only shared market infrastructure:
- source identity and canonical URL;
- stable `identity_key`;
- card evidence and parsed card fields;
- first/last seen;
- query provenance;
- duplicate evidence;
- geography and collection completeness;
- archive/tombstone lifecycle;
- per-consumer feed checkpoints.

It does **not** own personal activity or outcomes. `presented_by_agent`, `viewed_by_user`, `applied`, `rejected`, `interview`, `no_response`, and similar facts belong to Job Hunter's per-user activity service under JH-305.

## Incremental feed

```text
GET /v3/feed/jobs?after_id=<cursor>&limit=<n>
```

Persist `next_cursor` only after safely processing the returned items. A feed read is processing, not evidence that a user saw a vacancy.

Named consumers may use:

```text
GET  /v3/consumers/job-hunter/feed
POST /v3/consumers/job-hunter/checkpoint
GET  /v3/consumers/plan-z/feed
POST /v3/consumers/plan-z/checkpoint
GET  /v3/consumers/reset-edge/feed
POST /v3/consumers/reset-edge/checkpoint
```

Each consumer checkpoint is independent. A checkpoint answers only: "how far has this workflow safely processed the shared feed?"

## Canonical JD retrieval

Consumers that need the full JD use:

```text
POST /v3/jobs/{id}/jd
```

This is idempotent get-or-enrich behaviour. JMM returns an existing canonical JD without refetching. If the JD is absent and the source is supported, JMM performs the source-specific fetch through its own adapter/browser infrastructure, stores the one canonical JD write-once, and returns it. SEEK is currently supported. Unsupported sources and source-fetch failures are explicit; consumers do not scrape the source page or write JMM SQLite themselves.

## Personal-history integration

Job Market Map returns stable `identity_key` so an authorised consumer can ask Job Hunter/JH-305 about user-specific history without coupling that history to the market database.

Target questions belong to Job Hunter, for example:
- Has this agent already presented this vacancy to Rob?
- Has another agent presented it to Rob?
- Has Rob actually viewed it?
- Has Rob applied, been rejected, interviewed, or received no response?

Until JH-305 is operational, consumers must continue using their existing authoritative history/application/rejection sources. Do not recreate a temporary canonical activity ledger in Job Market Map.

## Duplicate semantics

`duplicate_link_count` indicates strong possible-duplicate evidence. Source rows remain separate. Consumers may collapse presentation, but should retain source identities.

## Coverage semantics

A non-empty feed does not prove exhaustive coverage. For whole-state SEEK coverage use:

```text
GET /v3/coverage/seek
```

Any incomplete/failed partition means that geography is not proven complete.

## Failure behaviour

If Job Market Map is unavailable, report the feed failure. Do not silently switch to direct SQLite access. If JH-305 is unavailable, do not invent personal-history state in Job Market Map; use the currently authorised legacy history source or report the missing dependency.
