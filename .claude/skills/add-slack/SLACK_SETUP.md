# Slack App Setup Reference

Use this guide when configuring Slack for NanoClaw.

## Required tokens

1. `SLACK_BOT_TOKEN` (starts with `xoxb-`)
2. `SLACK_APP_TOKEN` (starts with `xapp-`)
3. Optional for verification: `SLACK_SIGNING_SECRET`

## Create app

1. Go to https://api.slack.com/apps
2. Click Create New App
3. Choose From scratch
4. Name it and pick your workspace

## Enable Socket Mode

1. Open Socket Mode in app settings
2. Enable Socket Mode
3. Generate App-Level Token
4. Add `connections:write` scope
5. Save the `xapp-...` token as `SLACK_APP_TOKEN`

## Bot token scopes

In OAuth & Permissions, add these bot scopes:

- `app_mentions:read`
- `channels:history`
- `channels:read`
- `chat:write`
- `groups:history`
- `groups:read`
- `im:history`
- `im:read`
- `im:write`
- `mpim:history`
- `users:read`

Install or reinstall the app after changing scopes.

## Event subscriptions

1. Open Event Subscriptions
2. Enable events
3. Subscribe to bot events:

- `app_mention`
- `message.channels`
- `message.groups`
- `message.im`

## Invite bot to channel

In Slack, run:

`/invite @<your-bot-name>`

## Quick verification

1. Set `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` in `.env`
2. Sync env: `mkdir -p data/env && cp .env data/env/env`
3. Build: `npm run build`
4. Restart service
5. Send a message in registered channel and check logs

## Common issues

- `missing_scope`: add the scope shown in logs, reinstall app
- Bot not receiving messages: ensure bot is invited to that channel
- Socket mode disconnected: verify `SLACK_APP_TOKEN` and `connections:write`
