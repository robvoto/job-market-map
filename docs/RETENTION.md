# Retention and Staleness

## Principle

Job Market Map is intended to be useful not only as a live feed, but also as historical market evidence. Old card text can later support audit, scam/phishing investigation, reposting analysis, and learning from application outcomes. Storage is cheap compared with losing evidence that cannot be reconstructed later.

Therefore **destructive retention is opt-in**. By default, canonical job evidence and raw card captures are preserved indefinitely. A job becoming old or stale is not, by itself, permission to erase evidence. Personal history remains outside Job Market Map under Job Hunter/JH-305.

## Default lifecycle

With the default settings, no destructive retention phase runs:

- `retention.prune_raw_captures_enabled = false` — repeated raw captures are retained.
- `retention.archive_jobs_enabled = false` — canonical jobs are not compacted and teaser/raw card evidence is retained.
- `retention.remove_archived_jobs_enabled = false` — detailed archived rows are not replaced by tombstones.

The existing age thresholds remain configurable for the future, but they are **inert until their corresponding switch is enabled**:

- `retention.raw_capture_days = 30`
- `retention.archive_after_days = 30`
- `retention.remove_archived_after_days = 120`

This keeps future cleanup easy to enable without silently imposing it today.

## If cleanup is deliberately enabled later

The three phases are independent policy gates:

1. Raw-capture pruning deletes repeated `card_captures` older than `raw_capture_days`.
2. Archiving marks the job as archived in `job_observation_state` and clears `teaser_text` / `raw_card_text` from the detailed canonical row after `archive_after_days`.
3. Detailed-row removal replaces an already archived job older than `remove_archived_after_days` with a small neutral tombstone so the source identity is still recognised if it reappears.

The removal threshold must remain greater than the archive threshold.

## Why preservation is the default

Historical card evidence may answer questions we do not yet know we will ask, for example:

- whether suspicious or scam-like wording repeats across postings;
- whether an employer or recruiter repeatedly reposts effectively the same vacancy;
- what characteristics are common in jobs that later produce rejection or no response;
- how title, teaser, salary or visible card metadata changed over time;
- whether a future dedupe or quality rule would have classified an old posting differently.

We can make retention more aggressive later when measured database size/performance justifies it. We cannot recreate discarded source evidence later.
