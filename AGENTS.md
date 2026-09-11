# Agent Instructions

Minimal always-loaded routing instructions. This file is not the project manual and must stay small.

## Default workflow

1. Use `docs/INDEX.md` to find the smallest relevant project document.
2. Use `.agents/skills/INDEX.md` to choose the smallest relevant task skill or skill combination.
3. Read local-only `docs/CURRENT_STATE.md` first, if present, for the latest verified crawl state, blocker, and exact resume point.
4. Read the selected task skill before changing code, configuration, instructions, Git state, runtime behaviour, or the API contract.
5. Inspect the current files/state before editing. Do not load the whole repository unless the task genuinely requires a broad audit.
6. For any commit, push, or `main` integration action, use `.agents/skills/git-lifecycle/SKILL.md`.
7. For canonical backlog work, use `.agents/skills/backlog-management/SKILL.md`.

## Durable rule placement

When a lesson or rule should apply beyond the current chat/session:

- First place it in the existing skill that owns that behaviour.
- If no suitable skill exists, create a focused skill and add it to `.agents/skills/INDEX.md`.
- Add detail to a doc under `docs/` when it is too large for a skill.
- Do not add implementation-specific, runtime-specific, or incident-specific detail to this file.
- `AGENTS.md` may point to the owner; it must not duplicate the owner's detailed rules.

Use `.agents/skills/instruction-maintenance/SKILL.md` whenever changing agent instructions, skills, adapters, or instruction structure.

## Navigation

- Project documentation index: `docs/INDEX.md`
- Task skills: `.agents/skills/INDEX.md`

## Universal rules

- Never guess or invent; inspect the authoritative source first.
- Keep context and changes bounded to what the task requires.
- Do not hardcode behaviour that belongs in admin settings, config, or another authoritative owner — see `.agents/skills/no-hardcoding/SKILL.md`.
- Do not add hidden fallbacks, compatibility shims, dead paths, or broad exception swallowing unless explicitly approved.
- Job Market Map is neutral/global market infrastructure only; it does not own personal activity/outcomes — see `.agents/skills/job-market-map/SKILL.md`.
- Do not claim completion without validation evidence (tests, Ruff, `git diff --check`).
- Preserve unrelated work when other agents or sessions may be active.
- Route specialised behaviour through its owning skill instead of expanding this file.

## Finish report

Report only what matters:
- what changed;
- validation performed and result;
- remaining risk or follow-up;
- for Git work, the integration state required by `.agents/skills/git-lifecycle/SKILL.md`.
