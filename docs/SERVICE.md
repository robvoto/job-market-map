# Admin Service, Scheduler and Backups

**Agent lifecycle note:** the start/stop/restart commands in this document are operator references. Agents must follow the Runtime ownership rule in `.agents/skills/job-market-map/SKILL.md`: read-only status/health/log inspection is allowed, but JMM runtime state must not be changed without fresh explicit approval for that exact target and action.

## Why this exists

Job Market Map uses one **in-app scheduler** for two source rhythms: SEEK whole-state runs every 12 hours and short rolling LinkedIn-only refreshes.

The distinction is important:
- the API/Admin service stays running;
- the scheduler lives inside that service;
- each collection runs in a separate subprocess;
- Admin can start/stop collection and pause/resume the schedule without killing itself.

## Start once in the background

```bash
cd /home/robvoto/projects/job-market-map
./scripts/service.sh start
```

You can close the terminal afterward. As long as Windows/WSL remains running, the Admin service remains available at:

```text
http://127.0.0.1:8770/admin
```

Check or stop the **Admin service itself** from a terminal:

```bash
./scripts/service.sh status
./scripts/service.sh stop
```

The UI intentionally does not contain a button that kills its own web service, because after doing so the UI could not restart itself.

This does not create operating-system startup persistence. After Windows/WSL restarts, run `./scripts/service.sh start` again.

## AWS boundary

JMM is currently a local-only service. Job Hunter's AWS deployment intentionally does not run JMM and must not be configured with a deployed or same-host JMM URL. Local Job Hunter uses:

```text
JOB_HUNTER_MARKET_MAP_BASE_URL=http://127.0.0.1:8770/v3
```

The scheduler, SQLite state and long-lived SEEK browser/session are process-persistent, so JMM is not a Lambda workload. LinkedIn remains HTTP-only and does not use Chromium. The `scripts/ec2/` files are deployment templates for a separately approved future AWS deployment; they are not part of the current local runtime.

## Admin collection controls

The Admin page provides:
- **Run SEEK now** — starts the same safe SEEK whole-state cycle used by the daily scheduler;
- **Run LinkedIn now** — starts the current rolling LinkedIn geography slot without SEEK;
- **Stop current collection** — requests a graceful stop after the current partition unit;
- **Pause ALL schedules** / **Resume ALL schedules** — emergency master switch for automatic collection;
- **Pause SEEK schedule** / **Resume SEEK schedule** — controls only the SEEK schedule;
- **Pause LinkedIn schedule** / **Resume LinkedIn schedule** — controls only rolling LinkedIn refreshes;
- overnight local time control (default **02:00**);
- current collector PID/state;
- persistent JMM browser running/unavailable state (reachability only; not proof of SEEK sign-in);
- **Open SEEK login browser** control for the same persistent JMM Chrome/profile;
- scheduler active/enabled/next-run state;
- latest collection outcome;
- **Backup DB now**;
- latest backup status;
- direct link to the durable collection log.

## Single-instance runtime services

Supported JMM runtime entrypoints are single-instance:
- API/Admin service: `data/api-service.lock`;
- persistent browser runner: `data/browser-service.lock` and the direct launcher;
- collector: `data/collection.lock`.

A manual Run Now and a scheduled run cannot overlap. Starting the supported API/browser launchers again reuses or refuses the existing instance rather than creating another JMM runtime process.

Do not remove these locks in favour of a UI-only `running=true` flag.

## Scheduling semantics

SEEK uses a configurable interval, default **12 hours**, anchored at the configured local time (default **02:00**), producing default slots at **02:00 and 14:00**. LinkedIn defaults to a **5-hour rolling window every 4 hours**. The LinkedIn window must remain larger than its cadence so adjacent runs overlap. `scheduler.enabled` is only the emergency master switch; `scheduler.seek_enabled` and `scheduler.linkedin_enabled` control the two automatic source rhythms independently. Both schedules share `data/collection.lock`, so they never mutate JMM concurrently.

When LinkedIn and SEEK are both due, LinkedIn is started first. The SEEK slot remains due and starts after LinkedIn releases the singleton lock.

The scheduler does not blindly restart collection on every slot:
- if the current SEEK NSW/ACT/QLD coverage cycle is incomplete, the run **resumes it**;
- if there is no current coverage workspace, the run starts a **fresh coverage cycle**;
- if all enabled states are complete, the next run first snapshots coverage history and starts a **fresh coverage cycle**;
- a max-runtime stop preserves partial progress for the next run.

A successful manual run using the normal freshness horizon may satisfy the scheduled slot **only when the manual run overlaps that slot's configured run window**. This avoids duplicate collection inside the same operating window without creating a coverage gap. A manual run hours before the next slot does not cancel that future slot.

Fresh extra runs within the same 24-hour freshness period use exact SEEK `listingDate` timestamps as a conservative incremental cutoff with `collection.seek_incremental_overlap_minutes` (default 120 minutes). Only a prior completed **fresh** cycle is trusted as a watermark. Resumed/stopped/partial cycles never advance it. If the previous completed fresh run is old enough that the cutoff falls outside the current 1-day horizon, the run is a normal full 1-day reconciliation. Missing or non-monotonic exact timestamps also force full paging.

