---
name: mcp-tooling
description: Use for repository/filesystem access, connected Google Sheets tooling, or tool/transport failure recovery; apply runtime-specific connector names only when that runtime exposes them.
---

# Skill: MCP Tooling

Use for project filesystem/tool access and whenever a connector call is unreliable.

## Source of truth
- Job Market Map repo: `/home/robvoto/projects/job-market-map`.
- A local coding runtime that already has direct repository shell/filesystem access (e.g. Claude Code) should use that native access instead of a connector. The same safety rules still apply.
- In a connector-based runtime without direct repo access, use the authorised Sheets/repo connector documented for that runtime (for example, the `rob-human` MCP server's `sheets_read_rows` / `sheets_update_cell` tools, or an equivalent). Do not silently switch connectors mid-task; if the primary path fails, report the exact failure before escalating.
- For canonical backlog access specifically, follow `.agents/skills/backlog-management/SKILL.md`, which pins the spreadsheet ID and tab name.

## Failure handling
- One failed tool/MCP call does **not** prove the connector or resource is unavailable.
- Inspect the actual error and retry with a smaller, safer command before concluding a path is broken.
- For every Python command in this repository, use `uv run python ...`, `uv run pytest ...`, or another `uv run ...` command. Never invoke a bare `python`, `python3`, `pytest`, or `ruff`. `uv` creates/reuses this repo's own `.venv`; do not reach into another project's `.venv`.
- A large Sheets read can exceed a tool's output-token limit and get redirected to a file; when that happens, probe the saved file's structure (e.g. with `jq`) before extracting the specific rows needed rather than re-reading the whole payload into context.
- Keep command output bounded (`head`, focused queries, exact paths). Large output increases transport/decoding risk and burns context for no benefit.
- Do not replace live connected data with memory or a stale local copy after a connector error.
- Do not repeat a known-failing broad command unchanged.

## Safe command pattern
1. Start with a tiny command such as `pwd`, `git status --short`, or a focused query.
2. Confirm the expected repo/path.
3. Run the smallest command that answers the question.
4. If output may contain arbitrary Unicode, make it ASCII-safe or escaped.
5. Report the exact failing layer: connector, command, path, encoding, permission, or application logic.

## Do not
- Do not say the repository, backlog, or Sheets access is unavailable without attempting the relevant connector/tool path first.
- Do not fall back to browser automation for backlog/Sheets work; that fallback belongs only to `.agents/skills/backlog-management/SKILL.md`'s documented escalation, and only when the human has authorised it.
