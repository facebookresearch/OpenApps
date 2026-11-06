"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Notification toasts -- how an adversarial event shows up on screen.

* :func:`Toast` -- one notification: an icon, its title and text, and a
  dismiss button. The title and text are one link (``/events/open/<id>``),
  which marks the toast read and redirects to what it points at -- the
  sender's conversation for a message.
* :func:`ToastStack` -- every unread toast, in a container that carries the
  page's theme tokens and the device's form factor.
* :func:`toast_parts` -- what the page renderer splices into a full page
  (``open_apps.chrome_middleware``), so unread toasts survive navigation.

The same markup is what ``OpenAppsTask`` injects with ``page.evaluate`` when an
event fires: ``/events/fire`` answers with :func:`ToastStack` rendered for the
page the agent is on, so there is exactly one renderer.

Accessibility, because agents read the tree: each toast is ``role="status"``
with ``aria-live="polite"`` and named by its title; the link and the dismiss
button are real ``<a>`` / ``<button>`` elements with names; the icon is
``aria-hidden``. Dismiss is a plain form post (no script), so it works on the
template-rendered pages that load no htmx -- and on a page the toast was
injected into, where no toast script ever ran.
"""

from __future__ import annotations

import urllib.parse
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from fasthtml.common import A, Button, Div, Form, Span, Style, to_xml

from open_apps.chrome_middleware import ChromeParts
from open_apps.icons import Icon, icon
from open_apps.notifications import Notification

_HERE = Path(__file__).resolve().parent
_FORM_FACTORS = ("desktop", "tablet", "phone")

CSS_ELEMENT_ID = "oa-toast-css"
STACK_ELEMENT_ID = "oa-toasts"


@lru_cache(maxsize=None)
def toast_css() -> str:
    """``toast.css``, read once. A real file for the same reasons as
    ``chrome.css``; inlined into the page because nothing here is fetched at
    page load."""
    return (_HERE / "toast.css").read_text()


def _path_segment(notification_id: str) -> str:
    """``notification_id`` as one URL path segment.

    An id is any non-empty string (config keys, or whatever a caller posts),
    so a ``/``, ``?``, ``#`` or ``%`` in it would otherwise change which route
    -- or which part of the URL -- it lands in. Every character but the
    unreserved ones is percent-encoded; the server decodes the path before
    routing, and the routes take the rest of the path (``:path``) so an
    encoded ``/`` still reaches them.
    """
    return urllib.parse.quote(notification_id, safe="")


def open_href(notification: Notification) -> str:
    return f"/events/open/{_path_segment(notification.id)}"


def dismiss_action(notification: Notification) -> str:
    return f"/events/dismiss/{_path_segment(notification.id)}"


def Toast(notification: Notification):
    """One notification. Without a link (a system notice that points nowhere)
    the text is not a link, but the toast is still dismissible."""
    title_id = f"oa-toast-{notification.dom_id}-title"
    glyph = Icon.MESSAGE if notification.channel == "messenger" else Icon.BELL
    content = (
        Span(icon(glyph, size=18), cls="oa-toast-icon", aria_hidden="true"),
        Span(
            Span(notification.title, cls="oa-toast-title", id=title_id),
            Span(notification.body, cls="oa-toast-body"),
            cls="oa-toast-text",
        ),
    )
    main = (
        A(*content, href=open_href(notification), cls="oa-toast-main", data_testid="toast-link")
        if notification.link
        else Div(*content, cls="oa-toast-main")
    )
    return Div(
        main,
        Form(
            Button(
                icon(Icon.CLOSE, size=16),
                type="submit",
                cls="oa-toast-dismiss",
                aria_label=f"Dismiss notification: {notification.title}",
                title="Dismiss",
                data_testid="toast-dismiss",
            ),
            method="post",
            action=dismiss_action(notification),
        ),
        cls="oa-toast",
        role="status",
        aria_live="polite",
        aria_atomic="true",
        aria_labelledby=title_id,
        data_toast_id=notification.id,
        data_channel=notification.channel,
        data_testid="toast",
    )


def ToastStack(
    notifications: Iterable[Notification], *, form_factor: str = "desktop", tokens_css: str = ""
):
    """Every unread toast, newest last, in the themed, device-aware container.

    ``tokens_css`` is the page's app theme already scoped to ``#oa-toasts``
    (``render_theme_css(theme, selector="#oa-toasts")``).
    """
    form_factor = form_factor if form_factor in _FORM_FACTORS else "desktop"
    return Div(
        Style(tokens_css) if tokens_css else None,
        *(Toast(n) for n in notifications),
        id=STACK_ELEMENT_ID,
        cls="oa-toasts",
        data_oa_device=form_factor,
        data_testid="toasts",
    )


def render_stack(notifications: Iterable[Notification], **kw) -> str:
    return to_xml(ToastStack(notifications, **kw))


def toast_parts(
    notifications: Iterable[Notification], *, form_factor: str = "desktop", tokens_css: str = ""
) -> ChromeParts | None:
    """The stylesheet and the stack for one page, or None with nothing unread."""
    notifications = list(notifications)
    if not notifications:
        return None
    return ChromeParts(
        head=f'<style id="{CSS_ELEMENT_ID}">{toast_css()}</style>',
        body=render_stack(notifications, form_factor=form_factor, tokens_css=tokens_css),
    )
