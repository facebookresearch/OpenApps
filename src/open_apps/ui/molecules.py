"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Molecules -- atoms composed into recognisable UI pieces.

These are the parts of the desktop shell: the toolbar and its chips, the
launcher menu, a pinnable app row, a desktop shortcut.

Two conventions matter here, both for the same reason -- this UI is scored by
agents, not only looked at by people:

**Everything interactive is a real control with a real name.** Buttons are
``<button>`` with ``aria-label``, not styled ``<div>``s. An agent driving the
accessibility tree can only act on what is named there, and a div with a click
handler is invisible to it.

**Nothing here reads the clock or the network directly.** Both the time and the
weather arrive as strings from the caller. The weather is always config -- a
lookup would be egress the eval nodes cannot make, and a fixed value means a
task can ask "what is the temperature?" and have a correct answer. The clock
now shows real time by default, with a config override to freeze it; see the
note on screenshots in ``config/apps/start_page/layout/desktop.yaml``.
"""
from __future__ import annotations

from fasthtml.common import A, Button, Div, Span

from open_apps.icons import Icon, icon
from open_apps.ui.atoms import IconButton, Stack, Text, _cls

#: Weather condition -> icon. The condition string comes from the desktop
#: config, so this is a closed set; anything unrecognised falls back to CLOUD
#: rather than rendering a gap where an affordance should be.
WEATHER_ICONS = {
    "clear": Icon.SUN,
    "sunny": Icon.SUN,
    "cloudy": Icon.CLOUD,
    "overcast": Icon.CLOUD,
    "rain": Icon.CLOUD_RAIN,
    "rainy": Icon.CLOUD_RAIN,
}


def Clock(time_text: str, cls: str = ""):
    """Toolbar clock.

    ``time_text`` is rendered verbatim. Whether it is the live clock or a
    frozen string is decided by the caller -- see ``clock_text`` in the start
    page, and the note about screenshots in the desktop layout config.
    """
    return Div(
        icon(Icon.CLOCK, size=15),
        Text(time_text, variant="caption"),
        cls=f"ui-chip {cls}".strip(),
        data_testid="toolbar-clock",
    )


def WeatherChip(
    condition: str,
    temperature: str,
    units: str = "celsius",
    post_url: str = "/desktop/units",
    cls: str = "",
):
    """Toolbar weather. Click to switch between Celsius and Fahrenheit.

    A real ``<button>``, not a Div with a handler. The unit switch is a genuine
    affordance, and a div is invisible to the accessibility tree that most of
    these agents actually act on -- it would be a control only a sighted mouse
    user could find.
    """
    glyph = WEATHER_ICONS.get((condition or "").lower(), Icon.CLOUD)
    other = "Fahrenheit" if units == "celsius" else "Celsius"
    return Button(
        icon(glyph, size=15),
        Text(temperature, variant="caption"),
        cls=f"ui-chip is-button {cls}".strip(),
        title=f"{condition} — click to show {other}",
        aria_label=f"Temperature {temperature}. Switch to {other}.",
        hx_post=post_url,
        hx_target="#desktop-shell",
        hx_swap="outerHTML",
        data_testid="toolbar-weather",
        data_units=units,
    )


def ModeToggle(mode: str, post_url: str = "/desktop/mode", cls: str = ""):
    """Light/dark switch.

    Shows the mode you would move *to*, not the one you are in -- a sun while
    dark, a moon while light. The label says so explicitly, because the glyph
    alone is ambiguous to anything reading the accessibility tree.

    Posts to ``post_url`` and swaps the whole shell, so the new theme's
    ``:root`` block arrives with the response rather than needing a reload.
    """
    going_to = "light" if mode == "dark" else "dark"
    return IconButton(
        Icon.SUN if mode == "dark" else Icon.MOON,
        label=f"Switch to {going_to} mode",
        cls=f"ui-mode-toggle {cls}".strip(),
        hx_post=post_url,
        hx_target="#desktop-shell",
        hx_swap="outerHTML",
        data_testid="mode-toggle",
        data_mode=mode,
    )


def Toolbar(*, left=(), right=(), cls: str = ""):
    """Top bar. Two slots, so callers decide what goes where."""
    return Div(
        Div(*left, cls="ui-toolbar-side"),
        Div(*right, cls="ui-toolbar-side is-right"),
        cls=f"ui-toolbar {cls}".strip(),
        role="toolbar",
        aria_label="Desktop toolbar",
    )


def AppTile(
    title: str,
    href: str,
    glyph=None,
    accent: str | None = None,
    slot: str = "shortcut",
    cls: str = "",
):
    """A shortcut on the desktop surface, or an icon on a phone home screen.

    ``accent`` is a token *name* (e.g. ``color-accent-pink``), not a colour.
    Passing a hex here would survive a theme swap unchanged and stand out as
    the one element that did not repaint.

    ``slot`` namespaces the test id. The phone composition renders tiles in two
    places -- the home grid and the dock -- and two nodes answering to
    ``shortcut-opentodos`` would make every selector ambiguous, for a test and
    for an agent alike.
    """
    style = f"--ui-tile-accent:var(--{accent});" if accent else None
    return A(
        Div(glyph if glyph is not None else icon(Icon.APPS, size=22), cls="ui-tile-glyph"),
        Text(title, variant="caption", cls="ui-tile-label"),
        href=href,
        cls=f"ui-tile {cls}".strip(),
        style=style,
        data_testid=f"{slot}-{title.lower().replace(' ', '-')}",
    )


def LauncherItem(
    title: str,
    href: str,
    app_key: str,
    pinned: bool = False,
    glyph=None,
    pin_url: str = "/desktop/pin",
    cls: str = "",
):
    """A row in the launcher menu, with a pin control revealed on hover.

    The pin is a real ``<button>`` that exists in the DOM whether or not it is
    visible -- hover only changes its opacity. Rendering it on hover instead
    would make it unreachable to anything that cannot hover, including keyboard
    users and any agent acting off the accessibility tree. It stays reachable
    by keyboard, and CSS keeps it visible while focused.
    """
    return Div(
        A(
            Div(glyph if glyph is not None else icon(Icon.APPS, size=18), cls="ui-launcher-glyph"),
            Text(title, variant="body"),
            href=href,
            cls="ui-launcher-link",
        ),
        IconButton(
            Icon.PIN_FILLED if pinned else Icon.PIN,
            label=f"{'Unpin' if pinned else 'Pin'} {title} {'from' if pinned else 'to'} desktop",
            size=16,
            cls="ui-pin-btn" + (" is-pinned" if pinned else ""),
            hx_post=f"{pin_url}/{app_key}",
            hx_target="#desktop-shell",
            hx_swap="outerHTML",
            data_testid=f"pin-{app_key}",
            data_pinned=str(pinned).lower(),
        ),
        cls=f"ui-launcher-item {cls}".strip(),
    )


def LauncherButton(open: bool = False, toggle_url: str = "/desktop/launcher", cls: str = ""):
    """The button that opens the launcher, on its own.

    Split out from :func:`LauncherMenu` because the phone renders the button
    and the menu in different places in the DOM -- see :func:`LauncherSheet`.
    """
    return IconButton(
        Icon.APPS,
        label="Close app launcher" if open else "Open app launcher",
        size=20,
        cls=_cls("ui-launcher-btn" + (" is-open" if open else ""), cls),
        hx_post=toggle_url,
        hx_target="#desktop-shell",
        hx_swap="outerHTML",
        data_testid="launcher-button",
        aria_expanded=str(open).lower(),
    )


def LauncherMenu(*items, open: bool = False, toggle_url: str = "/desktop/launcher", cls: str = ""):
    """The desktop launcher: a button plus the popover panel it opens.

    Open/closed is server state swapped over htmx rather than a CSS-only
    disclosure. That costs a request, and buys two things: the panel's contents
    are always current after a pin, and whether it is open is visible in the
    DOM, so a test or an agent can tell without inspecting computed styles.
    """
    return Div(
        LauncherButton(open=open, toggle_url=toggle_url),
        Div(
            *items,
            cls="ui-launcher-panel",
            role="menu",
            aria_label="Applications",
            data_testid="launcher-panel",
        ) if open else None,
        cls=f"ui-launcher {cls}".strip(),
    )


def LauncherSheet(*items, open: bool = False, toggle_url: str = "/desktop/launcher"):
    """The phone app drawer: a full-screen sheet, not a popover.

    Two reasons it is a sheet rather than the desktop's anchored panel.

    A popover anchored above the dock has to fit in whatever space is left on a
    844px-tall screen, and it is one `overflow` away from being clipped by an
    ancestor -- the failure mode is silent, because the markup is correct and
    `aria-expanded` flips either way. A sheet owns the screen and cannot be
    clipped or mispositioned. It is also what a phone user expects: this is the
    app-drawer pattern, not a desktop menu shrunk down.

    **Render this at the shell root, never inside the dock.** `.ui-phone-dock`
    sets `backdrop-filter`, and any of filter/backdrop-filter/transform makes an
    element the containing block for `position: fixed` descendants -- inside the
    dock, `inset: 0` would resolve to the dock's own ~88px box instead of the
    viewport, and the sheet would render as a sliver behind the icons.

    Returns ``None`` when closed so the closed state adds nothing to the DOM.
    """
    if not open:
        return None
    return Div(
        # The scrim is a real button, not a bare div: tapping outside to close
        # is the expected gesture, and as a button it is reachable by keyboard
        # and named in the accessibility tree an agent reads.
        Button(
            cls="ui-launcher-scrim",
            hx_post=toggle_url,
            hx_target="#desktop-shell",
            hx_swap="outerHTML",
            aria_label="Close app launcher",
            data_testid="launcher-scrim",
            tabindex="-1",
        ),
        Div(
            Div(
                Text("Apps", variant="title"),
                IconButton(
                    Icon.CLOSE,
                    label="Close app launcher",
                    size=20,
                    hx_post=toggle_url,
                    hx_target="#desktop-shell",
                    hx_swap="outerHTML",
                    data_testid="launcher-close",
                ),
                cls="ui-launcher-sheet-head",
            ),
            Div(
                *items,
                cls="ui-launcher-sheet-list",
                role="menu",
                aria_label="Applications",
                data_testid="launcher-panel",
            ),
            cls="ui-launcher-sheet",
        ),
        cls="ui-launcher-overlay",
        data_testid="launcher-overlay",
    )
