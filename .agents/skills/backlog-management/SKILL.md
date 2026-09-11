---
name: backlog-management
description: "Use ONLY for backlog work: Google Sheet rows, JMM IDs, priorities, duplicates, implementation state, evidence, human review flags, or adding/updating backlog items. Do NOT use for code implementation except to update backlog evidence."
---

# Skill: Backlog Management

Use when creating, updating, deduplicating, grooming, or analysing Job Market Map backlog items.

## Source of truth
- Canonical backlog: Google Sheet `job_market_map_backlog`, worksheet/tab name `Backlog`.
- Spreadsheet ID: `1UX7xEKgHi1gRkPs9PGqDlWLfWSiMgRKkDKPYTXtygOs`.
- Do not confuse the spreadsheet file name with the tab name. For Sheets calls, always use `sheet_name="Backlog"`, never `sheet_name="job_market_map_backlog"`.
- Service account: `job-hunter-backlog@angular-log-prj.iam.gserviceaccount.com`. A Google 403 means this account lacks permission on the Sheet; fix sharing before diagnosing tool/connector failure.
- This Google Sheet is the only backlog source of truth. Do not create a competing markdown backlog. Local `docs/CURRENT_STATE.md` is operational session handoff only, not a backlog substitute.
- Read the sheet header row first and update by column name, never by fixed position. Do not add, remove, or rename columns unless explicitly agreed.

## Required access rule
For canonical backlog reads/writes, follow `.agents/skills/mcp-tooling/SKILL.md` for the current runtime's authorised Sheets path. If the exact live sheet cannot be accessed:
- Stop backlog work.
- Do not claim the sheet was updated.
- Do not use local exports, docs, copied spreadsheets, or memory as a substitute backlog.
- Report the exact failure and stop that path before backlog-dependent work, unless Rob explicitly directs otherwise.
- Browser automation is not a backlog fallback path.

For backlog updates, always read first, derive the current row and header-column coordinates from the returned data, write only the intended cells, then read again and verify the persisted values. Never hard-code JMM row/column numbers from memory.

## Column order (verified 2026-09-11)
1. ID, 2. Creator, 3. Title, 4. Epic, 5. Type, 6. Priority, 7. Size, 8. Problem, 9. Outcome, 10. Acceptance Criteria, 11. Original Source, 12. Duplicate Of, 13. Depends On, 14. Notes, 15. Implementation State, 16. Implementation Date, 17. Implemented By, 18. Evidence, 19. Human Review Needed, 20. Review Category, 21. Review Reason, 22. Created Date, 23. Modified Date, 24. Resolved Date

## Creating a backlog row from a rough idea
When the human gives a rough idea, create a complete row rather than asking them to fill every field.

Fill the columns that exist in the sheet:
- `ID`: next stable `JMM-###` number — read existing IDs, find the highest valid `JMM-###`, increment by 1. Ignore malformed placeholders.
- `Creator`: `Human` if the human supplied the idea; `Agent` only if the agent discovered it while working.
- `Title`: concise action phrase.
- `Epic`: use an existing epic where possible.
- `Type`: one of `Story`, `Bug`, `Task`, `Spike`, `Decision`, `Risk`.
- `Priority`: `High`, `Medium`, or `Low`.
- `Size`: `S`, `M`, `L`, or `XL`.
- `Problem`: why this matters.
- `Outcome`: what success looks like.
- `Acceptance Criteria`: testable completion checks.
- `Original Source`: file, conversation, or code area that triggered it.
- `Duplicate Of`: leave blank unless clearly duplicate.
- `Depends On`: existing IDs that must happen first.
- `Notes`: assumptions, uncertainty, or implementation cautions — including ownership-boundary reminders (e.g. "must not create a second dedupe rule").
- `Implementation State`: `Not Done` by default unless implementation is verified.
- `Implementation Date`, `Implemented By`, `Evidence`: fill only when implementation is verified.
- `Created Date`: set when a new row is created.
- `Modified Date`: update when the row is materially changed.
- `Resolved Date`: set when the item is resolved; leave blank while still open.

## Selecting work
- Do not pick or implement rows where `Implementation State = Done`.
- `Done` rows may only be touched when the human explicitly asks to audit, reopen, correct evidence, or revise that specific row.
- Normal agent task selection must use rows where `Implementation State` is not `Done`, preferably `Not Done` or `Partially Done` after confirming scope.
- Before implementing, check `Depends On` and `Notes` for ownership-boundary constraints (Job Market Map is neutral market infrastructure — see `.agents/skills/job-market-map/SKILL.md`).

## Implementation State rules
- `Implementation State` is a delivery/status field, not an audit todo field.
- Allowed values: `Done`, `Not Done`, `Partially Done`, `Obsolete`.
- Code-check uncertainty belongs in `Human Review Needed`, `Review Category`, and `Review Reason`, not in `Implementation State`.

## Code-check evidence
Evidence must name files/functions/tests, not vague claims.
Good evidence: "`api/main.py:lookup_job` adds `GET /v3/jobs/lookup`; `tests/test_api.py` covers exact/unknown/malformed/duplicate-linked cases; commit `<sha>`."
Bad evidence: "Looks implemented."

## Deduplication
Before adding a row:
1. Search existing titles and notes for similar words.
2. If similar, update the existing row rather than adding a duplicate.
3. If uncertain, add the new row but note the possible duplicate in `Notes`.

## Finish format
Report:
- Row(s) created or updated.
- Any duplicates suspected.
- Any implementation state changes and evidence.
