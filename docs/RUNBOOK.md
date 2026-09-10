# Runbook

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

## Run/resume LinkedIn

```bash
uv run python -m scripts.run_linkedin_chunk "technical implementation" --location "Sydney NSW" --days 7 --max-offsets 8
```

Do not add `--reset` during normal continuation. Reset is for an intentional re-crawl from offset zero.

## Start local API

```bash
./scripts/start-api.sh
```

## Compact stale data

```bash
uv run python -m collector.retention
```

## Browser prerequisite

The canonical Human MCP/browser broker should already be running. Do not launch another MCP server. If browser access fails, use the Human MCP health/recovery tooling rather than a new Chrome profile.

## Failure rule

A collection failure must leave prior successful ingests intact and enough cursor/run state to diagnose/resume. Never convert a parser/browser failure into a successful `COMPLETE` run just to keep the campaign moving.


## Run/resume whole-state SEEK map

Prefer bounded resumable execution:

```bash
uv run python -m scripts.run_seek_market_map --state ACT --max-partitions 1
```

The admin default partition chunk is intentionally small so ChatGPT/Claude tool-call limits cannot invalidate a whole-state run. Completed partitions are skipped; split parents delegate to unfinished children; persisted leaf memberships survive interruption and may be recovered without re-downloading.

Use `--fresh` only when intentionally discarding resume behaviour for a fresh coverage pass.

Current enabled scope is NSW + ACT + QLD. Check `/v3/coverage/seek` afterwards; any `INCOMPLETE*`, `FAILED`, or `NOT_RUN` state means coverage is not proven complete. For the latest live recovery point, read `docs/CURRENT_STATE.md`.
