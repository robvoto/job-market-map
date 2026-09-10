# Job Market Map — Current State / New-Chat Handoff

Last verified: 10 September 2026

Read this first when continuing Job Market Map work in a new ChatGPT/Claude session. Then read `.agents/skills/job-market-map/SKILL.md` and the specific owning doc for the task.

## Purpose

Job Market Map is the **neutral/global market collection service** for Rob's job-search agents.

It owns:
- neutral job-board collection;
- stable source identity and `identity_key`;
- rich result-card evidence;
- first/last seen and query/source provenance;
- cross-source duplicate evidence;
- whole-state SEEK coverage and partition state;
- market retention/tombstones;
- per-consumer processing checkpoints;
- versioned local API for consumers.

It does **not** own personal activity/outcomes. JH-305 in Job Hunter is the intended canonical service for `presented_by_agent`, `viewed_by_user`, `applied`, `rejected`, `interview`, `no_response`, etc. Do not recreate those fields/tables in Job Market Map.

## Consumer architecture

Consumers use HTTP `/v3`, not direct SQLite access.

Named feeds:
- `/v3/consumers/job-hunter/feed`
- `/v3/consumers/reset-edge/feed`
- `/v3/consumers/plan-z/feed`

A `consumer_checkpoint` means only that an agent/workflow processed the shared market feed through that point. It does not mean Rob saw or viewed a job.

Reset / Edge and Plan Z apply their own policy after consuming neutral cards. Job Market Map must not embed their fit/reject/rung logic.

## Current geography scope

Primary SEEK market coverage is whole-state:
- NSW
- ACT
- QLD

SEEK keyword queries are supplemental and disabled by default because whole-state coverage is the primary mechanism. LinkedIn still uses the broad discovery-query registry.

## SEEK partition model

Oversized state searches split fail-closed:

```text
state
  -> classification
      -> subclassification
          -> work type
```

Default maximum reported results per leaf: 450.

Parents complete only when all direct children are complete and the deduplicated union of child SEEK job IDs covers the parent's reported count within tolerance.

The state runner is resumable. Completed partitions do not consume the next invocation's partition budget. Already-split parents delegate to unfinished children. A fully persisted leaf can recover to `COMPLETE_RECOVERED` after an interrupted run without re-downloading it.

Default safe execution is one genuinely unfinished partition per invocation. This is an execution-time bound, not a market-coverage limit.

## Live ACT crawl state

Last verified directly from `data/market.db` after the browser disconnect:
- ACT root reported by SEEK: **917** jobs.
- SEEK classifications discovered: **30**.
- Complete classifications: **29 / 30**.
- Deduplicated direct-child union already persisted: **859** jobs.
- Only unfinished classification: **Trades & Services**.
- Trades & Services reported: **89** jobs.
- Trades & Services memberships already persisted before failure: **32**.
- ACT root row currently says `FAILED` and its stored `collected_unique_jobs=822`; that stored total is stale because parent aggregation did not run after the interrupted child. Use the child-union figure above until the crawl resumes/finalizes.
- Total canonical jobs in the live DB at this verification point: **1,192**.

Do **not** call ACT complete yet.

## Current blocker

The latest ACT continuation failed in the browser transport layer, not SEEK parsing:

`Chrome profile 'rob' did not answer browser command 'select_page'`

Canonical broker health at failure:
- one broker listener on `8766`: yes;
- same canonical process owns `8001` and `8766`: yes;
- extension connected: no;
- browser list-pages: failed with 504.

This is not the old multiple-MCP port-collision bug. Do not start another Chrome profile or second canonical MCP server. Restore the existing Rob-Chrome extension/broker connection using the Human MCP recovery tooling, then resume the saved partition state.

## Safe ACT resume

After Human MCP browser health is green:

```bash
cd /home/robvoto/projects/job-market-map
uv run python -m scripts.run_seek_market_map --state ACT --max-partitions 1
```

Do not use `--fresh`; normal continuation must resume persisted work.

Afterward verify:

```text
GET /v3/coverage/seek
```

ACT is proven complete only when the root becomes a `COMPLETE*` state and no ACT partition remains failed/incomplete.

## Validation baseline

Latest code validation before this handoff update:
- **65 tests passed**;
- Ruff passed;
- Python compile had passed at the previous neutral-architecture gate.

Run the full checks again before committing new code.

## Git

Repository:
`/home/robvoto/projects/job-market-map`

Remote:
`git@github.com:robvoto/job-market-map.git`

Branch: `main`.

Use `git log --oneline -5` to verify the current implementation commit rather than relying on a hard-coded hash in this handoff.

## Next work order

1. Restore Human MCP browser-extension connection.
2. Resume ACT one unfinished partition at a time and prove ACT complete.
3. Commit/push the resumability/recovery work if validation remains green.
4. Run/prove NSW whole-state coverage.
5. Run/prove QLD whole-state coverage.
6. Keep Reset / Edge, Plan Z and Job Hunter as API consumers rather than adding parallel board collectors.
