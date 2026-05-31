"""
web/app.py — combined FastAPI application for zotero-tag-process.

Mounts the two existing sub-applications under path prefixes:

    /generate/*   tag-generation web UI (setup + real-time review)
    /update/*     zotero-restructuring web UI (import/apply + stats/network)

Root ``/`` serves a landing page with one card per workflow and a shared nav
(workflow switcher) provided by ``base.html``.

The sub-application templates emit *root-relative* URLs (``/api/...``,
``/review``, ``/stats``, ``EventSource('/api/progress')`` ...). Starlette's
``mount`` strips the prefix on the way *in* so routing works, but the browser
still emits unprefixed URLs from the served HTML. A lightweight ASGI
middleware wraps each sub-app and rewrites root-relative URLs in the HTML it
returns so they carry the mount prefix. This keeps the sub-packages unmodified.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tag_generation.web.app import create_app as create_generate_app
from zotero_restructuring.web.app import create_app as create_update_app

_HERE = Path(__file__).parent
_TEMPLATES_DIR = _HERE / "templates"


class _PrefixRewriteMiddleware:
    """Rewrite root-relative URLs in HTML responses to carry a mount prefix.

    Only HTML (``text/html``) responses are touched; JSON and event-stream
    bodies pass through unchanged. The sub-app's own routing is unaffected —
    Starlette has already stripped the prefix from the inbound path.
    """

    # Match the start of a root-relative URL inside quotes or parentheses,
    # e.g.  href="/review"   fetch('/api/x')   EventSource("/api/progress")
    # but NOT protocol-relative ("//") or already-prefixed URLs.
    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.prefix = prefix.rstrip("/")
        # group 1: opening delimiter (quote or paren), then a single "/"
        # negative lookahead avoids "//" (protocol-relative) and the prefix.
        self._pattern = re.compile(rb"""(["'(])/(?!/)""")
        self._replacement = rb"\1" + self.prefix.encode() + b"/"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = {"is_html": False}
        body_chunks: list[bytes] = []
        response_start: dict | None = {}

        async def _send(message: Message) -> None:
            nonlocal response_start
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                for k, v in headers:
                    if k.lower() == b"content-type" and b"text/html" in v.lower():
                        started["is_html"] = True
                if started["is_html"]:
                    # Defer sending until we have rewritten the full body.
                    response_start = {"message": message, "headers": list(headers)}
                    return
                await send(message)
            elif message["type"] == "http.response.body":
                if not started["is_html"]:
                    await send(message)
                    return
                body_chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                # Final chunk: rewrite, fix content-length, flush.
                body = b"".join(body_chunks)
                body = self._pattern.sub(self._replacement, body)
                assert response_start is not None
                headers = [
                    (k, v)
                    for (k, v) in response_start["headers"]
                    if k.lower() != b"content-length"
                ]
                headers.append((b"content-length", str(len(body)).encode()))
                start_msg = dict(response_start["message"])
                start_msg["headers"] = headers
                await send(start_msg)
                await send({
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                })
            else:
                await send(message)

        await self.app(scope, receive, _send)


def create_app() -> FastAPI:
    app = FastAPI(title="zotero-tag-process")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    generate_app = create_generate_app()
    update_app = create_update_app()

    app.mount("/generate", _PrefixRewriteMiddleware(generate_app, "/generate"),
              name="generate")
    app.mount("/update", _PrefixRewriteMiddleware(update_app, "/update"),
              name="update")

    @app.get("/")
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "workflows": ["generate", "update"]}

    return app


app = create_app()
