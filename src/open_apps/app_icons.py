"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The app glyphs, drawn once and rendered two ways.

* As **tiles** -- each glyph in white on its app's gradient square, written to
  ``apps/assets/icons/`` by ``scripts/build_app_icons.py`` and referenced by
  path from config (the dock, the title bar, app headers, gallery tiles).
* As **bare glyphs** -- :func:`app_glyph`, inline ``<svg>`` with no tile and
  ``currentColor`` ink, for the launcher menu. Inline rather than another set
  of files because an ``<img>`` cannot inherit the text colour: the glyph has
  to be dark in light mode and light in dark mode, following a live toggle.

Keeping the geometry here, in the package, is what stops the two from
drifting: the script imports it rather than holding its own copy.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from fasthtml.common import NotStr

# One stroke weight for every glyph, the defining trait of the style. 3.5 on a
# 64 grid is ~0.9px at the 16px title-bar size: the thinnest that still reads
# there, and not heavy at the 64px gallery size.
STROKE = 3.5
GLYPH_SCALE = 0.92

# Glyph ink. Elements tagged {knock} are filled with the tile's own gradient,
# so they read as cut-outs (the pin's hole, the bubble's dots) without masks.
INK = "#ffffff"

# The greyscale set's tile. One fill for every app, as a bw theme intends:
# the glyph alone has to tell the apps apart.
BW_TILE = ("#4b4d52", "#2e3034")


@dataclass(frozen=True)
class AppIcon:
    file: str
    top: str  # gradient stop at the top of the tile
    bottom: str  # and at the bottom, a few steps darker
    glyph: str  # SVG elements; `{knock}` -> the tile gradient


