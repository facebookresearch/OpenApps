"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Global window chrome -- the parts of a desktop that are not any one app.

* :func:`TitleBar` -- the window's title and its close / minimise / maximise
  controls, macOS- or Windows-style.
* :func:`Dock` -- pinned app shortcuts with their names under them, a running
  indicator, a way home, and an all-apps panel.
* :func:`AgentCursor` -- a rendered pointer that glides between the positions
  the agent's mouse is teleported to, so screenshots and recordings show where
  the agent is acting.

:func:`chrome_parts` assembles them into what
:mod:`open_apps.chrome_middleware` splices into each page. Nothing here knows
about request paths or server state; the start page owns both and passes in
plain values, which keeps this module renderable from a test with no server.

The same two rules as the rest of :mod:`open_apps.ui` apply, for the same
reason -- agents read the accessibility tree:

* every control is a real ``<button>`` or ``<a>`` with a name. The window
  controls are posted forms, not script handlers, so they work on every page,
  including the template-rendered ones that load no htmx;
* decoration is ``aria-hidden``. The cursor in particular must never show up
  as a node an agent might try to act on.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from fasthtml.common import A, Button, Div, Form, Img, Nav, NotStr, Span, Style, to_xml

from open_apps.chrome_middleware import ChromeParts
from open_apps.icons import Icon, icon

_HERE = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def _asset(name: str) -> str:
    """A sibling ``.css`` / ``.js`` file, read once.

    Kept as real files rather than Python strings so an editor, a linter and
    ``dev.py``'s reloader all treat them as what they are. Still inlined into
    the page: nothing in this repo is fetched at page load, and a stylesheet
    request racing an agent's first screenshot is exactly the flake that rule
    exists to prevent.
    """
    return (_HERE / name).read_text()


def chrome_css() -> str:
    return _asset("chrome.css")


def chrome_js() -> str:
    return _asset("chrome.js")


@dataclass(frozen=True)
class ChromeApp:
    """One app as the chrome sees it: enough to draw an icon and link to it."""

    key: str
    title: str
    href: str
    icon: str


def _token(name: Any, fallback: str) -> str:
    """``var(--name, fallback)`` for a token name from config, or the fallback.

    Config carries token names, not colours, so a theme swap repaints the
    cursor too. Anything that is not a plain custom-property name is dropped
    rather than interpolated into a style attribute.
    """
    name = str(name or "")
    if name and all(c.isalnum() or c in "-_" for c in name):
        return f"var(--{name}, {fallback})"
    return fallback


# --------------------------------------------------------------------------
# Title bar
# --------------------------------------------------------------------------

#: action -> (macOS glyph, Windows glyph, verb). Order is left-to-right in both
#: styles: macOS reads close/minimise/zoom, Windows minimise/maximise/close.
_MAC_ORDER = ("close", "minimize", "maximize")
_WINDOWS_ORDER = ("minimize", "maximize", "close")


def _window_button(action: str, app: ChromeApp, style: str, maximized: bool):
    if action == "maximize":
        verb = "Restore" if maximized else "Maximize"
        glyph = Icon.RESTORE if maximized else Icon.MAXIMIZE
    else:
        verb = action.capitalize()
        glyph = Icon.CLOSE if action == "close" else Icon.MINIMIZE
    # macOS draws the glyph inside a 12px light, so it is tiny; Windows draws
    # it bare in a 46px caption button.
    size = 8 if style == "mac" else 12
    glyph_el = icon(glyph, size=size)
    return Form(
        Button(
            Span(glyph_el) if style == "mac" else glyph_el,
            type="submit",
            cls=f"oa-win-btn is-{action}",
            aria_label=f"{verb} {app.title}",
            title=verb,
            data_testid=f"window-{action}",
        ),
        method="post",
        action=f"/chrome/{action}/{app.key}",
    )


