---
name: code-quality
description: Use for any code implementation or review to keep the repository lean: no duplicate logic, stale/dead paths, superseded helpers, or parallel implementations left behind.
---

# Skill: Code Quality

Use for every implementation/review task that changes executable code.

## Core rule
Keep one canonical implementation for each behaviour.

- Reuse/refactor the existing owner before adding parallel logic.
- Do not leave old and new implementations active side by side after a replacement is proven.
- Remove superseded helpers, branches, compatibility shims, dead paths, obsolete flags/settings and stale tests/docs when they are no longer required.
- Do not retain code merely because deleting it feels risky; verify callers/references first, then remove what is demonstrably unused or superseded.
- Do not delete uncertain code blindly. If ownership/use is unclear, investigate first and record the unresolved risk.

## Before adding code
1. Search for existing implementations, helpers and tests that already own the behaviour.
2. Prefer extending/refactoring that owner over creating a second path.
3. If a second implementation is temporarily necessary for a migration, make the temporary state explicit and define/remove the old path in the same task when safe.

## Before calling work complete
Check the touched area for:

- duplicate or near-duplicate logic;
- superseded functions/classes/modules;
- unreachable/dead branches;
- stale feature flags, settings, routes or adapters;
- tests that only cover behaviour no longer supported;
- docs/comments that describe an old implementation;
- compatibility/fallback code that is no longer justified.

Remove confirmed stale material as part of the implementation rather than creating cleanup debt.

## Guardrails
- Preserve unrelated active work from other agents/sessions.
- Do not perform broad speculative cleanup outside the task boundary.
- Behaviour changes still require regression tests and normal repository validation.
- Architecture/domain ownership remains with the relevant domain skill; this skill owns implementation hygiene only.

