# Operating Guide

## One-off Job Hunter bootstrap (JMM-006)

The bootstrap reads Job Hunter's `job_history` database **read-only** and whitelists only neutral
market/job evidence that exists independently of Job Hunter decisions. It never reads
`last_kept_at` or `last_kept_snapshot`, and does not copy fit scores, recommendations,
hidden/liked/applied/rejected state, user IDs, or other personal activity. Missing neutral fields
stay missing rather than being recovered from a KEEP snapshot or arbitrary history payload fields.
Direct history-record identity/title/company/URL values are eligible. Validated source-backed
`detail_evidence` may also contribute the exact source JD and structured neutral detail facts when its
source/job identity, canonical URL, provenance and fetch timestamp all match. For SEEK this includes
exact `listedAt.dateTimeUtc` as `posted_at`, plus source-exposed location, salary, employment type,
work arrangement, classification/subclassification, expiry/status and apply method. Relative labels
such as `3h ago` are never promoted into the canonical job row. Existing non-empty JMM market evidence
wins over bootstrap values.

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
2. Upsert already-known jobs and refresh operational `job_observation_state.last_seen_at`.
3. Add genuinely new source identities.
4. Preserve query-hit counts.
5. Expose operational `first_seen_at` through the API so consumers can request newly discovered jobs without storing it on the canonical `jobs` row.
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
- do not silently fill missing card fields during bulk mapping by opening JDs or switching to fit inference.

JD enrichment is a distinct source-detail operation, but for JMM-007 it is part of the same full-evidence pass rather than an optional later phase. On resume, existing discovered SEEK jobs are brought up to JD completeness before more coverage is collected; each completed coverage partition triggers JD catch-up; and the pass cannot be COMPLETE while any required SEEK JD remains unfetched. When a job page is deliberately opened for JD enrichment, capture the full source JD and any additional neutral structured source facts exposed by that same detail page. Never infer missing canonical facts from relative labels or prose when the source does not provide them explicitly.

## Browser rule

JMM uses one visible long-lived Chromium service with profile `data/playwright_jmm_seek_user_data`. It must not reuse Rob's normal Chrome or Job Hunter's SEEK profile. Collection runs attach over localhost CDP and detach without closing Chrome, so SEEK/Cloudflare session state survives retries and later runs. Genuine human verification is completed in that same visible JMM window and the run resumes.
For long-running manual/MCP starts, use `scripts/start_collection_service.sh ...`; it ensures the persistent browser service exists and launches the same `scripts.run_collection_cycle` runner in the user systemd manager.

## Source transport ownership

SEEK uses **one JMM-owned persistent Playwright browser context** and reuses a small number of workflow pages. The JMM profile is isolated from both Rob's normal Chrome and Job Hunter's browser profile.

LinkedIn uses the proven Job Hunter transport: python-jobspy HTTP discovery, exact native-ID dedupe, then one direct public-HTML detail fetch per deduplicated new/unfetched vacancy. The obsolete browser LinkedIn implementation has been removed. One shared collection runner owns SEEK followed by LinkedIn; there is no second scheduler or browser process for LinkedIn.
## SEEK daily collection

Normal ongoing SEEK collection uses the latest **1 day** only. Known SEEK source job IDs are recognised from JMM and do not go through full card ingestion or JD fetching again. Only identities without a successful permanent JD-fetch marker are eligible for JD acquisition.

The one-off first full-evidence load is explicitly wider and does not change the normal default:

```bash
uv run python -m scripts.run_collection_cycle --trigger manual --days 3 --max-runtime-minutes 0
```

After that, normal scheduler/Admin/manual runs use the configured freshness setting; the default is 1 day.
