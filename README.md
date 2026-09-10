# Job Market Map

Neutral local job-market infrastructure shared by Rob's job-search agents.

## Purpose

Job Hunter, Reset / Edge, Plan Z and future agents should not independently scrape the same market, rediscover the same vacancies, and maintain incompatible history. Job Market Map collects **result-card evidence once**, stores it locally, and exposes a stable HTTP feed.

It answers:

> What jobs did the sources show, where, when, and under which searches?

It does **not** answer:

> Is this job good for Rob?

Fit and application judgement belong to consumers.

## Core rules

1. **Cards first; zero JD opening in neutral mapping.**
2. **Capture rich source evidence** — title, employer, location, salary/work type when visible, classification, snippets, tags and raw card text.
3. **No fit filtering during collection.** Strange edge roles, BA roles, trainees and bad matches can all exist in the neutral map.
4. **Same-source identity upserts; rich duplicate evidence links cross-posts without destructive merging.**
5. **SQLite is canonical local storage; `/v3` HTTP is the supported consumer contract.**
6. **Admin settings/query controls handle normal tuning without Python edits.**
7. **Destructive retention is opt-in; canonical job/card evidence is preserved by default.**
8. **Personal activity is not owned here; Job Hunter/JH-305 is the canonical per-user activity service.**

## Repository

WSL: `/home/robvoto/projects/job-market-map`

Windows: `\\wsl$\Ubuntu\home\robvoto\projects\job-market-map`

Runtime database: `data/market.db` (local runtime state; not committed to Git).

## Key paths

```text
api/                  versioned local API + Rob admin page
collector/            DB, ingestion, dedupe, retention, settings, campaign mechanics
config/               typed admin setting catalog/help
queries/              neutral seed query registry
sources/              source-specific card collectors
examples/             consumer examples
scripts/              API/collection/run commands
docs/                 architecture, runbook, API, admin, source and lifecycle docs
tests/                isolated regression/contract tests
```

## Start API/Admin

```bash
cd /home/robvoto/projects/job-market-map
./scripts/start-api.sh
```

Then use:
- API docs: `http://127.0.0.1:8770/docs` by default;
- Admin: `http://127.0.0.1:8770/admin` by default.

The port itself is an admin setting and takes effect on the next API start.

## Current implementation

- SQLite canonical store: implemented
- versioned `/v3` consumer API: implemented
- Rob admin settings/query controls: implemented
- status history + idempotent writes: implemented
- duplicate fingerprints/evidence links: implemented
- one-off JMM-006 bootstrap from trustworthy existing Job Hunter neutral job/JD evidence: implemented
- opt-in retention controls with preserve-evidence defaults: implemented
- 109 neutral query specs expanded across NSW/ACT/QLD (684 seeded source-geography searches)
- SEEK card-only parser + recursive whole-state NSW/ACT/QLD partition coverage: implemented
- LinkedIn card-only parser + resumable arbitrary-offset traversal: implemented
- APSJobs neutral collector: pending
- individual JD collection: deliberately outside the neutral mapping stage

Measured 10 Sep 2026: one SEEK `technical implementation` search exposed **220 cards over seven populated pages**, with **0 individual JD opens**. LinkedIn card collection is resumable because large virtualised result sets exceed one bounded execution. See `docs/BENCHMARKS.md`.

## Read first

Agents: `.agents/skills/job-market-map/SKILL.md`

Consumers: `docs/CONSUMER_CONTRACT.md`

Rob/admin: `docs/ADMIN.md`


## Geography and SEEK completeness

Current whole-state scope is **NSW + ACT + QLD**. SEEK coverage is independent of role-keyword discovery and recursively partitions oversized result sets: state -> classification -> subclassification -> work type. Default maximum is 450 reported results per leaf. Parent completeness is based on the deduplicated union of child SEEK job IDs and fails closed when coverage is short. See `docs/SEEK_COVERAGE.md`.

## Running as a local service

Start once in the background:

```bash
./scripts/service.sh start
```

Admin: `http://127.0.0.1:8770/admin`

The in-app scheduler defaults to 02:00 local time and is controlled from Admin. It currently schedules the proven whole-state SEEK stage only. Manual and scheduled collection share one cross-process lock, so they cannot overlap. A verified SQLite online backup is created before each collection by default. See `docs/SERVICE.md`.
