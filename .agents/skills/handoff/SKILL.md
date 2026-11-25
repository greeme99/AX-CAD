---
name: handoff
description: >
  Use when a milestone finishes, 3 consecutive errors occur, context reaches 70-80%, or the user explicitly requests handoff.
  Summarizes critical state into docs/TASKS.md and docs/HANDOFF.md without triggering premature /compact.
---

# Session Handoff Protocol

## Trigger Conditions (No Excessive Compaction)
- Run ONLY on: (1) milestone/task completion, (2) actual context reaching **70–80%**, (3) 3 consecutive identical errors, or (4) explicit user request.
- NEVER auto-trigger `/compact` every turn or early in a session.

## Steps
1. **Sync State**: Update `docs/TASKS.md` with completed items and next actionable steps.
2. **Context Summary**: Record minimal essential decisions, changed files, and verification status in `docs/HANDOFF.md`.
3. **Transition Proposal**: Suggest starting a clean session or running `/compact` only when context pressure warrants it.
