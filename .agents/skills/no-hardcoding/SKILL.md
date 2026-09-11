---
name: no-hardcoding
description: Use whenever changing thresholds, partition limits, page sizes, admin-tunable behaviour, identity construction, or source-parsing rules. Usually combine with the job-market-map skill.
---

# Skill: No Hardcoding

Use before adding/changing thresholds, defaults, identity construction, or source-parsing rules.

## Core rule
Operational judgement must not hide as a scattered constant in feature code. Silent fallbacks are not acceptable — if required data is missing, surface an explicit error or fix the owner.

## Admin settings ownership
Configurable/operational behaviour belongs in the settings module (`collector/settings.py`, exposed at `GET/PUT /v3/admin/settings`), not a Python constant embedded in a collector/API module.

Examples already covered this way: `collection.seek_partition_max_results`, `api.default_page_size`, `api.max_page_size`, `retention.*` thresholds and their enable switches, `collection.default_freshness_days`. When adding a new tunable value of this kind, add it as a named setting with `help_text`, not an inline literal.

- Global collection/API/retention behaviour belongs in admin settings.
- Do not hardcode admin-tunable behaviour in Python.
- If unsure whether a new knob should be a setting or a genuine code constant (e.g. a fixed SQL column name), ask before implementing.

## Identity and canonical construction
`identity_key` construction is owned by `collector/identity.py` and the DB triggers in `collector/schema.sql`. Do not reimplement identity-key derivation, source+source_job_id matching, or canonical-URL fallback logic inline in a new module — call the existing `collector/db.py` lookup helpers (`get_job_by_id`, `get_job_by_source_id`, `get_job_by_identity_key`) instead.

Duplicate-link evidence is owned by the existing dedupe pipeline (see `docs/DECISIONS.md` and JMM-008). Do not add a second duplicate/canonicalisation rule in a new consumer-facing endpoint or script; reuse `duplicate_links` table evidence as-is.

## Forbidden
- A new Python constant for a value that changes collection/API/retention behaviour and would make sense as an admin setting.
- Reimplementing `identity_key` matching, source-ID lookup, or duplicate inference outside the owning module.
- Silent fallback default for a missing setting (`.get(..., 5)`); if a setting is required and absent, that is a setup bug — surface it.
- Fuzzy/text-similarity matching presented as if it were exact identity resolution (see `/v3/jobs/lookup`, JMM-009 — exact-identity paths must never fall back to `/v3/jobs/search` semantics).

## Required
- Before editing, inspect `collector/settings.py`, `collector/identity.py`, and `collector/db.py` for the area being changed.
- Reuse existing DB/query and response-serialization helpers rather than duplicating them (see `api/main.py:_job_detail_payload` as the canonical job-detail response builder).
- Source-specific parsing rules (SEEK, LinkedIn, APSJobs) must stay in their own source-adapter module and be explicit about what they don't support; never silently fall back to another source's parsing shape.
- If a change would force a workaround, legacy pattern, or unnecessary duplication, say so before editing and ask if a better approach is feasible within scope.

## Checklist
- Search for a new bare numeric/string literal that affects collection, API paging, or retention behaviour.
- Search for `.get(..., fallback)` around required config/settings.
- Search for inline identity/source-ID matching outside `collector/db.py` / `collector/identity.py`.
- Search for a second duplicate-detection or canonicalisation code path.
