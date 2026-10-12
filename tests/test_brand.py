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

    def test_carries_the_dark_mode_flip_hook(self):
        """styles.py flips this class; without it dark mode silently does nothing."""
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
