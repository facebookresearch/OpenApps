"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Atoms -- the smallest styled elements.

An atom sets appearance and nothing else. It has no layout opinion about where
it sits, no knowledge of the page, and no state. Composition is the caller's
job, or a molecule's.

Each returns a FastHTML element carrying a ``ui-*`` class; the CSS for those
classes lives in :mod:`open_apps.ui.styles` and is written entirely against
``var(--token)``. Nothing here emits an inline colour, so a theme swap or a
light/dark toggle repaints all of it without re-rendering anything.

``cls`` is appended rather than replaced on every atom, so callers can add a
layout or test-hook class without losing the component's own styling.
"""
from __future__ import annotations

from fasthtml.common import Button, Div, Span

from open_apps.icons import Icon, icon


def _cls(base: str, extra: str = "") -> str:
    """Append caller classes to a component's own, skipping empties."""
    return f"{base} {extra}".strip() if extra else base


def Surface(*children, elevated: bool = False, cls: str = "", **kw):
    """A panel: token background, border and corner radius.

    ``elevated`` switches from the page background to the surface token, which
    is what distinguishes a card from the plane it sits on. In dark mode that
    token is a translucent white tint, so an elevated surface reads as lifted
    rather than as a different colour.
    """
    return Div(*children, cls=_cls("ui-surface" + (" is-elevated" if elevated else ""), cls), **kw)


def Stack(*children, direction: str = "column", gap: int = 1, align: str = "stretch", cls: str = "", **kw):
    """Flex container. The only layout primitive here, deliberately.

    ``gap`` is in multiples of the ``--space`` token rather than pixels, so
    spacing scales with the theme instead of drifting from it.
    """
    style = f"--ui-stack-gap:{gap};--ui-stack-align:{align};"
    base = "ui-stack" + ("-row" if direction == "row" else "-col")
    return Div(*children, cls=_cls(base, cls), style=style, **kw)


def Text(content, variant: str = "body", cls: str = "", **kw):
    """Typographic scale. ``variant``: title | body | caption.

    An unknown variant falls back to body rather than rendering unstyled --
    a typo should look slightly wrong, not invisible.
    """
    variant = variant if variant in ("title", "body", "caption") else "body"
    return Span(content, cls=_cls(f"ui-text is-{variant}", cls), **kw)


def UIButton(label, variant: str = "primary", cls: str = "", **kw):
    """Button. ``variant``: primary | neutral | danger | ghost.

    Named ``UIButton`` because ``Button`` is already FastHTML's raw element and
    shadowing it inside app modules that do ``from fasthtml.common import *``
    would be a genuinely confusing bug to chase.

    Every ``hx_*`` keyword passes straight through, so this stays usable as an
    htmx trigger without wrapping.
    """
    variant = variant if variant in ("primary", "neutral", "danger", "ghost") else "primary"
    return Button(label, cls=_cls(f"ui-btn is-{variant}", cls), **kw)


def IconButton(name: Icon | str, label: str, size: int = 18, variant: str = "ghost", cls: str = "", **kw):
    """Icon-only button.

    ``label`` is required and becomes ``aria-label``. The icons themselves are
    ``aria-hidden``, so without it the control is nameless -- invisible to a
    screen reader, and to an agent reading the accessibility tree, which is the
    observation most of these agents actually act on.
    """
    return Button(
        icon(name, size=size),
        cls=_cls(f"ui-icon-btn is-{variant}", cls),
        aria_label=label,
        **kw,
    )


def Badge(content, tone: str = "neutral", cls: str = "", **kw):
    """Small status pill. ``tone``: neutral | success | warning | danger."""
    tone = tone if tone in ("neutral", "success", "warning", "danger") else "neutral"
    return Span(content, cls=_cls(f"ui-badge is-{tone}", cls), **kw)


def Divider(cls: str = "", **kw):
    """One-pixel rule in the border token."""
    return Div(cls=_cls("ui-divider", cls), **kw)
