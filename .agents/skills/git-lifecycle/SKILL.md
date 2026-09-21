---
name: git-lifecycle
description: Use before repository edits and for commit, push, or main-branch integration, or whenever Git state is unclear.
---

# Skill: Git Lifecycle

Use this whenever code work is started or finished, or whenever the human asks about commit/push/main state.

## Isolation contract

Repository edits must start in an isolated task branch/worktree. This is mandatory even when no concurrent agent is currently visible, because another agent/session may begin work at any time.

Before the first edit:
1. Fetch `origin` and inspect `git status`, the current branch, and `git worktree list`.
2. Start from the latest verified `origin/main` unless the task explicitly depends on another branch.
3. Create a dedicated branch named `task/<ticket-or-short-purpose>` and a dedicated worktree for that task.
4. Do not switch the branch of an existing worktree that another task/session may be using.
5. Do not edit directly on `main` unless the human explicitly instructs that exception in the current session.
6. Keep one task/purpose per branch so concurrent work can be merged or rebased independently.

If the requested task branch already exists and is clearly the branch assigned to the current task, reuse its worktree after confirming it is clean or that every dirty change belongs to that same task.

## Operator contract
The human should not need to remember Git mechanics.

- Completed intended work is committed and pushed automatically after validation, then integrated into `main` when safe, unless the human explicitly says not to push or not to integrate.
- Do not wait for a second approval when the human has already requested automatic commit/push.
- Stop and ask only for a genuine safety gate: unrelated changes, destructive actions, force-pushes, deployment/restart, credentials/external messages, or unresolved conflicting work.
- JMM runtime lifecycle is one of those safety gates and is owned by `.agents/skills/job-market-map/SKILL.md`: Git integration never authorises starting, stopping, restarting, reloading, killing, or otherwise changing JMM runtime state.
- Before integrating, fetch `origin/main` again. If it advanced, incorporate it into the task branch and revalidate rather than overwriting or force-pushing another agent's work.

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
