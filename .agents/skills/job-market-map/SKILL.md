---
name: job-market-map
description: Use when collecting, maintaining, querying, administering, or extending Rob's neutral local job-market database/API.
---

# Skill: Job Market Map

## Source of truth
Repository: `/home/robvoto/projects/job-market-map`
Canonical runtime DB: `/home/robvoto/projects/job-market-map/data/market.db`
Supported consumer API: `/v3`.

For a continuation/new session, read local-only `docs/CURRENT_STATE.md` first **if present** for the latest verified crawl state, blocker and exact resume point. Keep that handoff current locally, but do not commit or push it to Git.

## Hard ownership boundary
Job Market Map is neutral/global market infrastructure. It owns collection, source identity, evidence, duplicate links, coverage, retention and consumer checkpoints.

It does **not** own personal activity/outcomes. Do not add `shown`, `seen`, `presented_by_agent`, `viewed_by_user`, `applied`, `rejected`, `interview`, `no_response` or equivalent user state to jobs, tombstones or a parallel canonical ledger here. Job Hunter JH-305 is the intended owner of that domain.

## Collection order
1. Search result cards only.
2. Parse all reliable card-visible fields.
3. Preserve raw card evidence.
4. Same-source identity upsert.
5. Generate non-destructive duplicate evidence links.
6. Record query/partition provenance.
7. Do not open JD.
8. Do not score fit.

Unknown beats inference. Capture rich card evidence because it materially improves cross-source duplicate detection.

## API/consumer rule
External consumers use `/v3`; never couple them to SQLite columns. `identity_key` is the stable market reference for joining to Job Hunter/JH-305 personal history.

Named feed checkpoints belong here:
- `/v3/consumers/job-hunter/feed`
- `/v3/consumers/plan-z/feed`
- `/v3/consumers/reset-edge/feed`

Fetching does not advance a checkpoint. A checkpoint is processing progress only and must never be interpreted as user exposure.

## SEEK geography/completeness
Configured scope: whole NSW + ACT + QLD.

Primary SEEK coverage is state-wide partitioning, not keyword searches. A partition above `collection.seek_partition_max_results` (default 450) is incomplete until split. Split hierarchy: state -> SEEK classification -> SEEK subclassification -> work type. Final oversized leaves remain explicitly incomplete.

A parent is complete only when all children are complete and their deduplicated union covers the parent reported count within configured tolerance. `/v3/coverage/seek` is the coverage proof; a non-empty feed is not proof.

## Multi-agent/browser rule
Many agents may consume concurrently. SQLite WAL is enabled and normal consumers use HTTP. Signed-in collection uses the existing Human MCP Rob browser; a collection workflow owns/reuses one tab and does not start another canonical MCP server/browser profile.

## Admin/settings
Operational knobs belong in settings/admin with helper text and validation, not scattered constants. Retention, collection timing, partition thresholds, API page sizes, geography enablement and query enablement are admin-manageable where practical.

## Retention
Market lifecycle is rich/current -> archived/compacted -> neutral tombstone. Retention is based on market age, not Rob activity. Never delete identity memory in a way that makes an old source vacancy falsely new.

## Testing/documentation
Every parser, partition, dedupe, cursor, retention, settings or API-contract bug gets a regression test. Update docs with architecture/API changes in the same commit. Source parser failures must be explicit; never silently fall back to JD opening.

## Scheduler / service / backup rule
Use the project-owned in-app scheduler, not Windows Task Scheduler. The background Admin/API service is started with `./scripts/service.sh start`; scheduled and manual collection both invoke the same collection-cycle runner. `data/collection.lock` is the cross-process authority preventing overlap.

Create/verify an online SQLite backup before collection by default. Backup policy is admin-configurable, but do not disable or bypass it casually. The scheduler currently owns only the proven whole-state SEEK stage; do not add LinkedIn whole-registry scheduling until campaign-level continuation is implemented and tested.
