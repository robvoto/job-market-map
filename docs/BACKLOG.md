# Job Market Map Backlog

Simple working backlog for the Job Market Map POC.

Status values: `Discovery`, `Backlog`, `In Progress`, `Done`, `Blocked`.

| ID | Status | Priority | Title | Why / problem | Done when | Notes |
|---|---|---:|---|---|---|---|
| JMM-001 | Discovery | High | Resolve JD enrichment ownership | Current ADR-003 says JMM is card-only and consumers open JDs. The newer design direction is that JMM should own neutral JD acquisition/enrichment when a consumer requests it. We need one rule, not two. | ADR-003 and architecture docs are updated with the agreed ownership model. | Do not implement enrichment until this is resolved. |
| JMM-002 | Backlog | High | Add canonical JD snapshot/version model | If JMM owns enriched job evidence, a job needs versioned JD snapshots rather than one mutable description field. | A JD snapshot has a stable ID, job identity, captured timestamp, source/evidence, content hash/version relationship, and can coexist with older snapshots. | Depends on JMM-001. |
| JMM-003 | Backlog | High | Add on-demand neutral job enrichment flow | A consumer such as Job Hunter should be able to request a job and receive the existing JD snapshot, or trigger neutral enrichment if none exists. | API flow returns an existing snapshot when available; otherwise JMM obtains, stores and returns neutral JD evidence without adding user-specific analysis/state. | Depends on JMM-001 and JMM-002. |
| JMM-004 | Backlog | High | Expose JD snapshot provenance through `/v3` | Consumers need to know exactly which market evidence/version they analysed. | `/v3` responses expose the relevant JD snapshot/version identifier and capture timestamp without coupling consumers to SQLite columns. | Supports Job Hunter analysis provenance. |
| JMM-005 | Discovery | Medium | Define JD refresh/version rules | A later employer edit must not silently overwrite the evidence used by earlier analysis. | Refresh triggers, change detection, unchanged-content handling and new-version rules are documented and tested. | Avoid arbitrary refresh intervals until source behaviour is understood. |
| JMM-006 | Discovery | Medium | Define consumer contract for latest vs historical JD | Consumers may need the latest JD or an exact historical snapshot. | API contract clearly distinguishes current/latest evidence from retrieval by exact snapshot/version ID. | Depends on JMM-002. |
| JMM-007 | Backlog | Medium | Add regression tests for JD evidence lifecycle | Versioning/enrichment bugs could corrupt provenance or create duplicate evidence. | Tests cover first capture, repeat unchanged capture, changed JD, historical retrieval and consumer provenance fields. | Implement with JMM-002 to JMM-006. |

## Boundary reminder

- **JMM owns market truth:** job identity, source evidence, card data, JD snapshots/versions, market lifecycle and duplicate relationships.
- **JH-305 owns personal activity truth:** applied, rejected, interview, no response, viewed, agent-presented and similar events.
- **Job Hunter owns Rob-specific analysis/decision truth:** fit, requirement coverage, scores, recommendations and user-specific evidence mapping.

Do not add Job Hunter application state or scoring into JMM just because the systems integrate.
