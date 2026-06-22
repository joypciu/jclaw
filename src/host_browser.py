"""Host browser automation IPC handlers.

Python port of host-browser runtime actions used by IPC tasks.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import platform
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

import httpx

from .config import DATA_DIR
from .group_folder import resolve_group_folder_path
from .logger import logger

DEFAULT_SESSION = "default"
DEFAULT_NAVIGATION_TIMEOUT_MS = 30_000
DEFAULT_IDLE_TIMEOUT_MS = 300_000
MAX_SNAPSHOT_ENTRIES = 200
MAX_TEXT_CHARS = 12_000
RESULTS_DIR = "browser_results"
SUPPORTED_TYPES = {
    "host_browser_open",
    "host_browser_search_google",
    "host_browser_snapshot",
    "host_browser_click",
    "host_browser_fill",
    "host_browser_press",
    "host_browser_read_text",
    "host_browser_close",
    "download_from_web",
}


_sessions: dict[str, dict[str, Any]] = {}
_playwright_cm: Any | None = None
_playwright_obj: Any | None = None


def _sanitize_segment(value: str) -> str:
    sanitized = re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower())
    sanitized = re.sub(r"^-+|-+$", "", sanitized)
    return sanitized or "default"


def _get_session_name(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return _sanitize_segment(value)
    return DEFAULT_SESSION


def _get_browser_name(value: Any) -> str:
    if value in {"firefox", "opera"}:
        return str(value)
    return "chrome"


def _get_headless_mode() -> bool:
    raw = os.environ.get("JCLAW_HOST_BROWSER_HEADLESS", "")
    return raw == "1" or raw.lower() == "true"


def _get_idle_timeout_ms() -> int:
    raw = os.environ.get("JCLAW_HOST_BROWSER_IDLE_MS", "")
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 0
    return parsed if parsed > 0 else DEFAULT_IDLE_TIMEOUT_MS


def _build_session_key(group_folder: str, browser: str, session_name: str) -> str:
    return f"{_sanitize_segment(group_folder)}:{browser}:{session_name}"


def _get_browser_candidates(browser: str) -> list[str]:
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA") or str(
            Path(os.environ.get("USERPROFILE", r"C:\Users\Default")) / "AppData" / "Local"
        )

        if browser == "firefox":
            return [
                str(Path(program_files) / "Mozilla Firefox" / "firefox.exe"),
                str(Path(program_files_x86) / "Mozilla Firefox" / "firefox.exe"),
            ]
        if browser == "opera":
            return [
                str(Path(local_app_data) / "Programs" / "Opera" / "opera.exe"),
                str(Path(program_files) / "Opera" / "opera.exe"),
                str(Path(program_files_x86) / "Opera" / "opera.exe"),
                str(Path(program_files) / "Opera" / "launcher.exe"),
                str(Path(program_files_x86) / "Opera" / "launcher.exe"),
            ]
        return [
            str(Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ]

    if platform.system().lower() == "darwin":
        if browser == "firefox":
            return ["/Applications/Firefox.app/Contents/MacOS/firefox"]
        if browser == "opera":
            return ["/Applications/Opera.app/Contents/MacOS/Opera"]
        return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]

    if browser == "firefox":
        return ["/usr/bin/firefox", "/snap/bin/firefox"]
    if browser == "opera":
        return ["/usr/bin/opera", "/usr/bin/opera-stable"]
    return ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"]


def _resolve_browser_executable(browser: str) -> str:
    env_key = (
        "JCLAW_CHROME_PATH"
        if browser == "chrome"
        else "JCLAW_FIREFOX_PATH"
        if browser == "firefox"
        else "JCLAW_OPERA_PATH"
    )
    explicit = os.environ.get(env_key)
    if explicit and Path(explicit).exists():
        return explicit

    for candidate in _get_browser_candidates(browser):
        if Path(candidate).exists():
            return candidate

    raise RuntimeError(f"No {browser} executable found. Set {env_key} in environment.")


async def _load_playwright() -> Any:
    global _playwright_cm, _playwright_obj

    if _playwright_obj is not None:
        return _playwright_obj

    try:
        mod = importlib.import_module("playwright.async_api")
    except Exception as exc:
        raise RuntimeError(
            "playwright is not installed. Install with: pip install playwright and run playwright install"
        ) from exc

    _playwright_cm = mod.async_playwright()
    assert _playwright_cm is not None
    _playwright_obj = await _playwright_cm.start()
    return _playwright_obj


def _clear_session(session_key: str) -> None:
    session = _sessions.get(session_key)
    if session is None:
        return

    idle_task = session.get("idle_task")
    if isinstance(idle_task, asyncio.Task):
        idle_task.cancel()

    _sessions.pop(session_key, None)


def _reset_idle_timer(session: dict[str, Any]) -> None:
    idle_task = session.get("idle_task")
    if isinstance(idle_task, asyncio.Task):
        idle_task.cancel()

    async def _close_idle() -> None:
        await asyncio.sleep(_get_idle_timeout_ms() / 1000)
        try:
            await session["context"].close()
        except Exception as exc:
            logger.warning("Failed to close browser session %s: %s", session.get("key"), exc)
        finally:
            _clear_session(str(session.get("key", "")))

    session["idle_task"] = asyncio.create_task(_close_idle())


async def _ensure_session(group_folder: str, browser: str, session_name: str) -> dict[str, Any]:
    key = _build_session_key(group_folder, browser, session_name)
    existing = _sessions.get(key)
    if existing is not None:
        _reset_idle_timer(existing)
        pages = existing["context"].pages
        if pages:
            existing["page"] = pages[0]
        return existing

    playwright = await _load_playwright()
    executable_path = _resolve_browser_executable(browser)

    user_data_dir = DATA_DIR / "host-browser" / _sanitize_segment(group_folder) / browser / session_name
    user_data_dir.mkdir(parents=True, exist_ok=True)

    common_options = {
        "executable_path": executable_path,
        "headless": _get_headless_mode(),
        "accept_downloads": True,
        "viewport": {"width": 1440, "height": 960},
    }

    if browser == "firefox":
        context = await playwright.firefox.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            **common_options,
        )
    else:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            ignore_default_args=["--enable-automation"],
            **common_options,
        )

    page = context.pages[0] if context.pages else await context.new_page()
    session = {"key": key, "context": context, "page": page, "idle_task": None}
    _sessions[key] = session
    _reset_idle_timer(session)
    return session


async def _navigate(page: Any, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_NAVIGATION_TIMEOUT_MS)
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass


async def _create_snapshot(page: Any, interactive_only: bool) -> list[dict[str, str]]:
    script = r"""
