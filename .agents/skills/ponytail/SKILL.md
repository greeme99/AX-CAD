---
name: ponytail
description: Use when writing code or refactoring. Enforces the simplest, minimal working diff (YAGNI, standard libraries, native platform, no boilerplate).
---

# Ponytail — Minimal Working Diff Mode

Enforce the simplest possible solution:
1. **YAGNI**: Reject speculative features or abstractions.
2. **Native / Standard Lib**: Prefer built-in language/runtime features over extra libraries.
3. **Smallest Diff**: Only modify strictly necessary lines. No unrequested rewrites, interfaces, or factories.
4. **No Boilerplate**: Delete dead code rather than adding scaffolding for the future.
