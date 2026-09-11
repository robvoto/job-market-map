# Runbook

## Start the Admin + in-app scheduler service

```bash
./scripts/service.sh start
```

Then open `http://127.0.0.1:8770/admin`. The service runs in the background; the UI starts/stops collection, controls the overnight scheduler and shows current runtime health.

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

## Run one exhaustive SEEK query

```bash
uv run python -m scripts.run_seek_query "technical implementation" --location "Sydney NSW" --days 7
```

The SEEK query should finish only on a verified terminal/no-new-result state. A safety-page limit is a failure, not completion.

## LinkedIn prototype — do not use as the production path

```bash
uv run python -m scripts.run_linkedin_chunk "technical implementation" --location "Sydney NSW" --days 7 --max-offsets 8
```

This command exercises the early browser/snapshot prototype only. It is not the approved production design and must not be wired into the scheduler. JMM-011 replaces it with the proven Job Hunter architecture: python-jobspy HTTP discovery, exact LinkedIn-ID dedupe, then one direct public-HTML vacancy fetch reused for JD/apply/repost/closed/applicant-count evidence. After that cutover the obsolete browser LinkedIn path must be removed rather than kept as a fallback.

## Start local API

```bash
./scripts/start-api.sh
```

## Compact stale data

```bash
uv run python -m collector.retention
```

## SEEK browser prerequisite

JMM keeps one visible long-lived Chromium service using `data/playwright_jmm_seek_user_data` **for SEEK**. `scripts/start_browser_service.sh` starts it only when it is not already running; later SEEK collection runs attach to the same browser over localhost CDP and detach without closing it. Do not point JMM at Rob's normal Chrome or Job Hunter's profile. If SEEK presents human verification, use Admin's **Open SEEK login browser** control, complete it in the visible JMM browser and let the run continue. LinkedIn must not use this browser.

For a long-running/manual collection launched from MCP or another temporary shell, start the existing collection runner inside JMM's user-service scope so the visible Chromium process survives after the calling shell exits:

```bash
scripts/start_collection_service.sh --trigger manual --days 3 --backfill-existing-jds --max-runtime-minutes 0
```

This is only a durable launcher; `scripts.run_collection_cycle` remains the single collection runner.

## Failure rule

A collection failure must leave prior successful ingests intact and enough cursor/run state to diagnose/resume. Never convert a parser/browser failure into a successful `COMPLETE` run just to keep the campaign moving.


## Run/resume whole-state SEEK map

Prefer bounded resumable execution:

```bash
uv run python -m scripts.run_seek_market_map --state ACT --max-partitions 1
```

The admin default partition chunk is intentionally small so ChatGPT/Claude tool-call limits cannot invalidate a whole-state run. Completed partitions are skipped; split parents delegate to unfinished children; persisted leaf memberships survive interruption and may be recovered without re-downloading.

Use `--fresh` only when intentionally discarding resume behaviour for a fresh coverage pass.

Current enabled scope is NSW + ACT + QLD. Check `/v3/coverage/seek` afterwards; any `INCOMPLETE*` or `FAILED` state means current coverage is not proven complete. `NOT_RUN` means no current coverage workspace exists; Admin renders that as **Waiting for next run** and, when available, shows the archived previous coverage result. For the latest live recovery point, read local-only `docs/CURRENT_STATE.md` if present. That handoff is intentionally not tracked in Git.

### Collection logging

Every collection runner writes timestamped progress to `logs/collection.log` and to stdout. The file rotates at 10 MB and keeps five previous files. Important events include run configuration/start/end, backup path, browser page setup, JD sweep start/end, JD progress every 25 successful stores, failed JD attempts, coverage progress, genuine human-verification waits, browser loss/recovery, and full exception tracebacks.

Admin's **View collection log** link opens the bounded tail of this same durable file at `/v3/admin/log`.

For a Windows/WSL terminal that should stay open and show the daily run live, use the foreground launcher:

```bash
scripts/run_collection_terminal.sh --trigger manual
```

The systemd launcher still writes the same stdout stream to the journal, while `logs/collection.log` remains the durable source regardless of how the run was launched.
