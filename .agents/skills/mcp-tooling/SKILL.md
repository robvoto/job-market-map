---
name: mcp-tooling
description: Use for repository/filesystem access, connected Google Sheets tooling, or tool/transport failure recovery; apply runtime-specific connector names only when that runtime exposes them.
---

# Skill: MCP Tooling

Use for project filesystem/tool access and whenever a connector call is unreliable.

## Source of truth
- Job Market Map repo: `/home/robvoto/projects/job-market-map`.
- A local coding runtime that already has direct repository shell/filesystem access (e.g. Claude Code) should use that native access instead of a connector. The same safety rules still apply.
- In a connector-based runtime without direct repo access, use the authorised Sheets/repo connector documented for that runtime (for example, the documented Sheets/repo tools exposed by the currently authorised connector). Do not silently switch connectors mid-task; if the primary path fails, report the exact failure before escalating.
- For canonical backlog access specifically, follow `.agents/skills/backlog-management/SKILL.md`, which pins the spreadsheet ID and tab name.

## Read-only repository inspection first
- In connector-based ChatGPT, use `repo_status`, `repo_diff`, `repo_log`, `repo_show`, `repo_search`, `repo_remote_ref`, `search`, `fetch`, `read_directory`, and `file_info` for routine repository/file inspection. These are published by Human MCP as read-only tools.
- Do **not** use `run_command` merely for `pwd`, `git status`, `git diff`, `git log`, `git show`, `git worktree list`, `git rev-parse`, `rg`/`grep`, `sed`, `cat`, or equivalent reads when the dedicated tools can answer the question. `run_command` is a general shell capability and may require user approval.
- Use `repo_remote_ref` when the question is only whether `origin/main` or another remote branch changed. It queries the remote without modifying local refs. Run `git fetch` only when the task genuinely needs local remote-tracking refs updated.
- Use `repo_search` for tracked source/code search and `search`/`fetch` for ordinary filesystem content, including potentially untracked files. Keep reads bounded to the relevant repo/path.
- If an already-open ChatGPT chat exposes `run_command` but not the `repo_*` read tools, treat that as a stale connector schema. Refresh/reconnect the authorised Human MCP tool catalogue rather than silently falling back to repeated shell approvals for routine reads.
- Local coding runtimes with native shell/filesystem access are not required to use these MCP read tools.

## Failure handling
- One failed tool/MCP call does **not** prove the connector or resource is unavailable.
- A rejected file patch (`old_text not found`, failed hunk, or equivalent) is a validation stop. Re-read the exact current file, construct a new context-checked patch, and verify the diff; never retry stale patch text.
- Inspect the actual error and retry with the smallest relevant read-only tool, or a smaller safer command only when command execution is genuinely required.
- For every Python command in this repository, use `uv run python ...`, `uv run pytest ...`, or another `uv run ...` command. Never invoke a bare `python`, `python3`, `pytest`, or `ruff`. `uv` creates/reuses this repo's own `.venv`; do not reach into another project's `.venv`.
- A large Sheets read can exceed a tool's output-token limit and get redirected to a file; when that happens, probe the saved file's structure (e.g. with `jq`) before extracting the specific rows needed rather than re-reading the whole payload into context.
- Keep command output bounded (`head`, focused queries, exact paths). Large output increases transport/decoding risk and burns context for no benefit.
- Do not replace live connected data with memory or a stale local copy after a connector error.
- Do not repeat a known-failing broad command unchanged.

## Connector inspection pattern
1. Confirm the expected repository with `repo_status` or the smallest relevant read-only filesystem tool.
2. Use `repo_search`/`search` to locate code and `repo_show`/`fetch` to read exact content; use `repo_diff`/`repo_log` for Git inspection.
3. Use `repo_remote_ref` before `git fetch` when only remote freshness is being checked.
4. Escalate to `run_command` only when a dedicated read-only tool cannot express the required operation or the task genuinely needs command execution.
5. Report the exact failing layer: connector, tool/command, path, encoding, permission, or application logic.

## Do not
- Do not say the repository, backlog, or Sheets access is unavailable without attempting the relevant connector/tool path first.
- Do not fall back to browser automation for backlog/Sheets work; that fallback belongs only to `.agents/skills/backlog-management/SKILL.md`'s documented escalation, and only when the human has authorised it.
