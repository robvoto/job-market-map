# Runbook

**Agent lifecycle note:** the start/stop/restart commands in this document are operator references. Agents must follow the Runtime ownership rule in `.agents/skills/job-market-map/SKILL.md`: read-only status/health/log inspection is allowed, but JMM runtime state must not be changed without fresh explicit approval for that exact target and action.

## Start the Admin + in-app scheduler service

```bash
./scripts/service.sh start
```

Then open `http://127.0.0.1:8770/admin`. The service runs in the background; the UI starts/stops SEEK or LinkedIn collection, controls the shared scheduler and shows current runtime health.

```bash
./scripts/service.sh status
./scripts/service.sh stop
```

Before every manual/scheduled collection, a verified SQLite backup is created by default. The API, persistent browser and collector each have a single-instance guard; the collector lock is `data/collection.lock`.


## Check project

```bash
cd /home/robvoto/projects/job-market-map
uv run pytest -q
```

## Initialise / migrate DB

```bash
uv run python collector/db.py
```

## Sync discovery registry

```bash
uv run python -m collector.query_registry
```

## Run LinkedIn market discovery by itself

```bash
uv run python -m scripts.run_linkedin_market --hours-old 5
```

This uses the production geography-first LinkedIn HTTP path for enabled ACT/NSW/QLD locations, takes a verified backup first, acquires the normal singleton collection lock, and does **not** run SEEK. The default scheduler uses this same 5-hour window every 4 hours. Geography network fetches run in parallel; ingestion/dedupe/cursor writes are serialized through one writer. Discovery is card-first; after geography coverage completes, newly discovered LinkedIn jobs are processed through the pending JD queue using direct public HTTP. JMM owns exact 10-position source offsets, retries transient short pages and confirms apparent terminal pages. A geography that reaches LinkedIn's hard 1,000-result ceiling is reported as `INCOMPLETE_CAP`.

`scripts.run_linkedin_chunk` remains a narrow diagnostic for explicitly testing one query/location; it is not the production geography collector.

## Start local API

```bash
./scripts/start-api.sh
```

When this foreground launcher is run from a terminal, it mirrors API/Admin
output and all scheduled or Admin-started collection subprocess output into
that same terminal. It also appends the stream to `logs/api.log`; the durable
collection-specific record remains `logs/collection.log`. The detached
`./scripts/service.sh start` mode has no live terminal and writes to the log
files instead.

## Compact stale data

```bash
uv run python -m collector.retention
```

## SEEK browser prerequisite

JMM keeps one visible long-lived Chromium service using `data/playwright_jmm_seek_user_data` **for SEEK only**. `scripts/start_browser_service.sh` starts it only when it is not already running; later SEEK collection runs attach to the same browser over localhost CDP and detach without closing it. Do not point JMM at Rob's normal Chrome or Job Hunter's profile. If SEEK presents human verification, use Admin's **Open SEEK login browser** control, complete it in the visible JMM browser and let the run continue. LinkedIn is a separate HTTP-only subprocess and never touches this browser.

For a long-running/manual collection launched from MCP or another temporary shell, use the detached collection launcher so the visible Chromium process and collection continue after the calling shell exits:

```bash
scripts/start_collection_service.sh --trigger manual --days 3 --backfill-existing-jds --max-runtime-minutes 0
```

This launcher is for the SEEK runner. LinkedIn uses `scripts.run_linkedin_market`; both entrypoints still share the same singleton collection lock.

## Failure rule

A collection failure must leave prior successful ingests intact and enough cursor/run state to diagnose/resume. Never convert a parser/browser failure into a successful `COMPLETE` run just to keep the campaign moving.


## Run/resume whole-state SEEK map

Use Admin's **Run SEEK now** control for a normal manual run. It invokes the same singleton-locked `scripts.run_collection_cycle` runner used by the scheduler, creates the configured pre-run backup, resumes unfinished coverage, and drains normal JD batches. Do not invoke the old state-only wrapper: it bypassed the collection process manager and has been removed.

The admin default partition chunk is intentionally small so tool-call limits cannot invalidate a whole-state run. Completed partitions are skipped; split parents delegate to unfinished children; persisted leaf memberships survive interruption and may be recovered without re-downloading.

Current enabled scope is NSW + ACT + QLD. Check `/v3/coverage/seek` afterwards; any `INCOMPLETE*` or `FAILED` state means current coverage is not proven complete. `NOT_RUN` means no current coverage workspace exists; Admin renders that as **Waiting for next run** and, when available, shows the archived previous coverage result. For the latest live recovery point, read local-only `docs/CURRENT_STATE.md` if present. That handoff is intentionally not tracked in Git.

### Collection logging

Every collection runner writes timestamped progress to `logs/collection.log` and to stdout. The file rotates at 10 MB and keeps five previous files. Normal scheduled and manual collection acquires JDs for newly discovered SEEK jobs through bounded batches. The `--backfill-existing-jds` option is maintenance-only and is not part of Admin or scheduled collection.

Admin's **View collection log** link opens the bounded tail of this same durable file at `/v3/admin/log`.

For a Windows/WSL terminal that should stay open and show the daily run live, use the foreground launcher:

```bash
scripts/run_collection_terminal.sh --trigger manual
```

The detached launcher writes the same stdout stream to `logs/collection-service.log`; `logs/collection.log` remains the durable source regardless of how the run was launched.
