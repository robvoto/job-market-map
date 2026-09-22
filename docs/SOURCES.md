# Source Behaviour

## SEEK

Status: result-card discovery plus write-once JD enrichment implemented.

Mechanics:
- query encoded in SEEK search path;
- one-off first live load uses `daterange=3`; normal ongoing discovery uses `daterange=1` with listed-date ordering;
- one SEEK result page typically exposes about 32 cards in JMM's Playwright snapshot;
- pagination uses `page=N`;
- collector validates source job link order against repeated card blocks;
- explicit terminal state `No matching search results` ends the query cleanly;
- transient empty renders are polled until a valid card structure or a bounded failure.

Captured card data currently includes title, employer, source ID, URL, relative posted text, exact UTC `listingDate`, employment type, location, work arrangement, visible salary, teaser, classification/subclassification, selected card tags and raw card evidence. The exact timestamp is read from SEEK's embedded `SEEK_REDUX_DATA.results.results.jobs[]` search state and matched to the visible card by source job ID.

Date integrity: exact card `listingDate` is preferred. When absent, SEEK labels such as `Listed three hours ago` are converted from the card's capture time and retained verbatim as evidence. An explicit `daterange` query can supply a conservative bound only when no usable label or exact date is available. A later exact `listedAt.dateTimeUtc` may replace a weaker relative/window-derived value. The exact-only SEEK pagination cutoff remains separate and never uses derived dates.

Known SEEK IDs are linked to current coverage without full re-ingest unless the card can repair a missing/stale date or improve a weaker date basis. `scripts/backfill_posting_dates.py --source seek` previews historical fills from `captured_at + posted_text`; `--apply` fills recoverable missing dates and clears stale `not_present` values only when retained source evidence is unusable, recording the previous value. If a canonical SEEK identity has no successful JD-fetch marker, its job page is opened once. JMM stores the full neutral JD and fills missing neutral detail facts exposed by the source page; the detail page's exact `listedAt.dateTimeUtc` can improve a weaker stored date. The permanent marker prevents normal future refetches after a successful detail capture. During JMM-007, this happens progressively inside the same pass: resumed/discovered jobs are JD-caught-up before coverage is allowed to run far ahead, and pass completion requires both coverage completion and zero required JD remainder.

Extra fresh runs inside 24 hours use the exact ordered card timestamps to stop after crossing a conservative prior-run cutoff. This optimization is fail-closed and does not replace the normal full 1-day daily reconciliation.

## LinkedIn

Status: geography-first HTTP card discovery, resumable campaign state and queued direct public-page JD enrichment for newly discovered jobs implemented.

Mechanics are based on Job Hunter's proven implementation:
- discovery uses `python-jobspy` over HTTP, not Chromium/Playwright;
- production discovery uses a blank search term plus each enabled geography's LinkedIn location and native LinkedIn-ID dedupe;
- JMM owns exact 10-position source offsets and uses bounded retry/terminal confirmation because LinkedIn can transiently return a short or empty page before later real results;
- discovery collects cards first; newly discovered jobs enter JMM’s pending JD queue and are enriched through the direct public-page helper before the cycle is complete. JMM-003 remains the on-demand fallback for a missing canonical JD;
- LinkedIn's public guest endpoint returns HTTP 400 at offset 1000. Reaching that ceiling is reported as `INCOMPLETE_CAP`, never as complete coverage;
- a vacancy detail page is fetched only through explicit/on-demand enrichment, not merely because a card was discovered;
- apply method uses the current public page's direct apply URL: trustworthy external URL -> `external_apply`; no external URL -> `easy_apply`; otherwise unknown;
- explicit `Reposted` sets `reposted=true`; repost identity/merging remains JMM-008's responsibility;
- explicit `No longer accepting applications` supplies the LinkedIn closed source status;
- `applicant_count` is stored only for an exact numeric count such as `30 applicants` or `187 applicants`; threshold text such as `Be among the first 25 applicants` is not converted into a count;
- personal/UI activity such as `Viewed` is never canonical JMM data. Low-value badges such as `Promoted`, `Actively reviewing applicants` and `Be an early applicant` are not promoted into structured canonical fields.

Existing LinkedIn rows are not bulk re-fetched merely to fill these fields. Unknown stays unknown unless a vacancy is fetched later for a valid enrichment reason.

Date integrity: JobSpy's exact source date and LinkedIn's exact card/detail `<time datetime>` values take precedence. The visible relative card label is retained and converted using its capture time when exact data is absent; the explicit LinkedIn lookback window supplies a conservative bound only when no label exists. Historical LinkedIn rows require a bounded source recheck because old captures did not retain their search window or relative labels. Use `scripts/backfill_posting_dates.py --source linkedin --limit N --apply`; each recheck records source text, conversion basis and unavailable/closed outcomes. Never substitute first-seen/last-seen times.

The old browser/snapshot LinkedIn path has been removed. LinkedIn has no Playwright/Chromium runtime dependency.

## APSJobs

Status: registry entries exist; neutral collector not yet implemented.

Date integrity: no APSJobs result/detail adapter currently exposes a validated published-date field. If a future adapter exposes relative text, convert it using its capture time; if the source query itself has an explicit freshness window, use only its conservative boundary. First-seen/last-seen and imported Job Hunter observation times are not substitutes.

Do not mark APSJobs coverage complete until its card/result parser and exhaustion rules are implemented and measured.

The field-capability endpoint reports APSJobs fields as `unknown` until a
neutral adapter proves what the source supplies. This is deliberately not
`not_applicable`: lack of an adapter is not evidence that APSJobs cannot
provide a field.

All source adapters use the same four explicit field states. A blank card
value defaults to `unknown`; adapters may emit `not_present` or
`not_applicable` only when the relevant source evidence justifies it.

## Additional sources

New sources should be added when they materially increase coverage or reach roles not well represented on SEEK/LinkedIn. Every new source needs:
- card-only extraction where technically possible;
- stable identity strategy;
- explicit exhaustion/completion rule;
- parser fixture/regression tests;
- source behaviour documented here;
- no policy filtering in the collector.


## SEEK state-wide coverage

SEEK now has a separate whole-state coverage path for NSW, ACT and QLD. Oversized state/classification partitions are recursively split via SEEK's own classification, subclassification and work-type refinements. See `docs/SEEK_COVERAGE.md`.
