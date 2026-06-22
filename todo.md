# J Claw TODO

Last updated: 2026-06-06

## Completed (Sprint 2)

- [x] **P0: Circular import** — `src/types.py` shadowed stdlib `types` module → renamed to
  `schema.py`, `types.py` is now a backward-compat shim. All 8 import sites updated.
- [x] **P0: Windows stdin hang** — `asyncio.create_subprocess_exec` with `stdin=PIPE` doesn't
  send EOF reliably on Windows. Fixed with temp-file IPC: JSON written to `tempfile.mkstemp()`,
  path passed as `argv[2]` to Node, `stdin=DEVNULL`.
- [x] **P0: SIGTERM false error** — Non-zero exit codes after streaming output now treated as
  success. `_SIGNAL_EXIT_CODES = frozenset({-15, -2, 130, 143, 1})`.
- [x] **P1: Hardcoded Python path** — `scripts/run-local-e2e.ps1` now discovers Python via
  `$env:JCLAW_PYTHON` → `python` → `python3` → throw.
- [x] **P1: Context guardrails** — `src/context_guard.py` — middle-out truncation, preserves
  start + end of prompt when over context limit (oh-my-pi pattern).
- [x] **P1: Web search fallback** — `needs_web_fallback()` detects `TOOLS_UNAVAILABLE` in
  agent output; retries with `host_browser.search_google_text()` result injected.
- [x] **P1: Hindsight memory** — `src/memory.py` — auto-appends dated episode blocks to
  `CLAUDE.md` after each agent run, bounded by `<!-- jclaw:memory:start/end -->` markers,
  rotated at 12K chars (oh-my-pi pattern).
- [x] **P1: Retry + backoff** — `run_container_agent()` wraps `_run_agent_once()` with
  `max_retries=2`, `backoff = min(2**attempt, 30)` seconds. Never retries after streaming
  output received (Hermes pattern).
- [x] **P2: Pytest test suite** — 58 tests passing, 2 skipped (cron skipped on Windows when
  TIMEZONE is a Windows name rather than IANA zone):
  - `tests/test_schema.py` — dataclasses, Channel protocol, shim identity
  - `tests/test_model_gateway.py` — ProviderRouter routing, health, EMA
  - `tests/test_container_runner.py` — IPC payload, snapshot helpers, SIGTERM set
  - `tests/test_context_guard.py` — truncation, web fallback detection, prompt builder
  - `tests/test_memory.py` — hindsight memory CRUD, rotation, marker insertion
  - `tests/test_task_scheduler.py` — `compute_next_run` interval/cron/once/daily/weekly

---

## P1 - Remaining reliability

- [ ] Harden [scripts/run-local-e2e.ps1](scripts/run-local-e2e.ps1) smoke assertion.
  - Add exact-match mode (not substring) for expected output.
  - Add `-ExpectedRegex` option for flexible CI assertions.

- [ ] Add a dedicated web-search E2E test mode.
  - New switch: `-TestWebSearch`.
  - Fails if output contains `TOOLS_UNAVAILABLE`.

- [ ] Separate main/worker model defaults for local path.
  - Current setup maps both aliases to one model.
  - Add profile options for fast worker + stronger main model.

- [ ] Fix TIMEZONE config — use IANA format (`Asia/Dhaka`) instead of Windows
  timezone name (`Bangladesh Standard Time`) so `zoneinfo.ZoneInfo` and cron work.
  See `src/config.py`.

## P2 - Skills validation

- [ ] Test all skills locally (see Skill Validation Matrix below).

### Skill Validation Matrix (Local)

#### Host Skills (.claude/skills)

- [ ] /add-compact
- [ ] /add-discord
- [ ] /add-gmail
- [ ] /add-image-vision
- [ ] /add-ollama-tool
- [ ] /add-parallel
- [ ] /add-pdf-reader
- [ ] /add-reactions
- [ ] /add-slack
- [ ] /add-telegram
- [ ] /add-telegram-swarm
- [ ] /add-voice-transcription
- [ ] /add-whatsapp
- [ ] /claw
- [ ] /convert-to-apple-container
- [ ] /customize
- [ ] /debug
- [ ] /get-qodo-rules
- [ ] /local-quality-gate
- [ ] /qodo-pr-resolver
- [ ] /setup
- [ ] /update-nanoclaw
- [ ] /update-skills
- [ ] /use-local-whisper
- [ ] /x-integration

#### Container Skills (container/skills)

- [ ] /agent-browser
- [ ] /capabilities
- [ ] /host-browser
- [ ] /repo-radar
- [ ] /slack-formatting
- [ ] /source-verify
- [ ] /status
- [ ] /web-search

## P3 - Environment and DX

- [ ] Install optional runtime packages: `croniter`, `playwright`.
  - Done when: `python -m src.main doctor` reports all optional packages present.

- [ ] Add troubleshooting section to README for local model + tool-call limitations.

## Verification checklist

- [x] `python -m src.main doctor` — passes after schema rename fix
- [x] `python -m pytest tests/ -v` — 58 passed, 2 skipped
- [ ] `python -m src.main port-audit --json`
- [ ] `python -m src.main agent-runner-parity`
- [ ] `powershell -ExecutionPolicy Bypass -File .\scripts\run-local-e2e.ps1 -StrictAssert`
