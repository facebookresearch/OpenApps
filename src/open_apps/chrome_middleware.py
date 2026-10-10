"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Inject the global window chrome into every full HTML page.

The chrome -- title bar, dock, agent cursor -- has to appear on every page of
every app, and there is no single place the apps share that could draw it:

* FastHTML routes keep the headers of the app that *defined* them, even after
  being mounted onto the start page's server, so a header added in
  ``start_page.helper.get_app`` never reaches the todo app's pages;
* two apps (maps, the shop) render Jinja templates and never see a FastHTML
  header list at all.

What every page does share is the server. So the chrome goes in on the way
out, the same way a debug toolbar does: buffer an HTML response, splice the
chrome in, fix ``Content-Length``. No app module changes, and an app added
later gets the chrome without opting in.

Only full documents are touched. An htmx swap returns a fragment that lands
inside a page that already has its chrome, so a request carrying ``HX-Request``
passes straight through -- unless it is also ``HX-Boosted``, which is htmx
standing in for a real navigation and replacing the whole body.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


@dataclass
class ChromeParts:
    """What to splice into one page.

    ``head`` goes before ``</head>`` -- the stylesheet, which must be there
    rather than in the body so it applies before an app's own scripts measure
    the layout (Leaflet sizes the map from its container's height at init).
    ``body`` goes before the last ``</body>``. ``html_attrs`` land on the
    ``<html>`` tag, which is what the stylesheet keys the reserved space off.
    """

    head: str = ""
    body: str = ""
    html_attrs: dict[str, str] = field(default_factory=dict)


#: Called once per full-page GET with the request path. Returns ``None`` to
#: leave the page alone.
Renderer = Callable[[str], ChromeParts | None]

_HTML_OPEN = re.compile(r"<html\b", re.IGNORECASE)
_HEAD_CLOSE = re.compile(r"</head\s*>", re.IGNORECASE)
_BODY_CLOSE = re.compile(r"</body\s*>", re.IGNORECASE)


def _attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def inject(html: str, parts: ChromeParts) -> str:
    """Splice ``parts`` into ``html``. Pure; returns ``html`` unchanged when it
    is not a full document.

    "Full document" means it has an ``<html`` tag and a ``</body>``. Anything
    else -- a fragment a route returned without htmx asking for it, an error
    page -- is left as is rather than half-decorated.
    """
    html_match = _HTML_OPEN.search(html)
    body_matches = list(_BODY_CLOSE.finditer(html))
    if html_match is None or not body_matches:
        return html

    # Last </body>, not first: a page can carry the string inside a script or
    # a <template>, and the real one closes the document.
    body_at = body_matches[-1].start()
    head_match = _HEAD_CLOSE.search(html, 0, body_at)
    # No head: the styles go in with the body markup, still ahead of it.
    head_at = head_match.start() if head_match is not None else body_at
    attrs = "".join(f' {k}="{_attr(v)}"' for k, v in parts.html_attrs.items())

    # Spliced back to front so the earlier offsets stay valid.
    html = html[:body_at] + parts.body + html[body_at:]
    html = html[:head_at] + parts.head + html[head_at:]
    html_at = html_match.end()
    return html[:html_at] + attrs + html[html_at:]


class ChromeMiddleware:
    """Pure-ASGI middleware applying :func:`inject` to full-page responses.

    Pure ASGI rather than ``BaseHTTPMiddleware``: the latter runs the endpoint
    in a separate task and has a history of breaking streaming and background
    tasks, and this needs nothing it offers.
    """

    def __init__(self, app: ASGIApp, render: Renderer) -> None:
        self.app = app
        self.render = render

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "GET":
            await self.app(scope, receive, send)
            return
        request_headers = Headers(scope=scope)
        if request_headers.get("hx-request") and not request_headers.get("hx-boosted"):
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, _Buffer(send, lambda: self.render(scope["path"])))


class _Buffer:
    """``send`` wrapper that holds back an HTML body until it is complete.

    Anything that is not ``text/html`` is forwarded untouched from the first
    message, so static files and JSON stream exactly as before.
    """

    def __init__(self, send: Send, render: Callable[[], ChromeParts | None]) -> None:
        self.send = send
        self.render = render
        self.start: Message | None = None
        self.chunks: list[bytes] = []
        self.passthrough = False

    async def __call__(self, message: Message) -> None:
        if self.passthrough:
            await self.send(message)
            return

        if message["type"] == "http.response.start":
            headers = Headers(raw=message["headers"])
            content_type = headers.get("content-type", "")
            # A compressed body cannot be spliced as text. Nothing in this
            # server compresses today; this keeps a future GZipMiddleware from
            # turning every page into mojibake.
            if not content_type.startswith("text/html") or headers.get("content-encoding"):
                self.passthrough = True
                await self.send(message)
                return
            self.start = message
            return

        if message["type"] == "http.response.body":
            self.chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            await self._flush()
            return

        await self.send(message)

    async def _flush(self) -> None:
        body = b"".join(self.chunks)
        try:
            parts = self.render()
        except Exception:
            # The chrome is decoration. A bug in it must cost the page its
            # title bar, not its 200 -- a 500 on every route would zero an
            # entire eval run over a cosmetic layer. Logged with a traceback,
            # and the tests assert the chrome is present, so it is not silent.
            logger.exception("window chrome failed to render; serving the page without it")
            parts = None
        if parts is not None:
            charset = _charset(Headers(raw=self.start["headers"]))
            try:
                body = inject(body.decode(charset), parts).encode(charset)
            except (UnicodeDecodeError, LookupError):
                # Undecodable page: serve it as it came rather than fail it.
                pass
        headers = MutableHeaders(raw=list(self.start["headers"]))
        headers["content-length"] = str(len(body))
        await self.send({**self.start, "headers": headers.raw})
        await self.send({"type": "http.response.body", "body": body, "more_body": False})


def _charset(headers: Headers) -> str:
    match = re.search(r"charset=([\w-]+)", headers.get("content-type", ""), re.IGNORECASE)
    return match.group(1) if match else "utf-8"
