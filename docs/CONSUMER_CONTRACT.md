# Consumer Contract

## Purpose

Job Market Map may become the discovery feed for Job Hunter and other agents. Consumers must depend on the **HTTP API contract**, not the SQLite schema or Python modules in this repository.

Base URL by default:

```text
http://127.0.0.1:8770/v1
```

The port is admin-configurable.

## Ownership boundary

Job Market Map owns factual market collection:
- source identity and URL;
- result-card fields/evidence;
- first/last seen;
- query discovery history;
- strong possible-duplicate links;
- shared human-status flags/events;
- archive/tombstone lifecycle.

A consumer such as Job Hunter owns:
- candidate/profile fit;
- hard blockers;
- title policy;
- domain/platform requirements;
- JD opening and deeper interpretation;
- scoring/ranking;
- application workflow.

The feed must never pre-filter jobs merely because one consumer would reject them.

## Incremental feed

Use:

```text
GET /v1/feed/jobs?after_id=<cursor>&limit=<n>
```

The response contains:
- `api_version`: route contract version;
- `schema_version`: payload schema version;
- `items`: canonical current job rows;
- `next_cursor`: highest returned job ID;
- `has_more`: whether another page is currently available;
- `generated_at`: UTC response time.

### Cursor rule

A consumer persists `next_cursor` **only after it has safely processed the returned items**. If processing fails, retry the same cursor. This gives at-least-once delivery without losing jobs.

`after_id` is intentionally independent of `first_seen_at`. If a tombstoned vacancy later reappears, it gets a current row again and can be delivered without pretending its historical first-seen time is new.

## Updates versus new canonical rows

The v1 feed is primarily a **canonical-row discovery feed**. Existing rows may be refreshed in place as the same source vacancy is seen again. Consumers that need every observation change should use a future observation/event endpoint rather than polling SQLite.

Do not infer “job changed” merely because `last_seen_at` moved.

## Raw card evidence

`include_raw=true` includes the latest retained `raw_card_text`. It can be disabled for lighter polling. Parsed fields remain source evidence, not LLM inference.

## Duplicate semantics

`duplicate_link_count` means the mapper has one or more strong **possible duplicate** links. It does not mean rows were merged.

Retrieve a job detail to see duplicate confidence/reasons. Consumers may choose to collapse presentation, but should retain source identities internally.

## Status writes

Use:

```text
POST /v1/jobs/{job_id}/status
```

Shared status fields are:
- `shown_to_rob`
- `reviewed`
- `applied`
- `rejected`
- `dismissed`

Use an `actor` and stable `idempotency_key`. Retrying the same write with the same actor/key returns the original event instead of adding duplicate history.

A consumer must not set `shown_to_rob` merely because it fetched a feed page. Set it only when the role was actually presented to Rob.

## Compatibility policy

- Breaking payload/semantic changes require `/v2` or a schema-version transition with migration notes.
- Adding optional fields to v1 is allowed.
- Internal SQLite tables/columns are not part of the consumer contract.
- Consumers should ignore unknown optional fields.
- Removing or changing meaning of an existing v1 field is not allowed without a version change.

## Failure behaviour

If Job Market Map is unavailable, a consumer must report that feed failure explicitly. It must not silently switch to direct SQLite access because that bypasses lifecycle, duplicate and admin rules.

If the consumer has its own legacy scraper fallback, that is a consumer-owned product decision and must be explicit; it is not part of the Job Market Map API contract.
