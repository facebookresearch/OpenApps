"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Invariants for the OpenApps wordmark.

The mark's radius is *solved*, not chosen: given the wedge angle, the ray reach
and the stroke-to-radius ratio, exactly one radius makes the glyph span its
viewBox exactly. That is what lets ``height=34`` on the ``<svg>`` mean "render
the mark 34px tall", so the lockup can be sized to match the circular toolbar
buttons next to it.

Nothing enforces that relationship at runtime. Nudge the wedge wider or the
rays longer without re-solving and the mark quietly stops filling its box --
which does not raise, does not fail any render, and shows up only as a logo
that no longer lines up with the button beside it. These tests re-derive the
constants and fail when they drift apart.
"""

import math
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from open_apps.ui import brand


def mark_bounds() -> tuple[float, float]:
    """Top and bottom of the drawn mark in viewBox units, strokes included."""
    half_stroke = float(brand._MARK_STROKE) / 2
    ray_dip = brand._MARK_R * brand._RAY_REACH * math.sin(math.radians(brand._GAP_FROM))
    return (
        brand._MARK_CY - brand._MARK_R - half_stroke,
        brand._MARK_CY + ray_dip + half_stroke,
    )


class TestMarkFillsItsBox:

    def test_mark_spans_the_full_viewbox_height(self):
        """``height=N`` only means "N pixels tall" if the glyph fills the box."""
        top, bottom = mark_bounds()
        assert top == pytest.approx(0.0, abs=0.05), f"mark starts at y={top:.3f}, not 0"
        assert bottom == pytest.approx(brand._VIEWBOX_H, abs=0.05), (
            f"mark ends at y={bottom:.3f}, not {brand._VIEWBOX_H:g}"
        )

    def test_radius_is_the_solved_one(self):
        """Re-derive R from the other constants and check it matches.

        If this fails, something was retuned without re-solving. The fix is to
        use the value this test computes, not to widen the tolerance.
        """
        span_in_radii = (
            1
            + brand._RAY_REACH * math.sin(math.radians(brand._GAP_FROM))
            + brand._STROKE_RATIO
        )
        assert brand._MARK_R == pytest.approx(
            brand._VIEWBOX_H / span_in_radii, abs=0.01
        )

    def test_stroke_tracks_the_radius(self):
        assert float(brand._MARK_STROKE) == pytest.approx(
            brand._MARK_R * brand._STROKE_RATIO, abs=0.01
        )


class TestWedge:

    def test_wedge_is_centred_on_straight_down(self):
        """Symmetry is what makes the two rays read as one V."""
        rays = [
            math.degrees(math.atan2(b[1] - brand._MARK_CY, b[0] - brand._MARK_CX)) % 360
            for _, b in brand._mark_geometry()["rays"]
        ]
        assert sum(angle - 90 for angle in rays) == pytest.approx(0.0, abs=1e-9)

    def test_rays_overshoot_the_ring(self):
        """Ending flush would close the wedge back into a plain pie slice."""
        assert brand._RAY_REACH > 1.0


class TestMarkup:

    def test_is_well_formed_svg(self):
        ET.fromstring(brand.wordmark_markup())

    def test_has_one_arc_and_two_rays(self):
        assert brand.wordmark_markup().count("<path") == 3

    def test_mark_is_grouped_apart_from_the_word(self):
        """CSS can target the mark without touching the word."""
        assert 'class="ui-wordmark-mark"' in brand.wordmark_markup()

    def test_gradient_runs_bottom_to_top(self):
        """The brand wash rises from the ray tips; reversed, it would sit on the ring."""
        markup = brand.wordmark_markup()
        assert f'y1="{brand._VIEWBOX_H:g}"' in markup and 'y2="0"' in markup

    def test_word_is_not_gradient_filled(self):
        """Lettering stays flat currentColor -- a gradient costs legibility here."""
        text = brand.wordmark_markup().split("<text")[1]
        assert 'fill="currentColor"' in text

    def test_height_argument_reaches_the_svg(self):
        assert 'height="34"' in brand.wordmark_markup(height=34)


class TestLogomark:
    """The mark without the word, for the docs header and favicon."""

    def test_is_well_formed_square_svg(self):
        root = ET.fromstring(brand.logomark_markup())
        assert root.get("viewBox") == f"0 0 {brand._VIEWBOX_H:g} {brand._VIEWBOX_H:g}"
        assert root.get("width") == root.get("height")

    def test_is_the_wordmark_mark_without_the_word(self):
        mark = brand.logomark_markup()
        assert "<text" not in mark
        # Same arc and rays as the lockup: the paths are byte-identical.
        def paths(svg):
            return [p.get("d") for p in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}path")]
        assert paths(mark) == paths(brand.wordmark_markup())
        assert len(paths(mark)) == 3

    def test_does_not_share_the_wordmarks_gradient_id(self):
        # Two elements with one id resolve to the first one's stops.
        assert brand._GRADIENT_ID not in brand.logomark_markup()

    def test_mark_is_grouped_like_the_wordmarks(self):
        assert 'class="ui-wordmark-mark"' in brand.logomark_markup()


class TestDocsAssetsAreCurrent:
    """The docs header icon and favicon are generated from this module."""

    ROOT = Path(__file__).resolve().parents[1]

    @pytest.mark.parametrize("rel, render", [
        (brand.DOCS_ICON_TEMPLATE, brand.docs_icon_template),
        (brand.DOCS_FAVICON, brand.docs_favicon),
    ])
    def test_committed_copy_matches(self, rel, render):
        path = self.ROOT / rel
        assert path.read_text(encoding="utf-8") == render(), (
            f"{rel} is stale; regenerate with `uv run -m open_apps.ui.brand`"
        )

    def test_favicon_has_no_unresolvable_colors(self):
        # Nothing styles a favicon, so currentColor would render black.
        assert "currentColor" not in brand.docs_favicon()

    def test_mkdocs_uses_the_override_and_favicon(self):
        config = (self.ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        assert "custom_dir: overrides" in config
        assert "favicon: assets/brand/logomark.svg" in config
        assert brand.DOCS_ICON_TEMPLATE.startswith("overrides/")
        assert brand.DOCS_FAVICON == "docs/assets/brand/logomark.svg"


def test_composer_sits_under_manual_installation_in_the_nav():
    config = (Path(__file__).resolve().parents[1] / "mkdocs.yml").read_text(encoding="utf-8")
    nav = [line.strip() for line in config.split("\nnav:\n", 1)[1].splitlines()
           if line.strip().startswith("- ")]
    install = next(i for i, l in enumerate(nav) if "installation.md" in l)
    assert "composer.md" in nav[install + 1]


def test_mark_stays_upright_in_dark_mode():
    # A dark-mode scaleY(-1) flip read as the logo being upside down, on the
    # launch screen and in the docs header alike.
    from open_apps.ui import styles
    css = " ".join(str(v) for v in vars(styles).values() if isinstance(v, str))
    assert "scaleY" not in css
    assert "scaleY" not in brand.docs_icon_template()
