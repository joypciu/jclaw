"""FastAPI web dashboard for J Claw.

Provides a browser UI for monitoring and managing the J Claw runtime:
  - live log stream via Server-Sent Events
  - group list and registration
  - task list
  - message history
  - model backend health
  - feature flags overview

Start with:
  python -m src.main ui              # port 7842
  python -m src.main ui --port 8080  # custom port

The UI is self-contained: all HTML/CSS/JS is inlined so there are no static
file build steps.  It is intentionally compact — the target user is the jclaw
operator, not end users of the assistant.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)

from .db import (
    get_all_registered_groups,
    get_all_tasks,
    get_messages_since,
    init_database,
)
from .feature_flags import ALL_FLAGS, active_flags
from .hooks import on as hook_on
from .logger import logger
from .model_registry import load_model_registry

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="J Claw Dashboard", version="1.0", docs_url=None, redoc_url=None)

# ── Live log broadcast (SSE) ──────────────────────────────────────────────────

_log_subscribers: list[asyncio.Queue] = []


def _broadcast(event: str, data: dict) -> None:
    payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    for q in list(_log_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


# Wire hook events into the broadcast system.
# These run in the orchestrator's event loop and push SSE to browsers.
@hook_on("agent_output")
def _on_agent_output(p: dict) -> None:
    _broadcast("agent_output", p)


@hook_on("before_agent_run")
def _on_before_agent(p: dict) -> None:
    _broadcast("agent_run", {**p, "phase": "start"})


@hook_on("after_agent_run")
def _on_after_agent(p: dict) -> None:
    _broadcast("agent_run", {**p, "phase": "end"})


@hook_on("channel_connected")
def _on_channel_up(p: dict) -> None:
    _broadcast("channel", {**p, "state": "connected"})


@hook_on("channel_disconnected")
def _on_channel_down(p: dict) -> None:
    _broadcast("channel", {**p, "state": "disconnected"})


@hook_on("startup_complete")
def _on_startup(p: dict) -> None:
    _broadcast("startup", p)


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/groups")
def api_groups() -> JSONResponse:
    groups = get_all_registered_groups()
    return JSONResponse([
        {"name": g.name, "folder": g.folder, "is_main": bool(g.is_main), "jid": jid}
        for jid, g in groups.items()
    ])


@app.get("/api/tasks")
def api_tasks() -> JSONResponse:
    tasks = get_all_tasks()
    return JSONResponse([
        {
            "id": t.id,
            "group_folder": t.group_folder,
            "prompt": t.prompt[:120] + ("…" if len(t.prompt) > 120 else ""),
            "schedule_type": t.schedule_type,
            "schedule_value": t.schedule_value,
            "status": t.status,
            "next_run": t.next_run,
        }
        for t in tasks
    ])


@app.get("/api/messages/{chat_jid}")
def api_messages(chat_jid: str, since: str = "") -> JSONResponse:
    msgs = get_messages_since(chat_jid, since, "", limit=50)
    return JSONResponse([
        {
            "timestamp": m.timestamp,
            "sender": m.sender,
            "content": m.content[:500] + ("…" if len(m.content) > 500 else ""),
            "is_from_me": m.is_from_me,
        }
        for m in msgs[-50:]
    ])


@app.get("/api/providers")
def api_providers() -> JSONResponse:
    import httpx
    registry = load_model_registry()
    result = []
    for alias in sorted(registry.all_aliases()):
        ep = registry.resolve(alias)
        if not ep:
            continue
        try:
            r = httpx.get(f"{ep.url}/models", timeout=2.0,
                          headers={"Authorization": f"Bearer {ep.api_key}"} if ep.api_key else {})
            status = r.status_code
            ok = status < 400
        except Exception as exc:
            status = 0
            ok = False
        result.append({
            "alias": alias, "url": ep.url, "model": ep.model,
            "http_status": status, "reachable": ok,
        })
    return JSONResponse(result)


@app.get("/api/features")
def api_features() -> JSONResponse:
    active = active_flags()
    return JSONResponse({
        "active": sorted(active),
        "disabled": sorted(ALL_FLAGS - active),
        "all": sorted(ALL_FLAGS),
    })


@app.get("/api/health")
def api_health() -> JSONResponse:
    return JSONResponse({"status": "ok", "time": int(time.time())})


@app.get("/api/events")
async def api_events(request: Request) -> StreamingResponse:
    """SSE stream that pushes live hook events to the browser."""
    q: asyncio.Queue = asyncio.Queue(maxsize=512)
    _log_subscribers.append(q)

    async def _gen() -> AsyncGenerator[str, None]:
        try:
            # Send a keepalive comment every 15 s so proxies don't close the connection.
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield data
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            try:
                _log_subscribers.remove(q)
            except ValueError:
                pass

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ── Dashboard HTML ────────────────────────────────────────────────────────────

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>J Claw Dashboard</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg:#0f1117;--surface:#1a1d27;--border:#2a2d3a;--accent:#4f9cf9;
    --green:#22d35e;--red:#f95f5f;--yellow:#f9c84f;--text:#e4e6ef;--dim:#6b7280;
    --font:'SF Mono','Fira Code',Consolas,monospace;
  }
  body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:13px;line-height:1.5}
  a{color:var(--accent);text-decoration:none}
  header{background:var(--surface);border-bottom:1px solid var(--border);padding:12px 20px;
    display:flex;align-items:center;gap:12px}
  header h1{font-size:16px;font-weight:600;color:var(--accent)}
  header .pill{background:var(--border);border-radius:999px;padding:2px 10px;font-size:11px;color:var(--dim)}
  #status-dot{width:8px;height:8px;border-radius:50%;background:var(--dim);margin-left:auto}
  #status-dot.ok{background:var(--green)}
  main{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:auto auto;gap:16px;padding:16px}
  @media(max-width:720px){main{grid-template-columns:1fr}}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden}
  .card-header{padding:10px 16px;border-bottom:1px solid var(--border);font-weight:600;
    display:flex;justify-content:space-between;align-items:center;font-size:12px;
    text-transform:uppercase;letter-spacing:.05em;color:var(--dim)}
  .card-header span{color:var(--text)}
  .card-body{padding:0;overflow:auto;max-height:320px}
  table{width:100%;border-collapse:collapse}
  th,td{padding:8px 16px;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap}
  th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim);font-weight:500}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:rgba(255,255,255,.03)}
  .badge{display:inline-block;border-radius:4px;padding:1px 7px;font-size:11px}
  .badge.ok{background:rgba(34,211,94,.15);color:var(--green)}
  .badge.err{background:rgba(249,95,95,.15);color:var(--red)}
  .badge.warn{background:rgba(249,200,79,.15);color:var(--yellow)}
  .badge.dim{background:rgba(107,114,128,.15);color:var(--dim)}
  #log{grid-column:1/-1}
  #log .card-body{max-height:260px;min-height:100px;padding:12px 16px;
    display:flex;flex-direction:column-reverse;gap:4px}
  .log-entry{font-size:12px;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.03)}
  .log-entry .ts{color:var(--dim);margin-right:8px}
  .log-entry .ev{margin-right:8px;font-weight:600}
  .log-entry.agent_output .ev{color:var(--accent)}
  .log-entry.agent_run .ev{color:var(--green)}
  .log-entry.channel .ev{color:var(--yellow)}
  .empty{color:var(--dim);font-size:12px;padding:20px 16px;text-align:center}
  .refresh-btn{background:none;border:1px solid var(--border);color:var(--dim);
    cursor:pointer;border-radius:4px;padding:2px 8px;font-size:11px;font-family:inherit}
  .refresh-btn:hover{border-color:var(--accent);color:var(--accent)}
  #providers-body td:nth-child(4){text-align:center}
</style>
</head>
<body>
<header>
  <h1>⚡ J Claw</h1>
  <span class="pill">Dashboard</span>
  <div id="status-dot" title="SSE stream status"></div>
</header>
<main>

  <!-- Groups -->
  <div class="card">
    <div class="card-header"><span>Groups</span>
      <button class="refresh-btn" onclick="loadGroups()">↻</button></div>
    <div class="card-body">
      <table><thead><tr><th>Name</th><th>Folder</th><th>Main</th></tr></thead>
      <tbody id="groups-body"><tr><td colspan="3" class="empty">Loading…</td></tr></tbody></table>
    </div>
  </div>

  <!-- Providers -->
  <div class="card">
    <div class="card-header"><span>Providers</span>
      <button class="refresh-btn" onclick="loadProviders()">↻ Test</button></div>
    <div class="card-body" id="providers-body">
      <table><thead><tr><th>Alias</th><th>Model</th><th>URL</th><th>Status</th></tr></thead>
      <tbody id="providers-tbody"><tr><td colspan="4" class="empty">Loading…</td></tr></tbody></table>
    </div>
  </div>

  <!-- Tasks -->
  <div class="card">
    <div class="card-header"><span>Scheduled Tasks</span>
      <button class="refresh-btn" onclick="loadTasks()">↻</button></div>
    <div class="card-body">
      <table><thead><tr><th>Group</th><th>Prompt</th><th>Schedule</th><th>Status</th><th>Next run</th></tr></thead>
      <tbody id="tasks-body"><tr><td colspan="5" class="empty">Loading…</td></tr></tbody></table>
    </div>
  </div>

  <!-- Features -->
  <div class="card">
    <div class="card-header"><span>Feature Flags</span></div>
    <div class="card-body">
      <table><thead><tr><th>Flag</th><th>State</th></tr></thead>
      <tbody id="features-body"><tr><td colspan="2" class="empty">Loading…</td></tr></tbody></table>
    </div>
  </div>

  <!-- Live event log -->
  <div class="card" id="log">
    <div class="card-header"><span>Live Events</span>
      <button class="refresh-btn" onclick="clearLog()">✕ Clear</button></div>
    <div class="card-body" id="log-body">
      <div class="empty">Connecting to event stream…</div>
    </div>
  </div>

</main>
<script>
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

async function apiFetch(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

// ── Groups ───────────────────────────────────────────────────────────────────
async function loadGroups() {
  try {
    const data = await apiFetch('/api/groups');
    const tbody = $('#groups-body');
    if (!data.length) { tbody.innerHTML = '<tr><td colspan="3" class="empty">No groups registered</td></tr>'; return; }
    tbody.innerHTML = data.map(g => `
      <tr>
        <td>${esc(g.name)}</td>
        <td><code>${esc(g.folder)}</code></td>
        <td>${g.is_main ? '<span class="badge ok">main</span>' : ''}</td>
      </tr>`).join('');
  } catch(e) { $('#groups-body').innerHTML = `<tr><td colspan="3" class="empty">Error: ${e.message}</td></tr>`; }
}

// ── Providers ─────────────────────────────────────────────────────────────────
async function loadProviders() {
  const tbody = $('#providers-tbody');
  tbody.innerHTML = '<tr><td colspan="4" class="empty">Testing…</td></tr>';
  try {
    const data = await apiFetch('/api/providers');
    if (!data.length) { tbody.innerHTML = '<tr><td colspan="4" class="empty">No aliases configured</td></tr>'; return; }
    tbody.innerHTML = data.map(p => `
      <tr>
        <td><strong>${esc(p.alias)}</strong></td>
        <td>${esc(p.model)}</td>
        <td><a href="${esc(p.url)}" target="_blank">${esc(p.url)}</a></td>
        <td>${p.reachable
          ? `<span class="badge ok">HTTP ${p.http_status}</span>`
          : `<span class="badge err">${p.http_status ? 'HTTP '+p.http_status : 'unreachable'}</span>`
        }</td>
      </tr>`).join('');
  } catch(e) { tbody.innerHTML = `<tr><td colspan="4" class="empty">Error: ${e.message}</td></tr>`; }
}

// ── Tasks ─────────────────────────────────────────────────────────────────────
async function loadTasks() {
  try {
    const data = await apiFetch('/api/tasks');
    const tbody = $('#tasks-body');
    if (!data.length) { tbody.innerHTML = '<tr><td colspan="5" class="empty">No scheduled tasks</td></tr>'; return; }
    tbody.innerHTML = data.map(t => `
      <tr>
        <td>${esc(t.group_folder)}</td>
        <td title="${esc(t.prompt)}">${esc(t.prompt.substring(0,40))}${t.prompt.length>40?'…':''}</td>
        <td>${esc(t.schedule_type || '')} <code>${esc(t.schedule_value || '')}</code></td>
        <td>${badgeStatus(t.status)}</td>
        <td>${esc(t.next_run || '—')}</td>
      </tr>`).join('');
  } catch(e) { $('#tasks-body').innerHTML = `<tr><td colspan="5" class="empty">Error: ${e.message}</td></tr>`; }
}

// ── Feature flags ─────────────────────────────────────────────────────────────
async function loadFeatures() {
  try {
    const data = await apiFetch('/api/features');
    const tbody = $('#features-body');
    const all = data.all || [];
    tbody.innerHTML = all.map(f => `
      <tr>
        <td><code>${esc(f)}</code></td>
        <td>${data.active.includes(f)
          ? '<span class="badge ok">on</span>'
          : '<span class="badge dim">off</span>'
        }</td>
      </tr>`).join('');
  } catch(e) { $('#features-body').innerHTML = `<tr><td colspan="2" class="empty">Error: ${e.message}</td></tr>`; }
}

// ── Live event log ─────────────────────────────────────────────────────────────
const MAX_LOG = 120;
let logEntries = [];
function appendLog(type, data) {
  const d = new Date();
  const ts = d.toTimeString().substring(0,8);
  logEntries.unshift({ ts, type, data });
  if (logEntries.length > MAX_LOG) logEntries.length = MAX_LOG;
  renderLog();
}
function renderLog() {
  const el = $('#log-body');
  if (!logEntries.length) { el.innerHTML = '<div class="empty">No events yet</div>'; return; }
  el.innerHTML = logEntries.map(e => {
    let detail = '';
    if (e.type === 'agent_output') detail = `[${esc(e.data.group_name||'')}] ${esc((e.data.text||'').substring(0,120))}`;
    else if (e.type === 'agent_run') detail = `[${esc(e.data.group_name||'')}] ${esc(e.data.phase||'')} — ${esc(e.data.status||'')}`;
    else if (e.type === 'channel') detail = `${esc(e.data.channel_name||'')} ${esc(e.data.state||'')}`;
    else if (e.type === 'startup') detail = `channels=${e.data.channel_count} groups=${e.data.group_count}`;
    else detail = JSON.stringify(e.data).substring(0,120);
    return `<div class="log-entry ${esc(e.type)}"><span class="ts">${e.ts}</span><span class="ev">${esc(e.type)}</span>${detail}</div>`;
  }).join('');
}
function clearLog() { logEntries = []; renderLog(); }

// ── SSE connection ─────────────────────────────────────────────────────────────
function connectSSE() {
  const dot = $('#status-dot');
  const es = new EventSource('/api/events');
  es.addEventListener('open', () => { dot.className='ok'; });
  es.addEventListener('error', () => { dot.className=''; setTimeout(connectSSE, 3000); es.close(); });
  ['agent_output','agent_run','channel','startup'].forEach(ev => {
    es.addEventListener(ev, e => {
      try { appendLog(ev, JSON.parse(e.data)); } catch {}
      if (ev === 'agent_run') loadGroups();
    });
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function badgeStatus(s) {
  const map = { active:'ok', paused:'warn', error:'err', done:'dim' };
  const cls = map[s] || 'dim';
  return `<span class="badge ${cls}">${esc(s||'unknown')}</span>`;
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadGroups(); loadProviders(); loadTasks(); loadFeatures();
setInterval(() => { loadGroups(); loadTasks(); }, 30000);
connectSSE();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(_HTML)


# ── Server launch ─────────────────────────────────────────────────────────────

def start_ui_server(port: int = 7842, host: str = "127.0.0.1") -> None:
    """Start the dashboard web server (blocking)."""
    init_database()
    try:
        import uvicorn
    except ImportError:
        raise RuntimeError("uvicorn is required for the web UI. Install it with: pip install uvicorn")

    logger.info("J Claw Dashboard starting on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")
