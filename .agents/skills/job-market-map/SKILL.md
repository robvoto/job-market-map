---
name: job-market-map
description: Use when collecting, maintaining, querying, administering, or extending Rob's neutral local job-market database/API.
---

# Skill: Job Market Map

## Source of truth
Repository: `/home/robvoto/projects/job-market-map`

Canonical runtime DB: `/home/robvoto/projects/job-market-map/data/market.db`

Supported consumer boundary: local versioned API, currently `/v1`.

## Hard boundary
This project is a **neutral collector/index**. Never add Job Hunter, Reset / Edge, Plan Z, CV, application or career-fit policy to ingestion.

Prior search knowledge may improve discovery-query coverage. It must not become collection filtering.

## Collection order
1. Search result cards only.
2. Parse all reliable card-visible fields.
3. Preserve raw card evidence.
4. Same-source identity upsert.
5. Generate duplicate fingerprints/evidence links.
6. Record query hit and raw capture.
7. Do not open JD.
8. Do not score fit.

## Data rules
- Never invent missing card fields; unknown beats inference.
- Source job ID is preferred same-source identity.
- Capture as much reliable card information as the source exposes because rich evidence materially improves duplicate detection.
- Duplicate links are evidence, not destructive merges.
- `first_seen_at` is mapper observation time, not employer posting time.
- Seeing a vacancy again must not reset Rob status flags.
- A tombstoned exact source identity that reappears is previously seen, not newly discovered.

## Consumer/API rule
Normal external consumers such as Job Hunter use the local `/v1` API. Do not couple them to SQLite columns or import mapper Python modules into those projects.

For incremental consumption use `/v1/feed/jobs` and persist `next_cursor` only after safely processing the page. Status writes should include `actor` and `idempotency_key`.

Breaking API semantics require versioning. See `docs/CONSUMER_CONTRACT.md`.


## Geography and SEEK completeness
Current configured whole-state scope is NSW + ACT + QLD. SEEK whole-state coverage is separate from role-keyword discovery. Whole-state partitioning is the primary SEEK collection path; SEEK keyword-registry runs are supplemental and disabled by default to avoid redundant hundreds of searches.

For SEEK, a result partition above `collection.seek_partition_max_results` (default 450) is **incomplete by definition** until split. Split order is state -> SEEK classification -> SEEK subclassification -> work type. Use SEEK's own live refinement links; do not invent classification IDs.

A parent is complete only when all children are complete and their deduplicated union of SEEK job IDs covers the parent's reported count within the configured tolerance. Final oversized leaves remain `INCOMPLETE_OVERSIZE_UNSPLITTABLE`; never relabel them complete to finish a run.

Use `GET /v1/coverage/seek` when a consumer needs proof of coverage. A non-empty feed does not prove the state crawl is complete.

## Named consumers
Job Hunter, Plan Z and Reset / Edge should use independent named API checkpoints rather than creating new local seen/cursor files:
- `/v1/consumers/job-hunter/feed`
- `/v1/consumers/plan-z/feed`
- `/v1/consumers/reset-edge/feed`

Fetching never advances a checkpoint. Advance only after the consumer safely processes the returned page.

## Multi-agent/browser rule
Many agents may read/query concurrently. Keep writes short/idempotent; SQLite WAL is enabled.

For signed-in source collection, reuse the existing HUMAN_MCP_SECURE Rob browser architecture. Do not start another canonical MCP server or another Rob Chrome profile. A full campaign opens one workflow-owned tab inside the existing Rob Chrome and reuses that same page across its source/query steps; do not create one tab per query.

## Admin/settings rule
Routine operational tweaks belong in Admin/settings or operational query rows, not hard-coded constants. See `docs/ADMIN.md` and `config/settings_catalog.json`.

Settings include helper text and validation. Query registry sync must not silently undo Rob's disabled queries.

## Retention
Default lifecycle is rich/current -> archived/compacted -> detailed unimportant row removed -> tombstone retained. Meaningful shown/reviewed/applied/rejected/dismissed history is preserved by default.

Never delete identity memory in a way that allows an old vacancy to return as falsely new. See `docs/RETENTION.md`.

## Documentation discipline
When architecture, schema, retention, API contract, source behaviour or admin settings change, update the relevant documentation in the same change.

## Testing
Every parser, dedupe, cursor, retention, settings, API-contract or lifecycle bug that could recur gets a regression test.

Source parser failures must be explicit; never silently fall back to opening JDs. API consumer tests use isolated temporary databases, never the live market DB.