# Each hue comes from the app's subject (fountain-pen ink for the todo list,
# a tear-off page for the calendar, kraft paper for the shop bag, banknote
# navy for the bank) and the eight are spread around the wheel so no two apps
# share one. Every gradient is the same small vertical value step with no hue
# shift: shading, not a colour wash.
ICONS = [
    # Checklist: two ticked rows and one open box.
    AppIcon("todo", "#3457ae", "#26428a", """
      <path d="M16 21.5l3.5 3.5 6-6.5M16 33.5l3.5 3.5 6-6.5"/>
      <rect x="16.5" y="42" width="8" height="8" rx="2"/>
      <path d="M32 22.5h16M32 34.5h16M32 46h12"/>"""),
    # Calendar page: solid header band, binder rings, a grid of days.
    AppIcon("calendar", "#c9343f", "#a82632", """
      <rect x="15" y="18" width="34" height="31" rx="5"/>
      <path d="M15 27h34" />
      <path d="M17 18h30a3 3 0 0 1 3 3v6H14v-6a3 3 0 0 1 3-3z" fill="#fff" stroke="none"/>
      <path d="M24 14v7M40 14v7" stroke="{knock}" stroke-width="7.5"/>
      <path d="M24 14v7M40 14v7"/>
      <g fill="#fff" stroke="none">
        <rect x="20" y="31" width="6" height="6" rx="1.5"/>
        <rect x="29" y="31" width="6" height="6" rx="1.5"/>
        <rect x="38" y="31" width="6" height="6" rx="1.5"/>
        <rect x="20" y="40" width="6" height="6" rx="1.5"/>
        <rect x="29" y="40" width="6" height="6" rx="1.5"/>
      </g>"""),
    # Speech bubble with a typing indicator.
    AppIcon("messages", "#1a9ca3", "#0f7c84", """
      <path d="M32 16c9.4 0 17 6.3 17 14.5S41.4 45 32 45c-1.9 0-3.7-.25-5.4-.7L18 48l2.4-7.2C17.3 38.3 15 34.6 15 30.5 15 22.3 22.6 16 32 16z"/>
      <g fill="#fff" stroke="none">
        <circle cx="24.5" cy="30.5" r="2.5"/>
        <circle cx="32" cy="30.5" r="2.5"/>
        <circle cx="39.5" cy="30.5" r="2.5"/>
      </g>"""),
    # Folded map with a pin standing on the middle panel.
    AppIcon("maps", "#4f9547", "#3a7835", """
      <path d="M15 22l10-4 14 4 10-4v26l-10 4-14-4-10 4z"/>
      <path d="M25 18v26M39 22v26"/>
      <path d="M32 13a8 8 0 0 1 8 8c0 5.6-8 13-8 13s-8-7.4-8-13a8 8 0 0 1 8-8z" fill="#fff" stroke="{knock}" stroke-width="3"/>
      <circle cx="32" cy="21" r="3" fill="{knock}" stroke="none"/>"""),
    # Angle brackets and a slash.
    AppIcon("code", "#6447a6", "#4d3388", """
      <path d="M24 21l-10 11 10 11M40 21l10 11-10 11M35.5 17l-7 30"/>"""),
    # Shopping bag.
    AppIcon("shop", "#ae814b", "#8e6535", """
      <path d="M18 25h28l-1.6 19.6A4 4 0 0 1 40.4 48H23.6a4 4 0 0 1-4-3.4z"/>
      <path d="M25 29v-6a7 7 0 0 1 14 0v6"/>"""),
    # Component swatches: three squares and a solid circle.
    AppIcon("uilibrary", "#cc4f8a", "#ac3a70", """
      <rect x="16" y="16" width="13" height="13" rx="3"/>
      <rect x="35" y="16" width="13" height="13" rx="3"/>
      <rect x="16" y="35" width="13" height="13" rx="3"/>
      <circle cx="41.5" cy="41.5" r="7.5" fill="#fff" stroke="none"/>"""),
    # Wallet with a card tucked behind the flap.
    AppIcon("wallet", "#26375a", "#17233d", """
      <path d="M19 22l19-6.5a3 3 0 0 1 4 2.8V22"/>
      <rect x="14" y="22" width="36" height="26" rx="5"/>
      <path d="M50 30h-9a5 5 0 0 0 0 10h9"/>
      <circle cx="41.5" cy="35" r="2.2" fill="#fff" stroke="none"/>"""),
    # Folder with a dog-eared page standing in it; the folder's front is
    # filled with the tile so it hides the page's lower half. Terracotta --
    # a manila folder's warmth, kept clear of the shop's kraft.
    AppIcon("files", "#d0682c", "#ad5320", """
      <path d="M22 28V17a3 3 0 0 1 3-3h13l6 6v8"/>
      <path d="M38 14v6h6"/>
      <path d="M14 27a4 4 0 0 1 4-4h8.5l4 4.5H46a4 4 0 0 1 4 4V46a4 4 0 0 1-4 4H18a4 4 0 0 1-4-4z" fill="{knock}"/>"""),
    # Address book: a card with tab marks on its spine and a person on the
    # page. Steel blue -- index-card stock -- a step lighter than the bank.
    AppIcon("contacts", "#58708a", "#43586f", """
      <rect x="19" y="14" width="30" height="36" rx="5"/>
      <path d="M14.5 23h4.5M14.5 32h4.5M14.5 41h4.5"/>
      <circle cx="34" cy="27.5" r="5.5"/>
      <path d="M24.5 42.5c1.2-5.2 5-8 9.5-8s8.3 2.8 9.5 8"/>"""),
]

# Messenger avatars. Round rather than a tile: an avatar is a person, not an
# app, and a circle is what every chat list draws.
AVATARS = {
    "user": """
      <circle cx="32" cy="25" r="8"/>
      <path d="M17 47c1.6-7.4 7.6-12 15-12s13.4 4.6 15 12"/>""",
    "group": """
      <circle cx="25" cy="25" r="6.5"/>
      <path d="M12.5 45c1.3-6.6 6.2-10.5 12.5-10.5S36.2 38.4 37.5 45"/>
      <circle cx="41" cy="23" r="5.5"/>
      <path d="M38.5 33.4c1-.3 1.6-.4 2.5-.4 5.6 0 9.9 3.6 11 9.5"/>""",
}



