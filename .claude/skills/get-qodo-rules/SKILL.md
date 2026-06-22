---
name: get-qodo-rules
description: Deprecated compatibility stub. Qodo integration was removed; use /local-quality-gate instead.
---

# Deprecated: get-qodo-rules

This skill no longer fetches external rules.

Use `/local-quality-gate` for local, deterministic checks:

- `python -m src.main doctor`
- `python -m src.main port-audit`
- `python -m src.main setup-step environment`
- `python -m src.main setup-step verify`
