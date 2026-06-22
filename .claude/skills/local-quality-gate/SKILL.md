---
name: local-quality-gate
description: Fast local quality gate for J Claw without git or external services. Use before/after code changes to catch runtime, migration, and diagnostics regressions.
---

# Local Quality Gate

Use this skill when implementing, refactoring, or debugging code in this workspace.

This replaces external rules services with a deterministic local gate that works even when the project has no git metadata.

## Checks

Run these checks in order:

1. Runtime health

```bash
python -m src.main doctor
```

2. Migration status (human-readable)

```bash
python -m src.main port-audit
```

3. Migration status (machine-readable)

```bash
python -m src.main port-audit --json
```

4. Setup flow sanity

```bash
python -m src.main setup-step environment
python -m src.main setup-step verify
```

5. File diagnostics

- Use editor diagnostics and fix all errors in touched files.

## Decision policy

- Blocking: syntax/type/import errors in touched files.
- Blocking: command failures caused by the current change.
- Non-blocking: missing local credentials, missing Docker runtime, missing optional dependencies unless the current change introduced them.

## Output format

Return:

1. Findings
2. Fixed issues
3. Remaining non-blocking risks
4. Next recommended step

## Notes

- Prefer workspace-local facts over assumptions.
- Keep fixes minimal and targeted.
- If a check fails unexpectedly, inspect and repair immediately instead of deferring.