({ interactiveOnly, maxEntries }) => {
  const entries = [];
  const cleanText = (value) => (value || '').replace(/\s+/g, ' ').trim().slice(0, 180);
  const isVisible = (element) => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };

  document.querySelectorAll('[data-jclaw-ref]').forEach((el) => el.removeAttribute('data-jclaw-ref'));

  const selector = interactiveOnly
    ? 'a,button,input,textarea,select,summary,[role="button"],[role="link"],[contenteditable="true"],label,[onclick]'
    : 'body *';

  let index = 1;
  for (const node of Array.from(document.querySelectorAll(selector))) {
    if (!(node instanceof HTMLElement)) continue;
    if (!isVisible(node)) continue;

    const ref = `e${index++}`;
    node.setAttribute('data-jclaw-ref', ref);

    const href = node instanceof HTMLAnchorElement ? node.href : '';
    const type = node instanceof HTMLInputElement ? node.type : '';
    const placeholder = (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) ? node.placeholder : '';
    const label = node.getAttribute('aria-label') || node.getAttribute('name') || placeholder || node.getAttribute('title') || '';

    entries.push({
      ref,
      tag: node.tagName.toLowerCase(),
      role: node.getAttribute('role') || '',
      type,
      text: cleanText(node.innerText || node.textContent),
      label: cleanText(label),
      href: cleanText(href),
    });

    if (entries.length >= maxEntries) break;
  }

  return entries;
}
"""
    return await page.evaluate(script, {"interactiveOnly": interactive_only, "maxEntries": MAX_SNAPSHOT_ENTRIES})


def _format_snapshot(entries: list[dict[str, str]]) -> str:
    if not entries:
        return "No matching visible elements found."

    lines: list[str] = []
    for entry in entries:
        details = [entry.get("tag", "")]
        for k in ["role", "type", "label", "text", "href"]:
            v = entry.get(k)
            if v:
                details.append(f"{k}={v}")
        lines.append(f"@{entry.get('ref', '')} {' | '.join(details)}")
    return "\n".join(lines)


async def _page_summary(page: Any) -> dict[str, str]:
    title = ""
    try:
        title = await page.title()
    except Exception:
        pass
    return {"title": title, "url": page.url}


async def _read_page_text(page: Any) -> str:
    text = await page.evaluate("() => document.body?.innerText || ''")
    return re.sub(r"\s+\n", "\n", str(text)).strip()[:MAX_TEXT_CHARS]


def _result_path(data_dir: str, source_group: str, request_id: str) -> Path:
    result_dir = Path(data_dir) / "ipc" / source_group / RESULTS_DIR
    result_dir.mkdir(parents=True, exist_ok=True)
    return result_dir / f"{request_id}.json"


def _write_result(data_dir: str, source_group: str, request_id: str, result: dict[str, Any]) -> None:
    _result_path(data_dir, source_group, request_id).write_text(
        json_dumps(result),
        encoding="utf-8",
    )


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Missing {field}")
    return value.strip()


def _sanitize_filename(value: str) -> str:
    return re.sub(r"[<>:\"/\\|?*]+", "-", value).replace(" ", "-")


def _download_name(url: str, preferred_name: str | None) -> str:
    if preferred_name:
        return _sanitize_filename(preferred_name)

    try:
        parsed = urlparse(url)
        base = Path(parsed.path).name
        if base and base != "/":
            return _sanitize_filename(base)
    except Exception:
        pass

    return "download.bin"


async def _download_from_web(source_group: str, url: str, filename: str | None) -> dict[str, Any]:
    group_dir = resolve_group_folder_path(source_group)
    downloads_dir = group_dir / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    relative_path = Path("downloads") / _download_name(url, filename)
    output_path = group_dir / relative_path

    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"Download failed with status {resp.status_code}")
            with output_path.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)

    stat = output_path.stat()
    return {
        "success": True,
        "message": f"Downloaded {url} to {relative_path.as_posix()}",
        "data": {"path": relative_path.as_posix(), "bytes": stat.st_size},
    }


async def _handle_browser_open(source_group: str, data: dict[str, Any]) -> dict[str, Any]:
    browser = _get_browser_name(data.get("browser"))
    session_name = _get_session_name(data.get("session"))
    session = await _ensure_session(source_group, browser, session_name)
    url = _require_string(data.get("url"), "url")

    await _navigate(session["page"], url)
    _reset_idle_timer(session)

    return {
        "success": True,
        "message": f"Opened {url} in {browser} ({session_name}).",
        "data": await _page_summary(session["page"]),
    }


async def _handle_google_search(source_group: str, data: dict[str, Any]) -> dict[str, Any]:
    query = _require_string(data.get("query"), "query")
    payload = dict(data)
    payload["url"] = f"https://www.google.com/search?q={quote_plus(query)}"
    return await _handle_browser_open(source_group, payload)


async def _handle_snapshot(source_group: str, data: dict[str, Any]) -> dict[str, Any]:
    browser = _get_browser_name(data.get("browser"))
    session_name = _get_session_name(data.get("session"))
    session = await _ensure_session(source_group, browser, session_name)
    interactive_only = data.get("interactive_only") is not False

    entries = await _create_snapshot(session["page"], interactive_only)
    _reset_idle_timer(session)

    return {
        "success": True,
        "message": _format_snapshot(entries),
        "data": {
            "interactiveOnly": interactive_only,
            "count": len(entries),
            "page": await _page_summary(session["page"]),
        },
    }


async def _with_ref(
    source_group: str,
    data: dict[str, Any],
    handler: Any,
    action: str,
) -> dict[str, Any]:
    browser = _get_browser_name(data.get("browser"))
    session_name = _get_session_name(data.get("session"))
    session = await _ensure_session(source_group, browser, session_name)

    ref = _require_string(data.get("ref"), "ref").lstrip("@")
    text = data.get("text") if isinstance(data.get("text"), str) else None
    locator = session["page"].locator(f'[data-jclaw-ref="{ref}"]').first

    await locator.wait_for(timeout=5000, state="visible")
    await handler(session["page"], ref, text)
    try:
        await session["page"].wait_for_load_state("networkidle", timeout=2500)
    except Exception:
        pass

    _reset_idle_timer(session)
    return {
        "success": True,
        "message": f"{action} @{ref} in {session_name}.",
        "data": await _page_summary(session["page"]),
    }


async def _handle_read_text(source_group: str, data: dict[str, Any]) -> dict[str, Any]:
    browser = _get_browser_name(data.get("browser"))
    session_name = _get_session_name(data.get("session"))
    session = await _ensure_session(source_group, browser, session_name)

    text = await _read_page_text(session["page"])
    _reset_idle_timer(session)

    return {
        "success": True,
        "message": text or "The page does not expose readable text.",
        "data": await _page_summary(session["page"]),
    }


async def _handle_close(source_group: str, data: dict[str, Any]) -> dict[str, Any]:
    browser = _get_browser_name(data.get("browser"))
    session_name = _get_session_name(data.get("session"))
    session_key = _build_session_key(source_group, browser, session_name)
    session = _sessions.get(session_key)

    if session is None:
        return {
            "success": True,
            "message": f"No active {browser} session named {session_name}.",
        }

    await session["context"].close()
    _clear_session(session_key)
    return {
        "success": True,
        "message": f"Closed {browser} session {session_name}.",
    }


async def _execute_task(source_group: str, data: dict[str, Any]) -> dict[str, Any]:
    task_type = data.get("type")

    if task_type == "host_browser_open":
        return await _handle_browser_open(source_group, data)
    if task_type == "host_browser_search_google":
        return await _handle_google_search(source_group, data)
    if task_type == "host_browser_snapshot":
        return await _handle_snapshot(source_group, data)
    if task_type == "host_browser_click":
        return await _with_ref(
            source_group,
            data,
            lambda page, ref, _text: page.locator(f'[data-jclaw-ref="{ref}"]').first.click(),
            "Clicked",
        )
    if task_type == "host_browser_fill":
        return await _with_ref(
            source_group,
            data,
            lambda page, ref, text: page.locator(f'[data-jclaw-ref="{ref}"]').first.fill(text or ""),
            "Filled",
        )
    if task_type == "host_browser_press":
        browser = _get_browser_name(data.get("browser"))
        session_name = _get_session_name(data.get("session"))
        session = await _ensure_session(source_group, browser, session_name)
        key = _require_string(data.get("key"), "key")
        await session["page"].keyboard.press(key)
        try:
            await session["page"].wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass
        _reset_idle_timer(session)
        return {
            "success": True,
            "message": f"Pressed {key} in {session_name}.",
            "data": await _page_summary(session["page"]),
        }
    if task_type == "host_browser_read_text":
        return await _handle_read_text(source_group, data)
    if task_type == "host_browser_close":
        return await _handle_close(source_group, data)
    if task_type == "download_from_web":
        return await _download_from_web(
            source_group,
            _require_string(data.get("url"), "url"),
            data.get("filename") if isinstance(data.get("filename"), str) else None,
        )

    return {
        "success": False,
        "message": f"Unsupported host browser task: {task_type}",
    }


async def handle_host_browser_ipc(
    data: dict[str, Any],
    source_group: str,
    data_dir: str,
) -> bool:
    task_type = data.get("type") if isinstance(data.get("type"), str) else ""
    if task_type not in SUPPORTED_TYPES:
        return False

    request_id = data.get("requestId") if isinstance(data.get("requestId"), str) else ""
    if not request_id:
        logger.warning("Host browser task blocked: missing requestId source=%s type=%s", source_group, task_type)
        return True

    try:
        result = await _execute_task(source_group, data)
    except Exception as exc:
        result = {"success": False, "message": str(exc)}

    _write_result(data_dir, source_group, request_id, result)

    if bool(result.get("success")):
        logger.info("Host browser task completed source=%s type=%s request=%s", source_group, task_type, request_id)
    else:
        logger.warning(
            "Host browser task failed source=%s type=%s request=%s message=%s",
            source_group,
            task_type,
            request_id,
            result.get("message"),
        )

    return True


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value)
