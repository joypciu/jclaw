---
name: x-integration
description: X (Twitter) integration for NanoClaw. Post tweets, like, reply, retweet, and quote. Use for setup, testing, or troubleshooting X functionality. Triggers on "setup x", "x integration", "twitter", "post tweet", "tweet".
---

# X (Twitter) Integration

Browser automation for X interactions via WhatsApp.

> **Compatibility:** NanoClaw v1.0.0. Directory structure may change in future versions.

## Features

| Action  | Tool        | Description              |
| ------- | ----------- | ------------------------ |
| Post    | `x_post`    | Publish new tweets       |
| Like    | `x_like`    | Like any tweet           |
| Reply   | `x_reply`   | Reply to tweets          |
| Retweet | `x_retweet` | Retweet without comment  |
| Quote   | `x_quote`   | Quote tweet with comment |

## Prerequisites

Before using this skill, ensure:

1. **NanoClaw is installed and running** - WhatsApp connected, service active
2. **Dependencies installed**:
   ```bash
   npm ls playwright dotenv-cli || npm install playwright dotenv-cli
   ```
3. **CHROME_PATH configured** in `.env` (if Chrome is not at default location):
   ```bash
   # Find your Chrome path
   mdfind "kMDItemCFBundleIdentifier == 'com.google.Chrome'" 2>/dev/null | head -1
   # Add to .env
   CHROME_PATH=/path/to/Google Chrome.app/Contents/MacOS/Google Chrome
   ```

## Quick Start

```bash
# 1. Setup authentication (interactive)
python .claude/skills/x-integration/scripts/setup.py
# Verify: data/x-auth.json should exist after successful login

# 2. Rebuild container
./container/build.sh
# Verify: build completes successfully

# 3. Rebuild host and restart service
npm run build
launchctl kickstart -k gui/$(id -u)/com.nanoclaw  # macOS
# Linux: systemctl --user restart nanoclaw
# Verify: launchctl list | grep nanoclaw (macOS) or systemctl --user status nanoclaw (Linux)
```

## Configuration

### Environment Variables

| Variable        | Default                                                        | Description                              |
| --------------- | -------------------------------------------------------------- | ---------------------------------------- |
| `CHROME_PATH`   | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` | Chrome executable path                   |
| `NANOCLAW_ROOT` | `process.cwd()`                                                | Project root directory                   |
| `LOG_LEVEL`     | `info`                                                         | Logging level (debug, info, warn, error) |

Set in `.env` file (loaded via `dotenv-cli` at runtime):

```bash
# .env
CHROME_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

### Configuration File

Edit `lib/config.py` to modify defaults:

```python
CONFIG = {
    // Browser viewport
    viewport: { width: 1280, height: 800 },

    // Timeouts (milliseconds)
    timeouts: {
        navigation: 30000,    // Page navigation
        elementWait: 5000,    // Wait for element
        afterClick: 1000,     // Delay after click
        afterFill: 1000,      // Delay after form fill
        afterSubmit: 3000,    // Delay after submit
        pageLoad: 3000,       // Initial page load
    },

    // Tweet limits
    limits: {
        tweetMaxLength: 280,
    },
};
```

### Data Directories

Paths relative to project root:

