# Architecture

## Purpose boundary

Job Market Map is **market infrastructure**, not a career agent.

It owns:
- discovery query execution;
- result-card extraction;
- raw evidence capture;
- one current neutral JD per job when obtained on demand;
- source-normalised fields;
- same-source identity;
- conservative cross-source duplicate hints;
- first/last-seen lifecycle;
- consumer-progress checkpoints for feed processing;
- API access for multiple agents;
- retention and compaction.

It does not own:
- fit scoring;
- career-lane classification;
- mandatory-experience judgement;
- seniority judgement;
- CV tailoring;
- application decisions;
- Reset / Edge policy;
- Plan Z policy;
- Job Hunter policy.

## Data flow

```text
LinkedIn cards ----\
SEEK cards ----------> source collectors ---> neutral ingest ---> SQLite market.db
Employer boards -----/                              |                 |
Other sources --------------------------------------/                 |
                                                                     v
                                                              Local FastAPI
                                                                     |
                         +---------------------------+---------------+----------------+
                         |                           |                                |
                     Job Hunter                 Reset / Edge                       Plan Z
                     policy                      policy                            policy
```

A consumer may decide that a card deserves an individual JD review. That acquisition occurs **outside** the neutral bulk-mapping stage, but the resulting neutral JD belongs in JMM and is reused from there rather than permanently duplicated by the consumer.

## Why SQLite

SQLite is appropriate because:
- the data is local and primarily single-machine;
- WAL mode supports concurrent readers plus bounded writes;
- tens or hundreds of thousands of rows are trivial at this scale;
- SQL makes freshness, dedupe, history and agent queries deterministic;
- it avoids large ad-hoc text files becoming de facto state;
- backups are a single database file plus exports.

Agents should normally use the API rather than writing SQLite directly. Direct DB access is acceptable for maintenance/tests inside this repository.

## Concurrency

The database is the shared canonical state. Collectors and agents must:
- use short transactions;
- enable foreign keys and WAL;
- never hold a DB write transaction while waiting on browser/network work;
- use idempotent upserts;
- record observations rather than overwriting history destructively;
- never infer that another agent's presence is an error.

Browser concurrency is owned by Human MCP, not this project. Signed-in card collection must reuse Rob's existing browser and one workflow-owned tab/session.

## Identity strategy

### Same source
Prefer the source's stable job ID. If unavailable, canonical URL is the fallback.

### Cross source
Do not merge merely because title + employer look similar. Record a `possible_same_job_group` only when evidence is strong enough to be useful. Consumers can still see both source records.

Incorrectly collapsing two live vacancies is more damaging than temporarily retaining a duplicate.

## Freshness model

`first_seen_at` = first time this database observed the vacancy.

`last_seen_at` = latest time a collector observed the same vacancy.

`posted_at` / `posted_text` = source claim about when the employer/board posted it.

These are different facts and must not be conflated.
