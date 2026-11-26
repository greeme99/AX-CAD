---
name: cost-engineering
description: >
  Audit, diagnose, and optimize token efficiency across both Antigravity (.agents/, GEMINI.md, agy MCP/plugins)
  and Claude Code (.claude/, CLAUDE.md) while keeping 100% coding quality.
  Enforces no excessive /compact, on-demand loading, static prompt cache prefixes, and approval gates.
  Explicit invocation only; never auto-run every turn.
---

# Cost Engineering Diagnosis & Cross-Tool Verification (v2.1)

## Non-Negotiable Principles
1. **No Excessive Compaction**: Never auto-run `/compact` or re-read all rules every turn. Suggest handoff/compact only when context reaches **70–80%**, a milestone finishes, or the user explicitly asks.
2. **On-Demand & Cache Prefix Stability**: Keep `GEMINI.md` and `CLAUDE.md` top prefixes static (no dates/issue numbers). Load skills, rules, and docs only when needed; do not create duplicate `AGENTS.md` alongside `GEMINI.md`.
3. **Approval Gate**: Always present diagnosis (D1–D9) and get explicit user approval before editing files or changing tool settings.
4. **Core 4 Skills + 100% Quality**: Maintain parity for `grill-me`, `ponytail`, `caveman`, `handoff` across `.agents/skills/` and `.claude/skills/`. Never weaken security `deny` rules or test/lint/typecheck gates.

## Workflow
1. **Phase 0 (Trigger)**: Run only on explicit request (`/cost-engineering`, token optimization, skill audit).
2. **Phase 1–2 (Read-Only Diagnosis & Cross-Check)**:
   - Inspect `GEMINI.md`, `.agents/{rules,skills}`, `CLAUDE.md`, `.claude/{settings.json,rules,hooks,skills,memory/MEMORY.md}`.
   - Check `agy mcp list` and `agy plugin list` for unrelated always-on tools/plugins.
   - Verify no broad rule globs (`**/*cost*/**`, `**/*cad*/**`) and no `@HARNESS.md`-style forced per-turn includes.
3. **Phase 3 (Proposal)**: Output D1–D9 table, checklist, preserved guardrails, and ask for approval (`All` / item numbers / edits).
4. **Phase 4 (Execute & Verify)**: Back up originals, apply approved minimal diffs, validate JSON & line counts, and report Before/After metrics. Leave git commits to the user.
