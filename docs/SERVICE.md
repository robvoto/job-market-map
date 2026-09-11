# Admin Service, Scheduler and Backups

## Why this exists

Job Market Map uses an **in-app scheduler**, following the same basic pattern as Job Hunter: a small long-running service checks whether the daily local-time window is due and starts a bounded collection subprocess.

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

## Admin collection controls

The Admin page provides:
- **Run collection now** — starts the same safe SEEK whole-state cycle used by the scheduler;
- **Stop current collection** — requests a graceful stop after the current partition unit;
- **Pause overnight schedule** / **Resume overnight schedule**;
- overnight local time control (default **02:00**);
- current collector PID/state;
- persistent browser ready/broken state;
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

## Overnight scheduling semantics

Default schedule: **02:00 local host time**. The time, start window, polling interval and maximum collection runtime are Admin settings.

The scheduler does not blindly restart collection every night:
- if the current SEEK NSW/ACT/QLD coverage cycle is incomplete, the run **resumes it**;
- if there is no current coverage workspace, the run starts a **fresh coverage cycle**;
- if all enabled states are complete, the next run first snapshots coverage history and starts a **fresh coverage cycle**;
- a max-runtime stop preserves partial progress for the next run.

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

The safe scheduled stage currently runs **whole-state SEEK only** for enabled NSW/ACT/QLD geographies.

LinkedIn individual queries have resumable offsets, but the entire LinkedIn query registry does not yet have a proven campaign-level cursor. Do not advertise or wire it as an overnight whole-registry stage until that is fixed and tested.
