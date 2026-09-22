# Data Model

## `jobs`

Current neutral source-vacancy rows. Fields describe the source vacancy itself: source/source job ID, stable `identity_key`, URL, title, employer, geography/location, raw `salary_text`, deterministic salary normalization (`salary_normalized` state, min/max, `salary_bound`, period, currency and package/super qualifier), employment/workplace type, `posted_at` and its `posted_at_basis`, source status/expiry/apply method, classifications, card evidence and fingerprints. `salary_bound` is `exact`, `range`, `from`, or `up_to` only when the numeric shape is proven. Posting-date basis is `source_exact`, `source_relative`, or `search_window_bound`; the last is a conservative freshness boundary, not an exact instant. Original relative text and query-window evidence stay in `card_captures.raw_json`. Raw salary evidence is always retained. Raw-field presence and normalization confidence are separate: a source value such as `Competitive` is raw salary evidence (`field_states.salary=known`) but its deterministic normalized state is `unknown`. Blank salary text alone remains `unknown`; only explicit source evidence can mark it `not_present`. Period-less or non-AUD-context numeric values keep a null period or currency rather than inventing one.

Collector-computed lifecycle fields are deliberately **not** stored on this master row. Relative posting labels are converted deterministically using the same card's `captured_at`; the original label remains capture evidence.

When a full JD has been obtained, the same canonical row may also hold `full_description`, `jd_fetched_at` and `jd_source`. JMM keeps one current JD only and reuses it; there is no JD snapshot/version history.

There are no user or agent activity flags on this table.

## `job_observation_state`

Operational collector state keyed by `job_id`: first/last observed time, capture count, archive flag and compaction time. These values describe JMM's collection activity, not the vacancy itself.

## `card_captures`

Recent append-only source observations used for evidence, parser diagnostics and source-change analysis. Relative display labels such as `Listed three hours ago` may live here as raw evidence.

## `queries` / `job_query_hits`

Neutral discovery provenance: which source/search/geography found a vacancy and how often. Query origin is discovery metadata, not career fit.

## `duplicate_links`

Non-destructive possible-duplicate evidence between market rows. Rich card evidence supports confidence; rows are not automatically destroyed/merged.

## `same_vacancy_links`

Auditable, non-destructive cross-source vacancy assignments. Each source posting keeps its own ID and URL, while `primary_job_id` points to the oldest matching JMM row used for JD fetching and downstream processing. `confidence`, `matching_signals_json`, and `detected_at` record why the assignment was made. The explicit rules require normalized title and employer plus either an exact rich-card fingerprint, substantial teaser/intro similarity at the configured minimum, or the configured minimum number of agreeing secondary signals (location, workplace type, employment type, salary, or classification). Uncertain matches remain only in `duplicate_links`.

## `job_tombstones`

Small neutral identity/history records retained after stale detailed rows are removed. This prevents an old vacancy being rediscovered as falsely new.

## `seek_partitions` / `seek_partition_jobs`

Coverage proof for SEEK whole-state collection. Partition status and deduplicated job membership allow the system to prove whether a state/classification/subclassification/work-type tree was completely collected.

## `consumer_checkpoints`

Processing progress for independent consumers such as `job-hunter`, `reset-edge`, and `plan-z`.

A checkpoint means only "this consumer safely processed through market feed row N". It is not `shown`, `seen`, `viewed`, `applied`, or any other personal activity.

## Personal activity is external

Job Hunter JH-305 is the intended canonical owner of per-user activity/outcomes, keyed by user + stable market identity and, where needed, agent + timestamp. Job Market Map exposes `identity_key` so consumers can join to that service without duplicating its data.