def TitleBar(app: ChromeApp, style: str = "mac", maximized: bool = False):
    """The window's title bar. ``style``: mac | windows.

    The three controls post to ``/chrome/<action>/<app>``; the server answers
    with a redirect, so they are plain navigations to anything driving the
    page -- no script, no swap target, nothing that can half-work.
    """
    style = style if style in ("mac", "windows") else "mac"
    order = _MAC_ORDER if style == "mac" else _WINDOWS_ORDER
    controls = Div(
        *(_window_button(a, app, style, maximized) for a in order),
        cls="oa-window-controls",
        role="group",
        aria_label="Window controls",
    )
    title = Div(
        Img(src=app.icon, alt="", aria_hidden="true"),
        Span(app.title),
        cls="oa-titlebar-title",
        data_testid="window-title",
    )
    return Div(
        controls,
        title,
        # The third grid column. Empty on macOS, where it balances the lights
        # so the title sits at true centre rather than centre-of-what-is-left.
        Div() if style == "mac" else None,
        id="oa-titlebar",
        cls=f"oa-titlebar is-{style}",
        data_app=app.key,
    )


# --------------------------------------------------------------------------
# Dock
# --------------------------------------------------------------------------


def DockItem(
    title: str,
    href: str,
    *,
    image: str | None = None,
    glyph: Icon | None = None,
    labels: bool = True,
    current: bool = False,
    running: bool = False,
    shortcut: str | None = None,
    testid: str,
):
    """One dock entry: icon, name underneath, running dot.

    With ``labels`` off the name leaves the page but not the link: it moves to
    ``aria-label``, so the bare-icon dock is harder to *see* without being any
    harder to *read* from the accessibility tree. That asymmetry is the point
    of the knob -- it isolates visual grounding.
    """
    if image is not None:
        art = Div(Img(src=image, alt="", aria_hidden="true"), cls="oa-dock-icon")
    else:
        art = Div(icon(glyph or Icon.APPS, size=22), cls="oa-dock-icon is-glyph")
    return A(
        art,
        Span(title, cls="oa-dock-label") if labels else None,
        href=href,
        cls="oa-dock-item",
        title=title,
        aria_label=None if labels else title,
        aria_current="page" if current else None,
        aria_keyshortcuts=shortcut,
        data_running="true" if running else None,
        data_testid=testid,
    )


def Dock(
    apps: list[ChromeApp],
    *,
    docked: list[str],
    running: list[str],
    current: str | None,
    labels: bool = True,
    home: bool = True,
    launcher: bool = True,
    shortcuts: bool = True,
):
    """The dock.

    ``docked`` is the ordered list of app keys with a permanent slot.
    ``running`` apps without one follow a separator, as on macOS, so an app
    reached through the all-apps panel still gets a one-click way back.
    ``current`` is the app on screen (``None`` on the desktop), marked
    ``aria-current="page"``.

    Shortcuts number the items left to right: Alt+0 is the desktop, Alt+1..9
    the first nine apps. They are advertised in ``aria-keyshortcuts`` -- the
    only place an agent reading the tree would find them.
    """
    by_key = {a.key: a for a in apps}
    slots = [by_key[k] for k in docked if k in by_key]
    extras = [by_key[k] for k in running if k in by_key and k not in docked]

    items: list = []
    if home:
        items.append(
            DockItem(
                "Desktop",
                "/",
                glyph=Icon.HOME,
                labels=labels,
                current=current is None,
                shortcut="Alt+0" if shortcuts else None,
                testid="dock-desktop",
            )
        )
    for n, app in enumerate([*slots, *extras], start=1):
        if extras and app is extras[0]:
            items.append(Div(cls="oa-dock-sep", aria_hidden="true"))
        items.append(
            DockItem(
                app.title,
                app.href,
                image=app.icon,
                labels=labels,
                current=app.key == current,
                running=app.key in running,
                shortcut=f"Alt+{n}" if shortcuts and n <= 9 else None,
                testid=f"dock-{app.key}",
            )
        )
    if launcher:
        items.append(Div(cls="oa-dock-sep", aria_hidden="true"))
        items.append(_Launcher(apps, current=current, labels=labels))

    return Nav(
        *items,
        id="oa-dock",
        cls="oa-dock" + ("" if labels else " is-unlabelled"),
        aria_label="Dock",
        data_shortcuts=str(shortcuts).lower(),
        data_testid="dock",
    )


