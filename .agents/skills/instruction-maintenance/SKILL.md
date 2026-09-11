---
name: instruction-maintenance
description: "Use ONLY when editing agent instruction files: AGENTS.md, CLAUDE.md, .agents/skills, or docs that define agent workflow. Do NOT use for product/code changes."
---

# Skill: Instruction Maintenance

Use when editing `AGENTS.md`, `CLAUDE.md`, `.agents/skills/*/SKILL.md`, `docs/INDEX.md`, or other markdown that guides agents rather than describes the product.

## Purpose
Keep agent instructions useful, small, current, and non-contradictory.

## Source hierarchy
- `AGENTS.md`: project-wide rules all agents should read first. Routing plus only genuinely universal, stable rules.
- `CLAUDE.md`: a thin Claude Code adapter that imports `AGENTS.md` and `.agents/skills/INDEX.md`. Do not duplicate rules there.
- `.agents/skills/*/SKILL.md`: compact domain rules loaded only for that work area.
- `docs/*`: human/reference documentation and product architecture, not agent operating rules unless explicitly linked from a skill.
- Google Sheet backlog: planning/tracking only, not instructions.

## Cleanup rules
- Prefer deleting or moving noise over adding more instructions.
- Keep `SKILL.md` files concise. If a skill grows large enough to need a `DETAILS.md` split, create one rather than letting `SKILL.md` sprawl.
- Remove stale architecture claims when verified wrong (check against current code, not memory).
- Do not edit `AGENTS.md`/`CLAUDE.md` to redefine rules owned by a `.agents/skills/*/SKILL.md`; point back to the owning file instead.
- Avoid duplicating the same rule across skills.
- Preserve important project constraints: neutral/personal ownership boundary, `identity_key` as the stable cross-service reference, Google Sheet backlog as source of truth, and settings-not-constants for admin-tunable behaviour.
- If unsure whether information is stale, mark it for review instead of rewriting as fact.

## Cross-agent portability
- Shared `AGENTS.md` and `.agents/skills/*` rules must be runtime-neutral: describe required capability/behaviour first, not a specific agent product or tool namespace.
- Runtime-specific connector names, local paths, or tool names are allowed only in the owning tooling skill (`mcp-tooling`) and must be scoped to the runtime where they actually exist.
- A local coding agent already running in the repository may use its direct filesystem/shell; a connector-based runtime should use its authorised connector and documented fallback. Neither should be told to invoke a tool that runtime does not expose.
- Examples are explanatory only. Do not let an example threshold, count, or observed value become an implementation rule.

## Audit checklist
When cleaning instructions, check:
- Does this rule still match current architecture (verify against the code, not the skill's own prior wording)?
- Is it actionable for an agent?
- Is it in the right file according to the source hierarchy?
- Is it duplicated elsewhere?
- Too verbose for a `SKILL.md`?
- Does it conflict with `AGENTS.md`?
- Does it accidentally encourage hardcoding, fallbacks, or a second identity/dedupe rule?

## Safe edit pattern
1. Inspect current files first.
2. Make small targeted edits.
3. Preserve long useful content by moving it, not deleting it outright.
4. Report exactly what changed and what was left alone.

## Repeated mistake protocol
- If the same agent/tooling mistake happens more than once and the correction is known, update the owning skill in the same work session instead of relying on conversational memory.
- Put the rule at the narrowest correct scope: universal behaviour in `AGENTS.md`, tooling failures in `mcp-tooling`, backlog mechanics in `backlog-management`, collection/API/ownership rules in `job-market-map`.
- Record the cause and recovery rule, not the incident narrative.
- Do not create duplicate rules in several skills. Link or route to the single owner.

## Ongoing maintenance
- When a durable rule should apply across future sessions, first put it in the existing skill that owns that behaviour.
- If no suitable skill exists, create a focused skill and add it to `.agents/skills/INDEX.md`; do not use `AGENTS.md` as the fallback dumping ground.
- If a rule needs examples or long explanation, move those details to `docs/*` and link to it from the skill.
- When new instructions are added, check whether they made the wrong file bigger or duplicated an existing owner.
- When a lesson learned while working in Job Hunter is genuinely reusable here (a governance pattern, a tooling failure mode, a documentation practice), port it into the matching JMM skill — even as a short placeholder — rather than leaving it only in the other repo. Adapt facts (paths, IDs, tool names) to what is actually true for Job Market Map; never copy a claim about a script, table, or connector that does not exist here.

## Do not
- Do not rewrite all instructions in one pass.
- Do not add broad inspirational guidance.
- Do not turn backlog rows into operating rules.
- Do not reference tooling, scripts, or files from another repository as if they exist in this one.
