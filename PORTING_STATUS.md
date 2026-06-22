# J Claw Python Porting Status

This file tracks migration progress from TypeScript runtime modules to Python runtime modules.

## Migration Principles

- Keep behavior parity first; optimize after parity.
- Remove a TypeScript file only after a Python equivalent exists.
- Keep interfaces modular so providers/channels can evolve independently.

## Recently Ported

- src/config.ts -> src/config.py
- src/env.ts -> src/env.py
- src/logger.ts -> src/logger.py
- src/group-folder.ts -> src/group_folder.py
- src/db.ts -> src/db.py
- src/router.ts -> src/router.py
- src/model-gateway.ts -> src/model_gateway.py
- src/credential-proxy.ts -> src/credential_proxy.py
- src/sender-allowlist.ts -> src/sender_allowlist.py
- src/group-queue.ts -> src/group_queue.py
- src/task-scheduler.ts -> src/task_scheduler.py
- src/mount-security.ts -> src/mount_security.py
- src/container-runtime.ts -> src/container_runtime.py
- src/remote-control.ts -> src/remote_control.py
- src/ipc.ts -> src/ipc.py
- src/channels/registry.ts -> src/channels/registry.py
- src/channels/index.ts -> src/channels/index.py
- src/container-runner.ts -> src/container_runner.py
- src/host-browser.ts -> src/host_browser.py
- src/index.ts -> src/index.py

## Remaining TypeScript Runtime Surface

- None (core src runtime is now Python)

## TypeScript Outside Core Runtime

- container/agent-runner/src/index.ts
- container/agent-runner/src/ipc-mcp-stdio.ts

## Recently Ported (Skills)

- .claude/skills/x-integration/host.ts -> .claude/skills/x-integration/host.py
- .claude/skills/x-integration/lib/config.ts -> .claude/skills/x-integration/lib/config.py
- .claude/skills/x-integration/lib/browser.ts -> .claude/skills/x-integration/lib/browser.py
- .claude/skills/x-integration/scripts/_.ts -> .claude/skills/x-integration/scripts/_.py

## Python Tooling Added

- scripts/run_migrations.py (Python migration runner with TS fallback support)
- setup_py/index.py (Python setup step runner)
- setup_py/environment.py
- setup_py/container.py
- setup_py/groups.py
- setup_py/register.py
- setup_py/mounts.py
- setup_py/service.py
- setup_py/verify.py
- src/main.py setup-step command
- src/main.py profiles command
- src/channels/console.py (local console channel)

## Next Recommended Order

1. Decide whether container/agent-runner/src/\*.ts remains a separate TypeScript subcomponent or is also ported.
2. Decide whether container/agent-runner remains a Node sidecar or is ported to Python.
3. Add Python-native channel implementations and register them via `JCLAW_CHANNEL_MODULES`.

## Notes From Reference Projects

- openclaude pattern: isolate provider-specific behavior behind a narrow translation layer.
- claw-code pattern: maintain a clear parity/status manifest while transitioning languages.
- Applied in J Claw by introducing explicit Python runtime module boundaries and this status tracker.
