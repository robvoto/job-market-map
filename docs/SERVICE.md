# Admin Service, Scheduler and Backups

## Why this exists

Job Market Map uses one **in-app scheduler** for two source rhythms: a daily SEEK whole-state run and short rolling LinkedIn-only refreshes.

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

## AWS deployment target

When JMM is deployed with Job Hunter, keep it as a **separate long-running Python service on the same EC2 host**:

```text
Job Hunter  127.0.0.1:8765
JMM         127.0.0.1:8770
JH -> JMM   JOB_HUNTER_MARKET_MAP_BASE_URL=http://127.0.0.1:8770/v3
```

JMM does not need a public listener. Keep `market.db`, backups and the SEEK Chromium profile on persistent storage. Do not move JMM to Lambda: the scheduler, SQLite state and long-lived SEEK browser/session are intentionally process-persistent. LinkedIn remains HTTP-only and does not use Chromium.

## Admin collection controls

The Admin page provides:
- **Run SEEK now** — starts the same safe SEEK whole-state cycle used by the daily scheduler;
- **Run LinkedIn now** — starts the current rolling LinkedIn geography slot without SEEK;
- **Stop current collection** — requests a graceful stop after the current partition unit;
- **Pause ALL schedules** / **Resume ALL schedules** — emergency master switch for automatic collection;
- **Pause SEEK schedule** / **Resume SEEK schedule** — controls only the daily SEEK run;
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
- persistent browser runner: `data/browser-service.lock` plus its fixed systemd user unit;
- collector: `data/collection.lock`.

A manual Run Now and a scheduled run cannot overlap. Starting the supported API/browser launchers again reuses or refuses the existing instance rather than creating another JMM runtime process.

Do not remove these locks in favour of a UI-only `running=true` flag.

## Scheduling semantics

SEEK uses the configured daily local time (default **02:00**). LinkedIn defaults to a **5-hour rolling window every 4 hours**. The LinkedIn window must remain larger than its cadence so adjacent runs overlap. `scheduler.enabled` is only the emergency master switch; `scheduler.seek_enabled` and `scheduler.linkedin_enabled` control the two automatic source rhythms independently. Both schedules share `data/collection.lock`, so they never mutate JMM concurrently.

When LinkedIn and SEEK are both due, LinkedIn is started first. The daily SEEK slot remains due and starts after LinkedIn releases the singleton lock.

The scheduler does not blindly restart collection every night:
- if the current SEEK NSW/ACT/QLD coverage cycle is incomplete, the run **resumes it**;
- if there is no current coverage workspace, the run starts a **fresh coverage cycle**;
- if all enabled states are complete, the next run first snapshots coverage history and starts a **fresh coverage cycle**;
- a max-runtime stop preserves partial progress for the next run.

A successful manual run using the normal freshness horizon may satisfy the scheduled slot **only when the manual run overlaps that slot's configured run window**. This avoids duplicate collection inside the same operating window without creating a coverage gap. A manual run hours before the next slot does not cancel that future slot.

Fresh extra runs within the same 24-hour freshness period use exact SEEK `listingDate` timestamps as a conservative incremental cutoff with `collection.seek_incremental_overlap_minutes` (default 120 minutes). Only a prior completed **fresh** cycle is trusted as a watermark. Resumed/stopped/partial cycles never advance it. If the previous completed fresh run is old enough that the cutoff falls outside the current 1-day horizon, the run is a normal full 1-day reconciliation. Missing or non-monotonic exact timestamps also force full paging.

A daily refresh therefore cannot inherit yesterday's partition memberships and falsely claim current completeness.

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

SEEK and LinkedIn use separate subprocess entrypoints under the same scheduler and singleton lock. SEEK uses the persistent JMM Chromium service. LinkedIn is cards-only HTTP discovery, never uses Chromium, and fetches enabled geographies concurrently while all SQLite ingest/dedupe/cursor writes remain serialized in one coordinator thread. LinkedIn owns independent geography cursors with exact 10-position source offsets and reports the hard 1,000-result ceiling as `INCOMPLETE_CAP` rather than falsely complete.