| Path                      | Purpose                                  | Git     |
| ------------------------- | ---------------------------------------- | ------- |
| `data/x-browser-profile/` | Chrome profile with X session            | Ignored |
| `data/x-auth.json`        | Auth state marker                        | Ignored |
| `logs/nanoclaw.log`       | Service logs (contains X operation logs) | Ignored |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
┌─────────────────────────────────────────────────────────────┐
│  Host (macOS)                                               │
│  └── src/ipc.py → process_task_ipc()                       │
│      └── host.py → handle_x_ipc()                          │
│          └── spawn subprocess → scripts/*.py               │
│              └── Playwright → Chrome → X Website           │
└─────────────────────────────────────────────────────────────┘
```

### Why This Design?

- **API is expensive** - X official API requires paid subscription ($100+/month) for posting
- **Bot browsers get blocked** - X detects and bans headless browsers and common automation fingerprints
- **Must use user's real browser** - Reuses the user's actual Chrome on Host with real browser fingerprint to avoid detection
- **One-time authorization** - User logs in manually once, session persists in Chrome profile for future use

### File Structure

```
.claude/skills/x-integration/
├── SKILL.md          # This documentation
├── host.py           # Host-side IPC handler
├── lib/
│   ├── config.py     # Centralized configuration
│   └── browser.py    # Playwright utilities
└── scripts/
    ├── setup.py      # Interactive login
    ├── post.py       # Post tweet
    ├── like.py       # Like tweet
    ├── reply.py      # Reply to tweet
    ├── retweet.py    # Retweet
    └── quote.py      # Quote tweet
```

### Integration Points

To integrate this skill into NanoClaw, make the following modifications:

---

**1. Host side: `src/ipc.py`**

`src/ipc.py` should dispatch unknown task types to `./.claude/skills/x-integration/host.py` via `handle_x_ipc`.

---

**2. Container side:** no extra MCP adapter file is required.

---

**3. Build script: `container/build.sh`**

Change build context from `container/` to project root (required to access `.claude/skills/`):

```bash
# Find:
docker build -t "${IMAGE_NAME}:${TAG}" .

# Replace with:
cd "$SCRIPT_DIR/.."
docker build -t "${IMAGE_NAME}:${TAG}" -f container/Dockerfile .
```

---

**4. Dockerfile: `container/Dockerfile`**

First, update the build context paths (required to access `.claude/skills/` from project root):

```dockerfile
# Find:
COPY agent-runner/package*.json ./
...
COPY agent-runner/ ./

# Replace with:
COPY container/agent-runner/package*.json ./
...
COPY container/agent-runner/ ./
```

No extra skill COPY line is required for X integration.

## Setup

All paths below are relative to project root (`NANOCLAW_ROOT`).

### 1. Check Chrome Path

```bash
# Check if Chrome exists at configured path
cat .env | grep CHROME_PATH
ls -la "$(grep CHROME_PATH .env | cut -d= -f2)" 2>/dev/null || \
echo "Chrome not found - update CHROME_PATH in .env"
```

### 2. Run Authentication

```bash
python .claude/skills/x-integration/scripts/setup.py
```

This opens Chrome for manual X login. Session saved to `data/x-browser-profile/`.

**Verify success:**

```bash
cat data/x-auth.json  # Should show {"authenticated": true, ...}
```

### 3. Rebuild Container

```bash
./container/build.sh
```

**Verify success:**

```bash
./container/build.sh 2>&1 | grep -i "error" || echo "Build OK"
```

### 4. Restart Service

```bash
npm run build
launchctl kickstart -k gui/$(id -u)/com.nanoclaw  # macOS
# Linux: systemctl --user restart nanoclaw
```

**Verify success:**

```bash
launchctl list | grep nanoclaw  # macOS — should show PID and exit code 0 or -
# Linux: systemctl --user status nanoclaw
```

## Usage via WhatsApp

Replace `@Assistant` with your configured trigger name (`ASSISTANT_NAME` in `.env`):

```
@Assistant post a tweet: Hello world!

@Assistant like this tweet https://x.com/user/status/123

@Assistant reply to https://x.com/user/status/123 with: Great post!

@Assistant retweet https://x.com/user/status/123

@Assistant quote https://x.com/user/status/123 with comment: Interesting
```

**Note:** Only the main group can use X tools. Other groups will receive an error.

## Testing

Scripts read environment variables from `.env` and your shell environment.

### Check Authentication Status

```bash
# Check if auth file exists and is valid
cat data/x-auth.json 2>/dev/null && echo "Auth configured" || echo "Auth not configured"

# Check if browser profile exists
ls -la data/x-browser-profile/ 2>/dev/null | head -5
```

### Re-authenticate (if expired)

```bash
python .claude/skills/x-integration/scripts/setup.py
```

### Test Post (will actually post)

```bash
echo '{"content":"Test tweet - please ignore"}' | python .claude/skills/x-integration/scripts/post.py
```

### Test Like

```bash
echo '{"tweetUrl":"https://x.com/user/status/123"}' | python .claude/skills/x-integration/scripts/like.py
```

Or export `CHROME_PATH` manually before running:

```bash
export CHROME_PATH="/path/to/chrome"
echo '{"content":"Test"}' | python .claude/skills/x-integration/scripts/post.py
```

## Troubleshooting

### Authentication Expired

```bash
python .claude/skills/x-integration/scripts/setup.py
launchctl kickstart -k gui/$(id -u)/com.nanoclaw  # macOS
# Linux: systemctl --user restart nanoclaw
```

### Browser Lock Files

If Chrome fails to launch:

```bash
rm -f data/x-browser-profile/SingletonLock
rm -f data/x-browser-profile/SingletonSocket
rm -f data/x-browser-profile/SingletonCookie
```

### Check Logs

```bash
# Host logs (relative to project root)
grep -i "x_post\|x_like\|x_reply\|handle_x_ipc" logs/nanoclaw.log | tail -20

# Script errors
grep -i "error\|failed" logs/nanoclaw.log | tail -20
```

### Script Timeout

Default timeout is 2 minutes (120s). Increase in `host.py` inside `_run_script` (`timeout=120`).

### X UI Selector Changes

If X updates their UI, selectors in scripts may break. Current selectors:

| Element         | Selector                             |
| --------------- | ------------------------------------ |
| Tweet input     | `[data-testid="tweetTextarea_0"]`    |
| Post button     | `[data-testid="tweetButtonInline"]`  |
| Reply button    | `[data-testid="reply"]`              |
| Like            | `[data-testid="like"]`               |
| Unlike          | `[data-testid="unlike"]`             |
| Retweet         | `[data-testid="retweet"]`            |
| Unretweet       | `[data-testid="unretweet"]`          |
| Confirm retweet | `[data-testid="retweetConfirm"]`     |
| Modal dialog    | `[role="dialog"][aria-modal="true"]` |
| Modal submit    | `[data-testid="tweetButton"]`        |

### Container Build Issues

If MCP tools not found in container:

```bash
# Verify host IPC handler exists
ls -la .claude/skills/x-integration/host.py
```

## Security

- `data/x-browser-profile/` - Contains X session cookies (in `.gitignore`)
- `data/x-auth.json` - Auth state marker (in `.gitignore`)
- Only main group can use X tools (enforced in `host.py`)
- Scripts run as subprocesses with limited environment
