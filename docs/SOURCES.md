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

Captured card data currently includes title, employer, source ID, URL, posted text, employment type, location, work arrangement, visible salary, teaser, classification/subclassification, selected card tags and raw card evidence.

Known SEEK IDs are linked to current coverage without full re-ingest. If a canonical SEEK identity has no successful JD-fetch marker, its job page is opened once. JMM stores the full neutral JD and fills missing neutral detail facts exposed by the source page, including the exact SEEK `listedAt.dateTimeUtc` posting timestamp when present. Relative card labels such as `3h ago` remain raw capture evidence only. The permanent marker prevents normal future refetches after a successful detail capture. During JMM-007, this happens progressively inside the same pass: resumed/discovered jobs are JD-caught-up before coverage is allowed to run far ahead, and pass completion requires both coverage completion and zero required JD remainder.

## LinkedIn

Status: an early browser/snapshot card prototype exists, but it is **not** the approved production path and is not scheduled. JMM-011 supersedes that design.

Approved JMM-011 mechanics are based on Job Hunter's proven implementation:
- discovery uses `python-jobspy` over HTTP, not Chromium/Playwright;
- JobSpy runs in an isolated subprocess with pagination-progress reporting and a bounded no-progress watchdog;
- discovery uses `linkedin_fetch_description=False` and deduplicates by native LinkedIn ID before detail work;
- each genuinely new/unfetched canonical vacancy gets at most one bounded direct public-HTML fetch, and that same response supplies JD plus neutral detail facts;
- apply method uses the current public page's direct apply URL: trustworthy external URL -> `external_apply`; no external URL -> `easy_apply`; otherwise unknown;
- explicit `Reposted` sets `reposted=true`; repost identity/merging remains JMM-008's responsibility;
- explicit `No longer accepting applications` supplies the LinkedIn closed source status;
- `applicant_count` is stored only for an exact numeric count such as `30 applicants` or `187 applicants`; threshold text such as `Be among the first 25 applicants` is not converted into a count;
- personal/UI activity such as `Viewed` is never canonical JMM data. Low-value badges such as `Promoted`, `Actively reviewing applicants` and `Be an early applicant` are not promoted into structured canonical fields.

Existing LinkedIn rows are not bulk re-fetched merely to fill these fields. Unknown stays unknown unless a vacancy is naturally fetched later for another valid reason.

After JMM-011 proves the HTTP path, remove the obsolete browser LinkedIn collector rather than leaving two source implementations.

## APSJobs

Status: registry entries exist; neutral collector not yet implemented.

Do not mark APSJobs coverage complete until its card/result parser and exhaustion rules are implemented and measured.

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
