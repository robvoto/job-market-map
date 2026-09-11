---
name: job-market-map
description: Use when collecting, maintaining, querying, administering, or extending Rob's neutral local job-market database/API — collection, source identity, JD ownership, API/consumer contract, coverage, and retention.
---

# Skill: Job Market Map

## Source of truth
Repository: `/home/robvoto/projects/job-market-map`
Canonical runtime DB: `/home/robvoto/projects/job-market-map/data/market.db`
Supported consumer API: `/v3`.

For a continuation/new session, read local-only `docs/CURRENT_STATE.md` first **if present** for the latest verified crawl state, blocker and exact resume point. Keep that handoff current locally, but do not commit or push it to Git.

For canonical backlog access, use `.agents/skills/backlog-management/SKILL.md`. For repository/filesystem/Sheets tooling failures, use `.agents/skills/mcp-tooling/SKILL.md`.

## Fast continuation / indexing
For every new JMM session, use this order before broad searching:
1. Read `docs/CURRENT_STATE.md`.
2. Read this skill.
3. Read the live backlog rows relevant to the requested JMM IDs (`.agents/skills/backlog-management/SKILL.md`).
4. Inspect only the owning code/docs/tests for those IDs.

Keep `docs/CURRENT_STATE.md` current with approved architecture decisions, exact blockers and next work so future sessions do not rediscover settled context.

## Hard ownership boundary
Job Market Map is neutral/global market infrastructure. It owns collection, source identity, evidence, duplicate links, coverage, retention and consumer checkpoints.

It does **not** own personal activity/outcomes. Do not add `shown`, `seen`, `presented_by_agent`, `viewed_by_user`, `applied`, `rejected`, `hidden`, `liked`, `interview`, `no_response` or equivalent user state to jobs, tombstones or a parallel canonical ledger here. Job Hunter JH-305 is the intended owner of that domain.

## JD ownership — approved simple model
JMM owns the one neutral/current raw JD for a job. Assume the JD does not change; do not build JD version history, multiple snapshots, snapshot IDs, hashes for historical reconstruction, or a historical JD ledger.

Canonical `jobs` rows contain source vacancy facts, not collector calculations. `posted_at` is stored only from an exact source timestamp (for SEEK, `listedAt.dateTimeUtc`). Never derive it from relative labels such as `3h ago`. Relative labels belong only in raw capture evidence. Collector lifecycle (`first_seen_at`, `last_seen_at`, `capture_count`, archive/compaction state) belongs in `job_observation_state`, not `jobs`. Keep the exact source work type in `employment_type`; do not infer Permanent/Contract basis from ambiguous labels or JD prose.

Target persistence on `jobs`:
- `full_description`
- `jd_fetched_at`
- `jd_source`

Behaviour:
- a successful JD fetch is remembered permanently by canonical identity, independently of whether bulky JD text is later retained;
- if that permanent marker exists, never fetch that source job again during normal collection;
- if the marker is absent, fetch the JD once, store it in JMM, record the permanent marker, and reuse it thereafter;
- the one-off first SEEK load uses a 3-day window; normal ongoing SEEK discovery uses a 1-day window;
- Job Hunter reads the neutral JD from JMM for analysis and must not permanently maintain another duplicate raw JD copy.

## SEEK collection order
1. Read the configured recent SEEK result window (normally 1 day; first live load explicitly 3 days).
2. Parse reliable card-visible identity/evidence.
3. For a known source job ID, record current coverage/operational last-seen only; do not re-ingest the full card or reopen its JD.
4. For a new job, create the canonical market record and preserve its source card evidence.
5. If the permanent JD-fetch marker is absent, open the job page once, extract the neutral JD plus explicit structured neutral source facts, store them fill-only, and record successful fetch memory. Failed/human-check fetches create no success marker.
6. Generate non-destructive duplicate evidence links and query/partition provenance.
7. Do not score fit or add personal activity.

Unknown beats inference. Capture rich card evidence because it materially improves cross-source duplicate detection.

## API/consumer rule
External consumers use `/v3`; never couple them to SQLite columns. `identity_key` is the stable market reference for joining to Job Hunter/JH-305 personal history. Full contract: `docs/API.md` and `docs/CONSUMER_CONTRACT.md`.

Named feed checkpoints belong here:
- `/v3/consumers/job-hunter/feed`
- `/v3/consumers/plan-z/feed`
- `/v3/consumers/reset-edge/feed`

Fetching does not advance a checkpoint. A checkpoint is processing progress only and must never be interpreted as user exposure.

`GET /v3/jobs/lookup` (JMM-009) is the only supported exact-identity resolution path (`identity_key`, or `source`+`source_job_id`). It must never perform title/employer/raw-text search, fuzzy matching, or URL-similarity inference; that would blur the ownership boundary with `/v3/jobs/search`.

## SEEK geography/completeness
Configured scope: whole NSW + ACT + QLD.

Primary SEEK coverage is state-wide partitioning, not keyword searches. A partition above `collection.seek_partition_max_results` (default 450) is incomplete until split. Split hierarchy: state -> SEEK classification -> SEEK subclassification -> work type. Final oversized leaves remain explicitly incomplete.

A parent is complete only when all children are complete and their deduplicated union covers the parent reported count within configured tolerance. `/v3/coverage/seek` is the coverage proof; a non-empty feed is not proof.

## Browser/collection infrastructure
Collection uses one JMM-owned visible long-lived Chromium service/profile (`data/playwright_jmm_seek_user_data`), isolated from Rob's normal Chrome and Job Hunter's SEEK profile. Runs attach/detach over localhost CDP and must not close/relaunch the browser between retries. SQLite WAL is enabled and normal consumers use HTTP, so many agents may consume the API concurrently even while collection runs.

## Admin/settings
Operational knobs belong in settings/admin with helper text and validation, not scattered constants — see `.agents/skills/no-hardcoding/SKILL.md`. Retention, collection timing, partition thresholds, API page sizes, geography enablement and query enablement are admin-manageable where practical.

## Retention
Market lifecycle is rich/current -> archived/compacted -> neutral tombstone. Retention is based on market age, not Rob activity. Never delete identity memory in a way that makes an old source vacancy falsely new.

## Testing/documentation
Every parser, partition, dedupe, cursor, retention, settings or API-contract bug gets a regression test. Update docs with architecture/API changes in the same commit. Source parser failures must be explicit; never silently fall back to JD opening.

## Scheduler / service / backup rule
Use the project-owned in-app scheduler. The background Admin/API service is started with `./scripts/service.sh start`; scheduled and manual collection both invoke the same collection-cycle runner. Supported runtime entrypoints are single-instance: API via `data/api-service.lock`, persistent browser via `data/browser-service.lock`/its fixed user-service unit, and collection via `data/collection.lock`.

Create/verify an online SQLite backup before collection by default. Backup policy is admin-configurable, but do not disable or bypass it casually. The scheduler currently owns only the proven whole-state SEEK stage; do not add LinkedIn whole-registry scheduling until campaign-level continuation is implemented and tested.