A fresh scheduled cycle cannot inherit stale partition memberships and falsely claim current completeness.

When SEEK failure hardening (JMM-014) exhausts a normal scheduled cycle without full completion (`BLOCKED_INCOMPLETE`), the run is archived and reset the same way rather than left resuming an unresolvable workspace; a scheduled SEEK failure gets at most one same-window retry after `scheduler.seek_failure_retry_minutes` (default 15 minutes), and a second same-day scheduled failure does not loop again.

Rollover (`snapshot_and_reset_coverage`, JMM-015) archives each geography's current root summary into `seek_coverage_history`, then deletes every child/grandchild partition for that geography and resets only the reused state-root row (matched by URL) to PENDING. Earlier rollover code reset root fields without deleting children, so a later same-day root completion — for example a narrower incremental pass reporting fewer results than the split threshold — could mark the root COMPLETE while a prior cycle's orphaned classification/subclassification/work-type rows still counted as incomplete. Because children are now deleted rather than left PENDING, COMPLETE always implies `incomplete_partitions=0` for that geography. Canonical jobs/JDs are never touched by rollover.

Operating cadence is source-specific. LinkedIn runs every 4 hours with a 5-hour lookback. SEEK runs every 12 hours by default with a 1-day horizon. Both sources queue newly discovered jobs for JD enrichment during their normal runs; SEEK drains bounded JD batches between coverage partitions and finishes with a final queue drain. If both sources are due together, LinkedIn runs first and SEEK follows after the singleton lock is released. On-demand JD retrieval remains a fallback, not the normal collection path.

The scheduler only starts when the API was launched in service mode (`start-api.sh` / `service.sh`). Direct test imports do not create background collection.

## SQLite backup policy

`backup.before_collection_enabled=true` by default.

Before each manual or scheduled collection process mutates market state:
1. SQLite's online backup API creates a transactionally consistent copy of `data/market.db`;
2. the backup runs `PRAGMA integrity_check`;
3. collection proceeds only after the backup verifies `ok`;
4. the newest configured number of backups are retained (`backup.keep_count`, default 14).

Backups live under:

`/home/robvoto/projects/job-market-map/backups/`

Backups are local runtime data and are ignored by Git.

If backup creation/integrity fails, the market run is recorded as `FAILED` and collection does not start.

## Collection log

The durable collection log is `logs/collection.log`. Admin links to `GET /v3/admin/log`, which returns a bounded tail of that same file; there is no second UI-specific log stream.

## Current scheduled source scope

SEEK and LinkedIn use separate subprocess entrypoints under the same scheduler and singleton lock. SEEK uses the persistent JMM Chromium service. LinkedIn uses HTTP card discovery and direct public-page JD enrichment, never Chromium, and fetches enabled geographies concurrently while all SQLite ingest/dedupe/cursor writes remain serialized in one coordinator thread. LinkedIn owns independent geography cursors with exact 10-position source offsets and reports the hard 1,000-result ceiling as `INCOMPLETE_CAP` rather than falsely complete.

## Pre-live local-to-AWS database promotion (JMM-012)

Until JMM is explicitly promoted, the **local JMM database is authoritative** and AWS JMM stays disabled. Installing the EC2 units does not enable them by default.

Use the promotion workflow from the local JMM repo:

```bash
export JMM_AWS_INSTANCE_ID=i-xxxxxxxxxxxxxxxxx
export JMM_AWS_TRANSFER_BUCKET=<approved-private-transfer-bucket>
uv run python -m scripts.promote_to_aws stage
```

`stage` refuses to run while local collection or the local scheduler is active and requires a clean `main` exactly synced with `origin/main`. It creates a transactionally consistent SQLite backup, verifies `PRAGMA integrity_check`, records source/job/JD/checkpoint counts and SHA-256 in an ignored local manifest, uploads the snapshot as an AES-256-encrypted temporary S3 object, and restores/verifies it on the persistent AWS JMM EBS volume. It starts the AWS API only long enough to prove `/v3` health and a real Job Hunter client read, then stops it again. Starting the API also brings up the persistent SEEK browser on port 9223 (`job-market-map.service` wants `job-market-map-browser.service`), which proves the browser starts cleanly on AWS; `stage` stops both services explicitly afterward since systemd's `Wants=` only applies on start, not on stop. The temporary S3 object is deleted. A failed stage restores the previous AWS DB, or removes the failed first-stage DB if no previous DB existed. **Stage does not enable AWS JMM or configure Job Hunter to depend on it.**

When a staged manifest has been reviewed and Rob explicitly approves production cutover, go-live is a separate command:

```bash
uv run python -m scripts.promote_to_aws \
  --instance-id "$JMM_AWS_INSTANCE_ID" \
  go-live --manifest exports/aws-promotion/<promotion-id>.json \
  --confirm GO-LIVE-JMM
```

Go-live re-verifies the staged DB and code commit before enabling the JMM API/browser/scheduler and then configuring Job Hunter to use `http://127.0.0.1:8770/v3`. Local automatic collection must remain off. The pre-promotion AWS DB backup and the local source DB are retained for rollback.
