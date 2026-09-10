# Source Benchmarks

Measured card-only collection behaviour. Update this document when source behaviour changes materially.

## 10 September 2026 — first Human MCP snapshot benchmark

Query: `technical implementation`, Sydney NSW, last 7 days.

| Source | Unique job links in initial snapshot | Open time | Snapshot time | Visible text | Notes |
|---|---:|---:|---:|---:|---|
| LinkedIn | 7 | 1.206s | 2.124s | 1,546 chars | Virtualised list; initial DOM does **not** represent the full result set. Must scroll/page before declaring coverage. |
| SEEK | 32 | 3.537s | 3.536s | 18,441 chars | One initial snapshot exposes a much larger card set; suitable for card-first bulk extraction. |

### Implication

Do not estimate full-map duration by JD-reading speed. The mapper does not open JDs.

SEEK can likely collect dozens of cards per roughly 7-second initial page cycle before pagination overhead. LinkedIn requires additional scrolling/pagination mechanics and must be benchmarked again after those mechanics are implemented.

A source run is not `complete` merely because a snapshot returned successfully. Completion means the collector reached the source's result-space boundary without silently truncating virtualised/unloaded cards.

## 10 September 2026 — first exhausted SEEK query

Query: `technical implementation`, Sydney NSW, last 7 days.

- SEEK reported: **220 jobs**.
- Parsed pages: **7**.
- Card observations: **220**.
- Page 8: explicit terminal state `No matching search results`.
- Individual JDs opened: **0**.
- The first exhaustive implementation took about 85 seconds including a 15-second terminal-page diagnostic wait that has since been removed by explicit terminal-state detection.

This establishes that a broad card-only query can ingest hundreds of jobs in roughly minutes, not hours. Future timing should use subsequent clean runs rather than this debugging run.
