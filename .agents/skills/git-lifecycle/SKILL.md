---
name: git-lifecycle
description: Use for commit, push, or main-branch integration in this repository, or whenever Git state is unclear.
---

# Skill: Git Lifecycle

Use this whenever code work is started or finished, or whenever the human asks about commit/push/main state.

**Current scope note:** Job Market Map today is a single shared checkout on `main`, with no worktree infrastructure and no executable Git-closure gate (unlike Job Hunter). This skill is a genuine placeholder for that simpler reality — expand it (branches, worktrees, an enforced closure check) if and when JMM's workflow actually grows that complexity. Do not reference tooling that does not exist in this repo.

## Operator contract
The human should not need to remember Git mechanics.

- Never push to the remote (`origin/main`) without the human's explicit approval in the current turn, even after implementation is validated and committed locally.
- `commit` alone never means `push`, and `push` alone never means the human has reviewed the change beyond what they asked for.
- If approval intent is unclear, ask one concise question: "Committed locally on `main`. Push to `origin/main` now?"
- Only create a task branch/worktree when the human asks for isolation, or when concurrent work on this repo is known to be in progress. Otherwise, commit directly to the checked-out branch (normally `main`) as the existing history does.

## Before committing
1. Run `git status` and inspect what changed; never assume the working tree only contains the current task's edits.
2. Preserve unrelated dirty work. Never stash, reset, discard, or commit another agent's/session's changes without explicit coordination.
3. Stage only the files that belong to the current task — avoid `git add -A`/`git add .` when unrelated changes are present.
4. Run the required validation for the change before committing (tests, Ruff, `python -m compileall`, `git diff --check`) — see `.agents/skills/job-market-map/SKILL.md` for the domain-specific baseline.

## Failure handling
- Any failed tool call, shell command, or validation step is a stop condition: report the failure immediately rather than continuing down the same line of work.
- Diagnose the root cause before retrying; do not skip hooks or bypass a failing check to force a commit through.
- Use `uv run ...` for every Python/test/lint command in this repo — see `.agents/skills/mcp-tooling/SKILL.md`.

## Mandatory status wording
End Git-related work with one clear statement of state:
- `NOT COMMITTED — uncommitted changes on <branch>`
- `COMMITTED — <sha> on <branch>, not pushed`
- `PUSHED — <sha> on origin/<branch>`

Do not use `done`, `shipped`, or `merged` ambiguously in place of one of these.
