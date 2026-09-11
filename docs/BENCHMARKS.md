# Source Benchmarks

Measured source collection behaviour. Update this document when source behaviour changes materially.

## 10 September 2026 — first Human MCP snapshot benchmark

Query: `technical implementation`, Sydney NSW, last 7 days.

| Source | Unique job links in initial snapshot | Open time | Snapshot time | Visible text | Notes |
|---|---:|---:|---:|---:|---|
| SEEK | 32 | 3.537s | 3.536s | 18,441 chars | One initial snapshot exposes a much larger card set; suitable for card-first bulk extraction. |

### Implication

SEEK can likely collect dozens of cards per roughly 7-second initial page cycle before pagination overhead.

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

## 11 September 2026 — first production LinkedIn JobSpy/HTTP smoke

Query: `business analyst`, New South Wales, last 1 day, diagnostic budget 2 results.

- JobSpy cards observed: **2**.
- New jobs: **1**; existing exact-ID job: **1**.
- Direct public-page detail fetches: **2**.
- Canonical LinkedIn JDs stored: **2**.
- Detail failures: **0**.
- End-to-end elapsed time: **3.7 seconds**.
- Existing naturally rediscovered job exposed an exact **114 applicants** count; another job's count stayed NULL because no exact number was present.
- Browser/Playwright use: **none**.

This was deliberately tiny to prove the original HTTP transport, exact-ID dedupe and shared detail helper. It is historical evidence only: production LinkedIn discovery was subsequently changed to geography-first, cards-only 20-job chunks. Direct detail/JD fetching remains available on demand through JMM-003 rather than being performed for every discovered LinkedIn card.