def _Launcher(apps: list[ChromeApp], *, current: str | None, labels: bool):
    """The all-apps button and the panel it opens.

    The panel lists *every* enabled app, whatever the dock shows -- it is the
    guaranteed route to an app an experiment has taken out of the dock. Closed
    by default and toggled client-side; see ``initLauncher`` in chrome.js.
    """
    return Div(
        Button(
            Div(icon(Icon.APPS, size=22), cls="oa-dock-icon is-glyph"),
            Span("All apps", cls="oa-dock-label") if labels else None,
            type="button",
            id="oa-dock-launcher-btn",
            cls="oa-dock-item",
            title="All apps",
            aria_label=None if labels else "All apps",
            aria_expanded="false",
            aria_controls="oa-dock-panel",
            data_testid="dock-launcher",
        ),
        Div(
            Span("All apps", cls="oa-dock-panel-title", aria_hidden="true"),
            *(
                DockItem(
                    a.title,
                    a.href,
                    image=a.icon,
                    current=a.key == current,
                    testid=f"dock-panel-{a.key}",
                )
                for a in apps
            ),
            id="oa-dock-panel",
            cls="oa-dock-panel",
            role="group",
            aria_label="All apps",
            hidden=True,
            data_testid="dock-panel",
        ),
        cls="oa-dock-launcher",
    )


# --------------------------------------------------------------------------
# Agent cursor
# --------------------------------------------------------------------------

# The classic pointer silhouette, tip at (3, 2) of a 24px box. Filled from a
# gradient between the two cursor tokens and outlined in white, so it holds up
# over a light page, a dark page and a map tile alike.
_ARROW_SVG = (
    '<svg class="oa-cursor-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"'
    ' width="26" height="26" aria-hidden="true" focusable="false">'
    '<defs><linearGradient id="oa-cursor-grad" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" style="stop-color: var(--oa-cursor-a)"/>'
    '<stop offset="1" style="stop-color: var(--oa-cursor-b)"/>'
    "</linearGradient></defs>"
    '<path class="oa-cursor-fill" d="M3 2 3 19.2 7.4 15.1 10.4 21.6 13.5 20.2 10.6 13.9 16.9 13.9Z"'
    ' fill="url(#oa-cursor-grad)" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round"/>'
    "</svg>"
)


