---
name: host-browser
description: Use the host machine's installed Chrome, Firefox, or Opera for Google search, scraping, downloads, and web apps like WhatsApp Web, Gmail, and Telegram Web. Prefer this when the real local browser matters.
---

# Host Browser

Use this when the container browser is not enough and you need the user's locally installed browser, saved logins, or a site that behaves differently outside the container.

## Good fits

- Google search and result scraping in the user's real browser
- Web apps that rely on saved sessions, cookies, or anti-bot checks
- WhatsApp Web, Telegram Web, Gmail, Outlook Web, and similar services
- Downloading files into the active group workspace

## Core workflow

1. Open a page with `mcp__nanoclaw__host_browser_open` or `mcp__nanoclaw__host_browser_search_google`
2. Inspect clickable inputs and buttons with `mcp__nanoclaw__host_browser_snapshot`
3. Interact using refs like `@e1` via `mcp__nanoclaw__host_browser_click` and `mcp__nanoclaw__host_browser_fill`
4. Read page contents with `mcp__nanoclaw__host_browser_read_text`
5. Close the session with `mcp__nanoclaw__host_browser_close` when finished

## Session naming

Use stable session names so cookies and tabs stay separate:

- `research`
- `gmail`
- `telegram`
- `whatsapp`

## Examples

- Search Google for a topic: call `mcp__nanoclaw__host_browser_search_google` with session `research`
- Scrape results: call `mcp__nanoclaw__host_browser_snapshot`, then click a result, then `mcp__nanoclaw__host_browser_read_text`
- Use Gmail in a logged-in browser: open `https://mail.google.com` with session `gmail`
- Use WhatsApp Web: open `https://web.whatsapp.com` with session `whatsapp`
- Download a file: call `mcp__nanoclaw__download_from_web`

## Performance rules

- Reuse an existing named session instead of opening a new browser each time
- Close sessions you no longer need
- Prefer direct downloads for files instead of keeping a browser open just to fetch a URL