"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The bare app glyphs the launcher menu draws inline.

They come from the same geometry as the tile SVGs in apps/assets/icons/, but
render with no tile, in ``currentColor``, with knock-outs as real masks -- so
they follow the shell's light/dark mode and sit on the launcher's translucent
panel without a box behind them.
"""

import re
import xml.etree.ElementTree as ET

import pytest

from open_apps.app_icons import GLYPHS, app_glyph_markup

SVG = "{http://www.w3.org/2000/svg}"


@pytest.mark.parametrize("name", sorted(GLYPHS))
def test_every_glyph_renders_as_valid_tileless_svg(name):
    markup = app_glyph_markup(name, size=24)
    root = ET.fromstring(markup)
    assert root.get("aria-hidden") == "true"
    assert root.get("stroke") == "currentColor"
    # No tile: no gradient, no 64x64 background, and nothing painted white.
    assert root.find(f".//{SVG}linearGradient") is None
    assert "#fff" not in re.sub(r"<mask\b.*?</mask>", "", markup)
    assert 'width="64" height="64" rx=' not in markup


@pytest.mark.parametrize("name", ["calendar", "maps"])
def test_knockouts_become_masks_not_fills(name):
    """A knock-out was painted in the tile's gradient; with no tile it has to
    erase, or it would show as a coloured blob on the menu."""
    markup = app_glyph_markup(name)
    assert "KNOCK" not in markup and "url(#t)" not in markup
    mask_ids = re.findall(r'<mask id="([^"]+)"', markup)
    assert mask_ids
    for mask_id in mask_ids:
        assert f'mask="url(#{mask_id})"' in markup


def test_mask_ids_are_namespaced_per_glyph():
    ids = [i for name in GLYPHS for i in re.findall(r'<mask id="([^"]+)"', app_glyph_markup(name))]
    assert len(ids) == len(set(ids))


def test_unknown_glyph_is_none_so_callers_can_fall_back():
    assert app_glyph_markup("not-an-app") is None
