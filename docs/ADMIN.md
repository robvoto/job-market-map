# Admin Controls

## UI

Start the API and open:

```text
http://127.0.0.1:<configured-port>/admin
```

The page exposes runtime settings, service health, collection controls, coverage and advanced discovery-query controls.


## Service / scheduler controls

The top Admin panel controls the running collector without killing the Admin API itself:
- Run collection now;
- graceful Stop current collection;
- Pause / Resume overnight schedule;
- set overnight time (default 02:00 local);
- Backup DB now;
- collector/JMM-browser/scheduler status dots;
- latest run start/finish/duration and result;
- next run using human-readable local date/time;
- right-side current-market, accepted 3-day-bootstrap and latest-run statistics;
- **Open SEEK login browser**, which focuses or opens SEEK in the persistent JMM Chrome;
- direct **View collection log** link;
- latest backup status.

**JMM browser: Running** means only that the dedicated persistent Chrome process is reachable. It does not claim SEEK is signed in. Use **Open SEEK login browser** to bring that same persistent SEEK session forward and sign in when needed.

Start the background Admin service with `./scripts/service.sh start`; it must remain running for the in-app overnight scheduler to fire. The status panel refreshes every 10 seconds.

**Backup DB now** creates a transactionally consistent copy of `data/market.db` under `/home/robvoto/projects/job-market-map/backups/`, verifies it with `PRAGMA integrity_check`, and keeps the configured number (`backup.keep_count`, default 14). See `docs/SERVICE.md`.

## SEEK coverage display

`NOT_RUN` is the raw API state when there is no current coverage workspace. In Admin this is shown as **Waiting for next run**, not as a failure. If archived coverage exists, Admin also shows the previous run's reported/covered totals and incomplete-partition count.

After the accepted 3-day bootstrap, the temporary coverage workspace was intentionally cleared so the first normal 1-day run starts fresh. Canonical jobs and JDs are independent of that workspace.

## What is configurable without code

### Retention
Destructive retention is **OFF by default**. Admin exposes an explicit switch for each destructive phase plus its age threshold:

- `retention.prune_raw_captures_enabled` + `retention.raw_capture_days`;
- `retention.archive_jobs_enabled` + `retention.archive_after_days`;
- `retention.remove_archived_jobs_enabled` + `retention.remove_archived_after_days`.

Default behaviour is to preserve canonical job/card evidence indefinitely. The numeric defaults (`30`, `30`, `120`) are future cleanup thresholds only; they do nothing until the matching switch is turned on. This is deliberate because historical card evidence may later support audit, scam/phishing investigation, repost analysis and learning from application outcomes.

Personal applied/rejected/presented state is not a retention exception here because it is not owned by Job Market Map; Job Hunter/JH-305 owns personal activity.

### Collection
- default freshness horizon;
- SEEK page settling, parser wait and safety-page guard;
- legacy LinkedIn prototype settings remain visible until JMM-011 replaces the browser-based prototype with the proven JobSpy/HTTP design;
- campaign source/query chunk size (execution bound only, never a market-coverage limit).

### Duplicate detection
- near-match enable/disable;
- near-match confidence threshold.

### API
- default/max API page sizes;
- local API port.

### Optional source keyword queries (advanced)
These are extra source/query/location searches for LinkedIn, APSJobs or targeted experiments. Normal SEEK daily coverage uses whole-state partitioning and does not depend on these keyword queries. Queries are operational DB data; Admin can enable/disable them, and registry sync does not silently re-enable a query Rob disabled.

## Safety semantics

A safety limit is not a market-coverage limit. If SEEK reaches its safety page limit before an explicit terminal state, the run is failed/incomplete.

Disabling a query stops future collection but does not delete jobs or query history already gathered through it.


## Geography scope

Whole-state collection scope is stored as admin data. Current enabled geographies are NSW, ACT and QLD. The Admin page can enable/disable a configured geography without changing Python.

`GET /v3/admin/geographies` and `PATCH /v3/admin/geographies/{code}` provide the same control for agents/admin tooling.

## SEEK completeness controls

- `collection.seek_partition_max_results` — default 450; oversized partitions must split and cannot be reported complete.
- `collection.seek_completion_count_tolerance` — default 5; small live-market count drift tolerated when reconciling a leaf or parent union.

These settings affect completeness semantics, so helper text is displayed in Admin. See `docs/SEEK_COVERAGE.md` before changing them.


`collection.seek_keyword_queries_enabled` is off by default. Whole-state partition coverage is the primary SEEK feed; turning this on adds the role-keyword SEEK registry as supplemental collection and substantially increases request volume.
