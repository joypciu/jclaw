"""
Credential proxy for container isolation.

Containers connect here instead of directly to the Anthropic API.
The proxy injects real credentials so containers never see them.

Supports multi-provider smart routing via ProviderRouter:
  - Routes each request to the healthiest provider
  - Records latency and errors for EMA scoring
  - Falls back automatically on provider errors
"""
from __future__ import annotations

import asyncio
import logging
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Optional
from urllib.parse import urlparse

import httpx

from .model_gateway import (
    GatewayAuthMode,
    ProviderConfig,
    ProviderRouter,
    build_provider_router,
    detect_gateway_auth_mode,
)
from .model_registry import load_model_registry
from .protocol_adapter import ProtocolAdapter

logger = logging.getLogger(__name__)


class _ProxyHandler(BaseHTTPRequestHandler):
    """Per-request handler: selects provider, injects credentials, proxies."""

    router: ProviderRouter
    auth_mode: GatewayAuthMode
    adapter: Optional[ProtocolAdapter]

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # suppress default access log — use our logger instead

    def _send_adapter_response(
        self,
        status: int,
        headers: dict[str, str],
        body_or_stream: object,
    ) -> None:
        """Write an adapter response (bytes or SSE generator) to the client."""
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        if isinstance(body_or_stream, bytes):
            self.send_header("Content-Length", str(len(body_or_stream)))
            self.end_headers()
            self.wfile.write(body_or_stream)
        else:
            self.end_headers()
            for chunk in body_or_stream:  # type: ignore[union-attr]
                self.wfile.write(chunk)
                self.wfile.flush()

    def do_request(self) -> None:
        # ── Direct routing via ProtocolAdapter (no LiteLLM) ──────────────
        if self.adapter and self.adapter.handles(self.path):
            length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(length) if length else b""
            status, resp_headers, body_or_stream = self.adapter.handle(body_bytes)
            self._send_adapter_response(status, resp_headers, body_or_stream)
            return

        provider: ProviderConfig = self.router.select_provider()
        upstream = urlparse(provider.base_url)
        scheme = upstream.scheme
        host = upstream.netloc
        path = self.path

        # Read request body
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        # Build forwarded headers
        headers: dict[str, str] = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ("connection", "keep-alive", "transfer-encoding", "host"):
                continue
            headers[k] = v
        headers["host"] = host
        if length:
            headers["Content-Length"] = str(length)

        # Inject credentials
        if self.auth_mode == "api-key":
            headers.pop("x-api-key", None)
            if provider.api_key:
                headers["x-api-key"] = provider.api_key
        else:
            if "authorization" in {k.lower() for k in headers}:
                for k in list(headers):
                    if k.lower() == "authorization":
                        del headers[k]
                if provider.auth_token:
                    headers["Authorization"] = f"Bearer {provider.auth_token}"

        url = f"{scheme}://{host}{path}"
        start = time.monotonic()

        try:
            with httpx.Client(timeout=300.0) as client:
                resp = client.request(
                    method=self.command,
                    url=url,
                    headers=headers,
                    content=body,
                )
            latency_ms = (time.monotonic() - start) * 1000
            if resp.status_code >= 500:
                self.router.record_error(provider.name)
            else:
                self.router.record_success(provider.name, latency_ms)

            self.send_response(resp.status_code)
            for k, v in resp.headers.items():
                lk = k.lower()
                if lk in ("transfer-encoding", "connection"):
                    continue
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp.content)

        except Exception as e:
            self.router.record_error(provider.name)
            logger.error(f"Credential proxy upstream error ({provider.name}): {e}")
            if not self._headers_buffer:  # type: ignore[attr-defined]
                self.send_response(502)
                self.end_headers()
                self.wfile.write(b"Bad Gateway")

    do_GET = do_request
    do_POST = do_request
    do_PUT = do_request
    do_PATCH = do_request
    do_DELETE = do_request


def start_credential_proxy(
    port: int,
    host: str = "127.0.0.1",
) -> HTTPServer:
    auth_mode = detect_gateway_auth_mode()
    router = build_provider_router()

    # Initialise provider health in background (non-blocking)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(router.initialize())
        else:
            # Not yet in async context — skip pings, health starts at defaults
            pass
    except RuntimeError:
        pass

    logger.info(
        f"Credential proxy starting on {host}:{port} "
        f"(auth={auth_mode}, providers={router.provider_count})"
    )

    # Build protocol adapter for direct /v1/messages → OpenAI routing
    registry = load_model_registry()
    adapter: Optional[ProtocolAdapter] = ProtocolAdapter(registry) if registry.all_aliases() else None
    if adapter:
        logger.info(f"Protocol adapter active for aliases: {', '.join(registry.all_aliases())}")
    else:
        logger.info("Protocol adapter inactive (no JCLAW_MODEL_ALIASES), using gateway passthrough")

    # Attach router/auth_mode/adapter as class attributes so handler can access them
    handler_cls = type(
        "_BoundProxyHandler",
        (_ProxyHandler,),
        {"router": router, "auth_mode": auth_mode, "adapter": adapter},
    )

    server = HTTPServer((host, port), handler_cls)
    thread = Thread(target=server.serve_forever, daemon=True, name="credential-proxy")
    thread.start()

    logger.info(f"Credential proxy started on {host}:{port} (auth={auth_mode})")
    return server


def detect_auth_mode() -> GatewayAuthMode:
    return detect_gateway_auth_mode()
