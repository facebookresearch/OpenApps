#!/usr/bin/env python3
"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Generate the app icons under ``src/open_apps/apps/assets/icons/``.

The SVG files there are checked in. Nothing needs this script to run the apps.
It is the source those files come from, kept so that a glyph is drawn once and
then written into both sets:

* ``real_icons/``     each app on its own colored gradient tile
* ``real_icons_bw/``  the same glyphs on one graphite tile, which is the set a
                      ``bw`` theme picks (see ``icon_for`` in
                      ``apps/start_page/main.py``)

Hand-editing one SVG would let the color and greyscale sets drift apart. Edit
the glyph here and re-run:

    uv run scripts/build_app_icons.py

The style follows Black Tie (geometric, one even stroke weight, generous
negative space, few details), drawn from scratch rather than copied. Why the
icons are files and not inline markup: every consumer (the dock, the title
bar, app headers, gallery tiles) takes an icon *path* from config, and a
content pack or ``broken_logos`` shuffle swaps paths, not markup.

No fonts, filters or external references: an ``<img>`` SVG cannot load them
on a node with no outbound network, and filters rasterize differently across
Chromium builds, which would make screenshot comparison flaky.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ICON_DIR = Path(__file__).resolve().parents[1] / "src/open_apps/apps/assets/icons"

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


def _svg(body: str, top: str, bottom: str, *, round_tile: bool = False) -> str:
    knock = "url(#t)"
    tile = (
        '<circle cx="32" cy="32" r="32" fill="url(#t)"/>'
        if round_tile
        # ~22% corner radius: the proportion app-icon masks use.
        else '<rect width="64" height="64" rx="14" fill="url(#t)"/>'
    )
    # A hairline edge, so a dark tile (the bank's navy, the bw graphite) still
    # separates from a dark dock or title bar. No gloss band: it read as
    # decoration and muddied the glyph at 24px.
    edge = (
        '<circle cx="32" cy="32" r="31.5" fill="none" stroke="#fff" stroke-opacity=".12"/>'
        if round_tile
        else '<rect x=".5" y=".5" width="63" height="63" rx="13.5" fill="none" '
        'stroke="#fff" stroke-opacity=".12"/>'
    )
    glyph = "\n".join(line.strip() for line in body.strip().splitlines())
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">\n'
        "<defs>"
        # userSpaceOnUse, so a knock-out element filled with url(#t) matches
        # the tile pixel-for-pixel wherever it sits.
        f'<linearGradient id="t" x1="0" y1="0" x2="0" y2="64" gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/>'
        "</linearGradient>"
        "</defs>\n"
        f"{tile}{edge}\n"
        # Glyphs are drawn on the 14-50 safe area and shrunk about the centre,
        # which leaves the tile some air at large sizes. The stroke is divided
        # by the same factor so it still renders at STROKE.
        f'<g transform="translate(32 32) scale({GLYPH_SCALE}) translate(-32 -32)" '
        f'fill="none" stroke="{INK}" stroke-width="{STROKE / GLYPH_SCALE:.2f}" '
        'stroke-linecap="round" stroke-linejoin="round">\n'
        f"{glyph.replace('{knock}', knock)}\n"
        "</g>\n</svg>\n"
    )


def main() -> None:
    color_dir, bw_dir = ICON_DIR / "real_icons", ICON_DIR / "real_icons_bw"
    color_dir.mkdir(parents=True, exist_ok=True)
    bw_dir.mkdir(parents=True, exist_ok=True)
    for icon in ICONS:
        (color_dir / f"{icon.file}.svg").write_text(_svg(icon.glyph, icon.top, icon.bottom))
        (bw_dir / f"{icon.file}.svg").write_text(_svg(icon.glyph, *BW_TILE))
    for name, body in AVATARS.items():
        (bw_dir / f"{name}.svg").write_text(_svg(body, "#9aa1ab", "#5f6670", round_tile=True))
    print(f"wrote {len(ICONS) * 2 + len(AVATARS)} icons under {ICON_DIR}")


if __name__ == "__main__":
    main()
