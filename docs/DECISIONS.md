# Architecture Decisions

## ADR-001 — Neutral shared market map
**Decision:** Collection is separate from Job Hunter, Reset / Edge and Plan Z policy.

**Reason:** Multiple agents share one factual market without contaminating each other's decision rules.

## ADR-002 — SQLite canonical storage
**Decision:** Use SQLite in WSL as the canonical local store.

**Reason:** Structured querying, WAL concurrency, evidence retention, lifecycle and dedupe are more reliable than flat text state. Raw text is still retained as evidence inside SQLite while useful.

## ADR-003 — Card-only mapping
**Decision:** Full market mapping does not open individual JDs.

**Reason:** Result cards can be collected at far higher throughput. JD opening belongs to a consumer after its own history/title screen.

## ADR-004 — Evidence-based, non-destructive duplicate linking
**Decision:** Stable same-source identity upserts deterministically. Rich cross-source/same-title evidence creates duplicate links rather than destructive merges.

**Reason:** Capturing title, employer, location, employment/workplace type, salary, classification and teaser makes duplicates highly detectable, while keeping both rows prevents false merges from hiding real vacancies.

## ADR-005 — Evidence-preserving retention by default
**Decision:** Destructive retention is opt-in. By default Job Market Map preserves canonical job evidence and raw captures indefinitely. Separate Admin switches can later enable raw-capture pruning, archive/compaction, and archived-row removal; their age thresholds are inert while the switches are off.

**Reason:** Historical card evidence is cheap to keep and may later support audit, scam/phishing investigation, repost analysis, dedupe improvements, and learning from application outcomes. Evidence can be pruned later if measured storage/performance warrants it, but discarded source evidence cannot be reconstructed. If detailed-row removal is deliberately enabled, a tiny neutral tombstone still preserves source identity so rediscovery is not falsely treated as new.

## ADR-006 — Versioned API is the consumer boundary
**Decision:** Job Hunter and other agents consume `/v3`; they do not write SQLite directly.

**Reason:** A stable API can validate writes, provide cursors/idempotency, preserve lifecycle invariants and evolve storage without breaking every agent.

## ADR-007 — Runtime settings are admin data
**Decision:** Normal operational tuning is controlled through typed settings and Admin/API, with helper text and bounds.

**Reason:** Rob should be able to tune retention, source timing, duplicate confidence, page sizes and query activation without editing Python.

## ADR-008 — Registry JSON seeds; SQLite operates
**Decision:** The JSON query registry bootstraps known search coverage, while SQLite is the live operational state.

**Reason:** Admin-added/disabled queries must take effect immediately and must not be undone by a seed sync.


## ADR-009 — Personal activity belongs to Job Hunter
**Decision:** Job Market Map owns market facts and consumer checkpoints only. Personal activity/outcomes are canonical in Job Hunter under JH-305. The abandoned temporary Job Market Map activity service was removed and the consumer API advanced to `/v3`.

`presented_by_agent` is agent-specific personal history; `viewed_by_user`, `applied`, `rejected`, `interview`, `no_response` and similar outcomes are user-specific history. None of these belong in neutral market rows or Job Market Map retention logic.

**Reason:** A vacancy is the same market object regardless of which user or agent interacts with it. A single canonical personal-history owner avoids conflicting ledgers while keeping Job Market Map reusable by Job Hunter, Reset / Edge, Plan Z and future consumers.
