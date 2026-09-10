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

Canonical backlog: `@job_market_map_backlog` (Google Sheet supplied by Rob). Read the live sheet before planning or changing code when backlog state matters. Do not create a competing markdown backlog. If the exact live sheet cannot be accessed, report that failure and stop before backlog-dependent work unless Rob explicitly directs otherwise. Use local `docs/CURRENT_STATE.md` only as operational handoff—not as a replacement backlog.

## JMM access and failure discipline
For JMM repository access, use `HUMAN_MCP_SECURE` first.
For repository edits, use the managed MCP patch/file tools rather than assuming shell helper commands exist.

For the canonical Google Sheet backlog, do **not** use browser automation as the normal path. Use this order:
1. `HUMAN_MCP_SECURE.sheets_read_rows` / `HUMAN_MCP_SECURE.sheets_update_cell`.
2. `HUMAN_MCP_SERVER_NGROK_UNSAFE` as the MCP fallback when Rob has authorised it.
3. If both MCP paths fail, stop and ask Rob before using another connector/path.
4. Browser automation is not a backlog fallback. If a browser path is ever genuinely required and browser control fails, stop and ask Rob.

Canonical backlog spreadsheet ID: `1UX7xEKgHi1gRkPs9PGqDlWLfWSiMgRKkDKPYTXtygOs`.
Spreadsheet file name: `job_market_map_backlog`.
Actual worksheet/tab name: `Backlog`.
Do not confuse the file name with the tab name. For native Sheets calls, always use `sheet_name="Backlog"`; never pass `job_market_map_backlog` as `sheet_name`.

Canonical read example:
`HUMAN_MCP_SECURE.sheets_read_rows(spreadsheet_id="1UX7xEKgHi1gRkPs9PGqDlWLfWSiMgRKkDKPYTXtygOs", sheet_name="Backlog")`.

Native Sheets access uses service account `job-hunter-backlog@angular-log-prj.iam.gserviceaccount.com`. A Google 403 means this account does not have permission to the Sheet; grant/share access to this account before diagnosing MCP or browser failure.

For backlog updates, always read first, derive the current row and header-column coordinates from the returned data, write only the intended cells, then read again and verify the persisted values. Never hard-code JMM row/column numbers from memory.

If a tool path fails, report the exact failure and stop that path. Follow the approved fallback order above; do not silently jump to browser automation, cached content, or another source. Merely locating the backlog file is not proof that its current rows were read.

## Fast continuation / indexing
For every new JMM session, use this order before broad searching:
1. Read `docs/CURRENT_STATE.md`.
2. Read this skill.
3. Read the live `@job_market_map_backlog` rows relevant to the requested JMM IDs.
4. Inspect only the owning code/docs/tests for those IDs.

Keep `docs/CURRENT_STATE.md` current with approved architecture decisions, exact blockers and next work so future sessions do not rediscover settled context.

## Hard ownership boundary
Job Market Map is neutral/global market infrastructure. It owns collection, source identity, evidence, duplicate links, coverage, retention and consumer checkpoints.

It does **not** own personal activity/outcomes. Do not add `shown`, `seen`, `presented_by_agent`, `viewed_by_user`, `applied`, `rejected`, `hidden`, `liked`, `interview`, `no_response` or equivalent user state to jobs, tombstones or a parallel canonical ledger here. Job Hunter JH-305 is the intended owner of that domain.

## JD ownership — approved simple model
JMM owns the one neutral/current raw JD for a job. Assume the JD does not change; do not build JD version history, multiple snapshots, snapshot IDs, hashes for historical reconstruction, or a historical JD ledger.

Target persistence on `jobs`:
- `full_description`
- `jd_fetched_at`
- `jd_source`

Behaviour:
- if JMM already has `full_description`, reuse it;
- otherwise fetch the JD once, store it in JMM, and reuse it thereafter;
- broad market mapping remains card-only and must not open every JD;
- Job Hunter reads the neutral JD from JMM for analysis and must not permanently maintain another duplicate raw JD copy.

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