def AgentCursor(
    *,
    show: str = "auto",
    style: str = "glow",
    glide_ms: int = 240,
    click_ripple: bool = True,
    follow_focus: bool = True,
    label: str | None = None,
):
    """The rendered agent pointer. Hidden until chrome.js has a position for it.

    Configuration travels as data attributes for the script to read, rather
    than as a second inline ``<script>`` with values interpolated into it --
    there is no escaping to get wrong in an attribute FastHTML already quotes.
    """
    style = style if style in ("glow", "classic") else "glow"
    show = show if show in ("auto", "always", "never") else "auto"
    return Div(
        Div(cls="oa-cursor-halo"),
        NotStr(_ARROW_SVG),
        Span(str(label), cls="oa-cursor-label") if label else None,
        id="oa-cursor",
        cls=f"oa-cursor is-{style}",
        aria_hidden="true",
        hidden=True,
        data_show=show,
        data_glide=str(max(0, int(glide_ms))),
        data_ripple=str(bool(click_ripple)).lower(),
        data_follow_focus=str(bool(follow_focus)).lower(),
        data_testid="agent-cursor",
    )


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def chrome_parts(
    cfg: dict,
    *,
    apps: list[ChromeApp],
    current: str | None,
    docked: list[str],
    running: list[str],
    maximized: bool,
    form_factor: str,
    shell_tokens_css: str,
    dock_overlay: bool = False,
) -> ChromeParts | None:
    """Everything the middleware splices into one page, or ``None`` for nothing.

    ``cfg`` is ``apps.chrome`` as a plain dict. ``current`` is the app being
    shown, ``None`` on the start page -- which gets no title bar, because the
    desktop is not a window. ``shell_tokens_css`` is the shell theme's token
    block, already scoped to ``#oa-chrome``.

    ``dock_overlay`` floats the dock over the page instead of reserving space
    for it. Only for a page with nothing clickable under the dock -- the
    desktop shell's wallpaper -- because anything under it is unclickable.
    """
    if not cfg.get("enabled", True):
        return None
    titlebar_cfg = cfg.get("titlebar") or {}
    dock_cfg = cfg.get("dock") or {}
    cursor_cfg = cfg.get("cursor") or {}

    def on(section: dict) -> bool:
        return bool(section.get("enabled", True)) and form_factor in (
            section.get("form_factors") or [form_factor]
        )

    by_key = {a.key: a for a in apps}
    show_titlebar = on(titlebar_cfg) and current in by_key
    show_dock = on(dock_cfg) and (current is not None or dock_cfg.get("on_start_page", True))
    show_cursor = bool(cursor_cfg.get("enabled", True)) and cursor_cfg.get("show") != "never"
    if not (show_titlebar or show_dock or show_cursor):
        return None

    labels = bool(dock_cfg.get("labels", True))
    style = str(titlebar_cfg.get("style", "mac"))
    colors = list(cursor_cfg.get("colors") or [])

    titlebar = TitleBar(by_key[current], style=style, maximized=maximized) if show_titlebar else None
    dock = (
        Dock(
            apps,
            docked=docked,
            running=running,
            current=current,
            labels=labels,
            home=bool(dock_cfg.get("home", True)),
            launcher=bool(dock_cfg.get("launcher", True)),
            shortcuts=bool(dock_cfg.get("shortcuts", True)),
        )
        if show_dock
        else None
    )
    cursor = (
        AgentCursor(
            show=str(cursor_cfg.get("show", "auto")),
            style=str(cursor_cfg.get("style", "glow")),
            glide_ms=int(cursor_cfg.get("glide_ms", 240)),
            click_ripple=bool(cursor_cfg.get("click_ripple", True)),
            follow_focus=bool(cursor_cfg.get("follow_focus", True)),
            label=cursor_cfg.get("label"),
        )
        if show_cursor
        else None
    )
    layer = Div(
        Style(shell_tokens_css) if shell_tokens_css else None,
        dock,
        cursor,
        id="oa-chrome",
        style=(
            f"--oa-cursor-a: {_token(colors[0] if colors else None, '#006fff')};"
            f"--oa-cursor-b: {_token(colors[1] if len(colors) > 1 else None, '#931efa')};"
        ),
    )

    attrs = {"data-oa-chrome": "", "data-oa-device": form_factor}
    if current is not None:
        attrs["data-oa-app"] = current
    if show_titlebar:
        attrs["data-oa-titlebar"] = style
    if show_dock:
        attrs["data-oa-dock"] = "overlay" if dock_overlay else "reserve"
        attrs["data-oa-labels"] = str(labels).lower()
    if maximized and show_titlebar:
        attrs["data-oa-maximized"] = ""

    return ChromeParts(
        head=f"<style id=\"oa-chrome-css\">{chrome_css()}</style>",
        body=(to_xml(titlebar) if titlebar is not None else "")
        + to_xml(layer)
        + f"<script id=\"oa-chrome-js\">{chrome_js()}</script>",
        html_attrs=attrs,
    )
