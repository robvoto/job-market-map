# Data Model

## `jobs`

Current neutral source-vacancy rows. Fields describe the vacancy and collection evidence only: source/source job ID, stable `identity_key`, URL, title, employer, geography/location, salary/work type, source posting metadata, card evidence, classifications, first/last seen, fingerprints and archive state.

There are no user or agent activity flags on this table.

## `card_captures`

Recent append-only source observations used for evidence, parser diagnostics and source-change analysis.

## `queries` / `job_query_hits`

Neutral discovery provenance: which source/search/geography found a vacancy and how often. Query origin is discovery metadata, not career fit.

## `duplicate_links`

Non-destructive possible-duplicate evidence between market rows. Rich card evidence supports confidence; rows are not automatically destroyed/merged.

## `job_tombstones`

Small neutral identity/history records retained after stale detailed rows are removed. This prevents an old vacancy being rediscovered as falsely new.

## `seek_partitions` / `seek_partition_jobs`

Coverage proof for SEEK whole-state collection. Partition status and deduplicated job membership allow the system to prove whether a state/classification/subclassification/work-type tree was completely collected.

## `consumer_checkpoints`

Processing progress for independent consumers such as `job-hunter`, `reset-edge`, and `plan-z`.

A checkpoint means only "this consumer safely processed through market feed row N". It is not `shown`, `seen`, `viewed`, `applied`, or any other personal activity.

## Personal activity is external

Job Hunter JH-305 is the intended canonical owner of per-user activity/outcomes, keyed by user + stable market identity and, where needed, agent + timestamp. Job Market Map exposes `identity_key` so consumers can join to that service without duplicating its data.
