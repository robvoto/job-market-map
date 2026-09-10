# Local Agent API

## Purpose

The API is the supported integration boundary for Job Hunter, Reset / Edge, Plan Z and future consumers. Consumers should not read/write `market.db` directly.

Default local base URL:

```text
http://127.0.0.1:8770/v1
```

The port is admin-configurable and is read by `scripts/start-api.sh` at startup.

Interactive OpenAPI documentation is available at `/docs`; Rob's runtime controls are at `/admin`.

## Versioning

`/v1` is the stable consumer contract. Internal SQLite tables are not API contracts.

Breaking semantics require a new API version. Adding optional response fields is allowed within v1.

## Consumer feed

### `GET /v1/feed/jobs`

Parameters:
- `after_id`: stable monotonically increasing cursor; default `0`;
- `limit`: bounded page size, constrained by admin settings;
- `source`: optional source filter;
- `include_archived`: default false;
- `include_raw`: include latest raw card text, default true.

Response:

```json
{
  "api_version": "v1",
  "schema_version": 1,
  "generated_at": "...",
  "items": [],
  "next_cursor": 123,
  "has_more": true
}
```

Persist `next_cursor` only after the consumer safely processes the page. Retrying the same cursor is safe.

See `docs/CONSUMER_CONTRACT.md`.

## Job reads

- `GET /v1/jobs/new?days=1`
- `GET /v1/jobs/search`
- `GET /v1/jobs/{id}` — includes recent captures, query hits and possible duplicate evidence.

## Status writes

### `POST /v1/jobs/{id}/status`

```json
{
  "field": "shown_to_rob",
  "value": true,
  "actor": "job-hunter",
  "note": "Presented to Rob",
  "idempotency_key": "job-hunter-show-123"
}
```

Allowed status flags:
- `shown_to_rob`
- `reviewed`
- `applied`
- `rejected`
- `dismissed`

The same `actor + idempotency_key` can be retried without duplicating the event.

## Operational/admin endpoints

- `GET /v1/stats`
- `GET /v1/runs`
- `GET /v1/admin/settings`
- `PUT /v1/admin/settings/{key}`
- `POST /v1/admin/settings/{key}/reset`
- `GET /v1/admin/queries`
- `POST /v1/admin/queries`
- `PATCH /v1/admin/queries/{id}`
- `POST /v1/admin/retention/run`

Admin helper text and validation are driven by `config/settings_catalog.json` plus the operational SQLite settings table.


## Named consumer feeds

Consumers can let Job Market Map store their independent cursor:

```text
GET  /v1/consumers/{consumer_key}/state
GET  /v1/consumers/{consumer_key}/feed
POST /v1/consumers/{consumer_key}/checkpoint
```

Fetching never advances the checkpoint. Consumers advance only after successful processing.

## Geography and coverage

- Feed/search endpoints accept `geography_code=NSW|ACT|QLD`.
- `GET /v1/coverage/seek` reports whole-state partition completeness.
- `GET /v1/admin/geographies` lists configured states.
- `PATCH /v1/admin/geographies/{code}` enables/disables a state.

A non-empty feed is not proof that SEEK coverage is complete; use the coverage endpoint when completeness matters.
