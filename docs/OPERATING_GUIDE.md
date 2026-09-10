# Operating Guide

## One-off Job Hunter bootstrap (JMM-006)

The bootstrap reads Job Hunter's `job_history` database **read-only** and whitelists only neutral
market/job evidence that exists independently of Job Hunter decisions. It never reads
`last_kept_at` or `last_kept_snapshot`, and does not copy fit scores, recommendations,
hidden/liked/applied/rejected state, user IDs, or other personal activity. Missing neutral fields
stay missing rather than being recovered from a KEEP snapshot or arbitrary history payload fields.
Direct history-record identity/title/company/URL/freshness values are eligible; JD text is eligible only from
validated `detail_evidence` with matching source/job identity, canonical URL, source provenance and
fetch timestamp. Existing non-empty JMM market evidence wins over bootstrap values.

First run the mandatory dry-run:

```bash
uv run python scripts/bootstrap_from_job_hunter.py \
  --job-hunter-db /home/robvoto/projects/job-hunter-agent/data/app.db
```

Review `exports/jmm006_job_hunter_bootstrap_dry_run.json`. The report includes records checked,
valid/importable jobs, skipped/invalid records, existing/new JMM jobs, jobs with/without JDs,
identity/JD conflicts and unmapped records.

Only then apply it:

```bash
uv run python scripts/bootstrap_from_job_hunter.py \
  --job-hunter-db /home/robvoto/projects/job-hunter-agent/data/app.db \
  --apply
```

Apply creates a verified JMM SQLite backup first, imports idempotently using JMM identity rules,
and never changes the Job Hunter database. Normal JMM collection remains a separate operation.

## Goal

Build the broadest useful **neutral** local map of jobs discoverable through the sources and query combinations we choose, then incrementally add new cards each day.

## Initial full-map run

1. Load the neutral query registry.
2. For each source/query/location combination, collect result cards only.
3. Capture as much source-visible card information as reliably available.
4. Upsert same-source job identity.
5. Append the raw observation to `card_captures`.
6. Record the query hit.
7. Do **not** open individual JDs.
8. Do **not** decide whether the role fits Rob.
9. Continue until the source/query result space is exhausted or the source itself imposes a clear practical boundary.
10. Produce coverage/statistics, not a hand-picked shortlist.

There is no `max_discovery_cards_per_pass` product rule for full mapping. Technical safety limits may exist for rate limiting, retries and runaway loops, but they must not silently truncate the intended market map.

## Daily delta run

1. Re-run the active query registry.
2. Upsert already-known jobs and refresh `last_seen_at`.
3. Add genuinely new source identities.
4. Preserve query-hit counts.
5. Expose `first_seen_at` so consumers can request only newly discovered jobs.
6. Run retention maintenance after collection.

## Consumer workflow

Consumers query the map first. Examples:
- Reset / Edge: new/unreviewed cards, then its own history/title gate, then JD review;
- Job Hunter: professional-role cards, then Job Hunter policy;
- Plan Z: Plan Z-relevant cards, then Plan Z policy.

The mapper itself does none of those policy decisions.

## Source parser rule

Prefer robust card-container extraction over clicking individual result items. A card parser should return a structured observation plus `raw_card_text`.

When a source DOM changes:
- fail visibly;
- retain diagnostic evidence;
- fix the source parser;
- add a regression fixture/test;
- do not silently fill missing fields by opening JDs or switching to fit inference.

## Browser rule

For signed-in sources, use HUMAN_MCP_SECURE and Rob's existing signed-in Chrome. One mapping run owns one browser tab/session. Do not start another Human MCP server or another Chrome profile merely because other agents are active.

## Campaign browser ownership

A full campaign opens **one new workflow-owned tab inside the already-running Rob Chrome profile** and reuses that page across source/query steps. It must not create one Chrome profile, browser process, or tab per job/query. Standalone diagnostics may open one dedicated tab for that one diagnostic.
