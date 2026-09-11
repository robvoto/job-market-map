# Documentation Index

Use this as the canonical documentation routing index for the repo. It should stay short and point to the current source-of-truth docs rather than becoming a manual itself.

## Source-of-truth docs

- [README.md](../README.md) — project overview and quick start.
- [ARCHITECTURE.md](ARCHITECTURE.md) — system design, purpose boundary, and module ownership.
- [API.md](API.md) — local `/v3` agent API surface.
- [CONSUMER_CONTRACT.md](CONSUMER_CONTRACT.md) — the neutral consumer contract for Job Hunter, Reset/Edge and Plan Z.
- [DATA_MODEL.md](DATA_MODEL.md) — canonical schema and table ownership.
- [OPERATING_GUIDE.md](OPERATING_GUIDE.md) — day-to-day operation, including the one-off Job Hunter bootstrap.
- [ADMIN.md](ADMIN.md) — admin UI and settings.
- [SERVICE.md](SERVICE.md) — background Admin/API service, scheduler and backups.
- [RETENTION.md](RETENTION.md) — market lifecycle and retention policy.
- [SOURCES.md](SOURCES.md) — supported job-board source behaviour (SEEK, LinkedIn, APSJobs).
- [SEEK_COVERAGE.md](SEEK_COVERAGE.md) — SEEK whole-state partition/coverage model.
- [QUERY_STRATEGY.md](QUERY_STRATEGY.md) — neutral discovery query strategy.
- [RUNBOOK.md](RUNBOOK.md) — operational start/recovery steps.
- [BENCHMARKS.md](BENCHMARKS.md) — measured source collection performance.
- [DECISIONS.md](DECISIONS.md) — architecture decision record (ADR) log.

## Session handoff

- [CURRENT_STATE.md](CURRENT_STATE.md) — local-only new-session handoff: latest verified crawl state, blocker, and exact resume point. Never committed to Git.

## Maintenance

Keep this index in sync when a doc is added, renamed, or removed. New agent-instruction rules belong in `.agents/skills/`, not here — see `.agents/skills/instruction-maintenance/SKILL.md`.
