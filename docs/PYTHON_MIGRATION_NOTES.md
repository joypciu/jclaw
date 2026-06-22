# Python Migration Notes

This document captures practical migration patterns inspired by reference repos and applied to J Claw.

## Reference-Informed Patterns

## openclaude-inspired

- Keep provider routing isolated behind a narrow interface.
- Use compatibility boundaries so upstream orchestration code stays mostly unchanged.
- Treat web and provider integrations as optional, pluggable capabilities.

Applied in J Claw:

- src/model_gateway.py isolates provider selection strategy.
- src/credential_proxy.py injects auth without exposing host secrets.
- src/task_scheduler.py and src/ipc.py are structured around dependency injection protocols.

## claw-code-inspired

- Maintain explicit migration/parity tracking while porting.
- Prefer a Python-first executable workspace and measurable progress commands.
- Keep architecture boundaries clear while replacing modules incrementally.

Applied in J Claw:

- PORTING_STATUS.md tracks migrated modules and remaining surfaces.
- src/main.py includes port-audit command for repeatable migration checks.
- src/main.py port-audit now reports categorized TS islands (agent_runner, skills, other), with optional JSON output.
- src/main.py now includes doctor and run commands for easier setup and operation.
- src/main.py now supports profiles and setup-step commands.
- setup_py now includes environment/container/groups/register/mounts/service/verify steps.
- Runtime modules have been split into focused Python files (queue, scheduler, ipc, container runtime, mount security).
- Host browser IPC actions now run from src/host_browser.py.
- The orchestrator runtime is now implemented in src/index.py.
- A built-in console channel is available for local testing (enable with JCLAW_ENABLE_CONSOLE_CHANNEL=1).
- X integration host runtime and scripts are now Python (`host.py`, `lib/*.py`, `scripts/*.py`), and TypeScript skill files were removed.

## Migration Checklist (Current)

1. Port container/agent-runner/src/\*.ts if complete TS elimination is required. This is currently coupled to the Node Claude Agent SDK + MCP stdio stack.
2. Decide whether `container/agent-runner/src/*.ts` should remain a Node sidecar or be ported alongside SDK/tooling changes.
3. Add Python-native channel modules and register through JCLAW_CHANNEL_MODULES.

## Validation Command

Use:

python -m src.main port-audit

or for machine-readable output:

python -m src.main port-audit --json

This prints remaining TypeScript files and highlights core runtime TS still left in src.

For environment checks, use:

python -m src.main doctor
