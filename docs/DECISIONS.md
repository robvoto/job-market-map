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

## ADR-005 — Archive then tombstone removal
**Decision:** Retention has two stale phases: archive/compact, then remove the detailed unimportant row while preserving a tiny tombstone identity.

**Reason:** Old raw observations are low-value bulk, but identity memory prevents stale jobs being rediscovered as falsely new.

## ADR-006 — Versioned API is the consumer boundary
**Decision:** Job Hunter and other agents consume `/v2`; they do not write SQLite directly.

**Reason:** A stable API can validate writes, provide cursors/idempotency, preserve lifecycle invariants and evolve storage without breaking every agent.

## ADR-007 — Runtime settings are admin data
**Decision:** Normal operational tuning is controlled through typed settings and Admin/API, with helper text and bounds.

**Reason:** Rob should be able to tune retention, source timing, duplicate confidence, page sizes and query activation without editing Python.

## ADR-008 — Registry JSON seeds; SQLite operates
**Decision:** The JSON query registry bootstraps known search coverage, while SQLite is the live operational state.

**Reason:** Admin-added/disabled queries must take effect immediately and must not be undone by a seed sync.


## ADR-007 — User activity is not market data
**Decision:** `shown`, `seen`, `reviewed`, `applied`, `rejected`, and `dismissed` live in a per-user activity ledger keyed by stable job identity, never on neutral `jobs`/tombstones. The API moved to `/v2` before external consumer integration.

**Reason:** A vacancy is the same market object regardless of which user or agent has interacted with it. Mixing user state into `jobs` breaks neutrality and multi-user/multi-agent reuse.
