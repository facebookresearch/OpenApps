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
the glyph in ``src/open_apps/app_icons.py`` and re-run:

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

from pathlib import Path

# The geometry and palette live in the package, so the launcher's inline
# glyphs (open_apps.app_icons.app_glyph) and these files cannot drift apart.
from open_apps.app_icons import AVATARS, BW_TILE, GLYPH_SCALE, ICONS, INK, STROKE

ICON_DIR = Path(__file__).resolve().parents[1] / "src/open_apps/apps/assets/icons"


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