#: Glyph by icon file stem ("todo", "code", "wallet", ...). Keyed by file
#: rather than app key because the file is what config names: a launcher row
#: finds its glyph from the same icon path the dock uses.
GLYPHS: dict[str, str] = {icon.file: icon.glyph for icon in ICONS}

# The glyphs sit on the 14-50 band of the 64 grid (the tile's safe area). The
# bare glyph crops to it, so a 24px glyph is 24px of ink, not 24px of tile.
_BARE_VIEWBOX = "11 11 42 42"


def _bare_body(name: str, glyph: str) -> str:
    """The glyph's elements with tile paint swapped for bare-glyph paint.

    White ink becomes ``currentColor``. A ``{knock}`` element -- painted in
    the tile's own gradient so it reads as a cut-out -- has no tile to borrow
    a colour from here, and the launcher sits on a frosted, translucent panel
    that no flat fill would match. So each knock *erases* instead: everything
    drawn before it is wrapped in a group masked by its shape, which is the
    painter's-order meaning the tile version has, made truly transparent.
    """
    root = ET.fromstring(f"<g>{glyph.replace('{knock}', 'KNOCK')}</g>")
    drawn: list[str] = []
    defs: list[str] = []

    def paint(el: ET.Element) -> str:
        for attr in ("fill", "stroke"):
            if el.get(attr) == "#fff":
                el.set(attr, "currentColor")
        for child in el:
            paint(child)
        return ET.tostring(el, encoding="unicode")

    for el in list(root):
        knocks = [a for a in ("fill", "stroke") if el.get(a) == "KNOCK"]
        if not knocks:
            drawn.append(paint(el))
            continue
        # The erasing shape: the knock paint in black on a white mask, every
        # other paint on the element switched off.
        eraser = ET.fromstring(ET.tostring(el, encoding="unicode"))
        for attr in ("fill", "stroke"):
            eraser.set(attr, "#000" if attr in knocks else "none")
        if "stroke" in knocks and eraser.get("stroke-width") is None:
            eraser.set("stroke-width", str(STROKE))
        mask_id = f"oa-glyph-{name}-k{len(defs)}"
        defs.append(
            f'<mask id="{mask_id}" maskUnits="userSpaceOnUse" x="0" y="0" width="64" height="64">'
            f'<rect width="64" height="64" fill="#fff"/>'
            f"{ET.tostring(eraser, encoding='unicode')}</mask>"
        )
        drawn = [f'<g mask="url(#{mask_id})">{"".join(drawn)}</g>']
        # Whatever the element paints in ink is still drawn, on top of the cut.
        for attr in knocks:
            el.set(attr, "none")
        if any(el.get(a) not in (None, "none") for a in ("fill", "stroke")) or (
            el.get("stroke") is None and "stroke" not in knocks
        ):
            drawn.append(paint(el))
    return (f"<defs>{''.join(defs)}</defs>" if defs else "") + "".join(drawn)


def app_glyph_markup(name: str, size: int = 24, cls: str = "") -> str | None:
    """Inline ``<svg>`` for one app's bare glyph, or ``None`` if ``name`` has
    none (a content pack pointing an app at a custom icon file).

    Same conventions as :mod:`open_apps.icons`: ``currentColor`` ink so it
    follows the theme, ``aria-hidden`` because the row's text already names
    the app and a decorative node would only add noise to the agent's tree.
    """
    glyph = GLYPHS.get(name)
    if glyph is None:
        return None
    class_attr = f' class="{cls}"' if cls else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_BARE_VIEWBOX}"'
        f' width="{size}" height="{size}"{class_attr}'
        f' fill="none" stroke="currentColor" stroke-width="{STROKE}"'
        f' stroke-linecap="round" stroke-linejoin="round"'
        f' aria-hidden="true" focusable="false">{_bare_body(name, glyph)}</svg>'
    )


def app_glyph(name: str, size: int = 24, cls: str = "") -> NotStr | None:
    """:func:`app_glyph_markup` wrapped so FastHTML emits it as markup."""
    markup = app_glyph_markup(name, size=size, cls=cls)
    return NotStr(markup) if markup is not None else None
