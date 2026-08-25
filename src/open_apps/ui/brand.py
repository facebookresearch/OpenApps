"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The OpenApps wordmark.

Inline SVG rather than a font file. A logo typeface would mean shipping and
licensing a binary, or fetching one -- and the eval nodes have no outbound
network, so a webfont would silently fail and leave the shell headed by
fallback text at the wrong weight.

The mark is a ring with a wedge cut out of the lower right and two rays running
from the centre out through the wedge's edges -- one straight down, one
down-and-right at 45 degrees. It reads as a stylised Q.

Two further details worth knowing:

* **Everything strokes ``currentColor``** -- no gradient, one flat colour. In
  the toolbar that resolves to ``--color-fg``, which is near-black
  (``#1c2b33``) in light mode and white in dark. Hardcoding ``#000`` would give
  a literally black mark that disappears against the dark theme's background,
  so "black" here means "the text colour, which is black in light mode".
* **The lettering is SVG ``<text>``** in the theme's own font stack, not traced
  outlines. It stays selectable and searchable, it costs a few hundred bytes
  rather than a few kilobytes of path data, and it inherits ``currentColor`` so
  the word is legible in both modes while only the mark carries the gradient.
"""
from __future__ import annotations

import math

from fasthtml.common import NotStr

# --- the mark --------------------------------------------------------------
# A ring with a wedge cut out of the bottom and two rays running from the centre
# out through the wedge's edges, forming an inverted V that opens downward.
#
# Not a chord cutting a circle, which is what earlier versions were, and which
# carried an awkward constraint: a chord's offset from the centre is
# perpendicular to the chord, so a 45-degree "/" chord could only ever open the
# lower-right or the upper-left. Rays from the centre have no such restriction
# -- the opening goes wherever the two angles say.
#
# Everything is derived from the constants below. Arc endpoints and the SVG
# large-arc/sweep flags are exactly what rots when someone nudges the radius and
# hand-edits the path.
# The mark FILLS the viewBox vertically rather than sitting inset in it. That
# is what lets `height=N` on the <svg> mean "render the mark N pixels tall", so
# the lockup can be sized to match the circular toolbar buttons beside it
# without anyone having to work out the inset by hand.
#
# These are solved, not chosen: given the wedge angle, the ray reach and a
# stroke-to-radius ratio, there is exactly one radius that makes the glyph span
# the box. See tests/test_brand.py, which re-derives them.
_VIEWBOX_H = 28.0
_STROKE_RATIO = 0.343          # stroke width as a fraction of the radius
_MARK_R = 11.993
_MARK_STROKE = f"{_MARK_R * _STROKE_RATIO:.3f}"
_MARK_CX = _MARK_CY = 14.05    # centred so the ring's left/top edge sits at 0

#: How far the rays run past the ring, as a multiple of the radius. They have to
#: overshoot: ending flush would close the wedge back up into a plain pie slice.
_RAY_REACH = 1.25

#: The ring is open between these two angles. SVG degrees with y growing
#: downward, so 90 is six o'clock. The wedge is centred on straight-down and
#: symmetric about it, which is what makes the two rays read as one inverted V
#: rather than as a stem plus a diagonal.
#:
#: 75 degrees wide, not the 45 it started at. At 45 the two rays sat close
#: enough together that the opening between them read as a slot; the inverted V
#: only becomes legible once the wedge is open enough to show daylight under
#: the peak.
_GAP_FROM, _GAP_TO = 52.5, 127.5

#: Where the word starts, and how wide the whole lockup is. The mark occupies
#: 0..28 horizontally once it fills the box, so the word clears it with a gap.
_TEXT_X = 34
_VIEWBOX_W = 132

#: Single gradient id, so one wordmark per page. A second instance would
#: resolve to the first one's stops.
_GRADIENT_ID = "oa-wordmark-gradient"


def _polar(angle_deg: float, reach: float = 1.0) -> tuple[float, float]:
    """A point at ``angle_deg`` on (or beyond) the ring."""
    r = _MARK_R * reach
    rad = math.radians(angle_deg)
    return (_MARK_CX + r * math.cos(rad), _MARK_CY + r * math.sin(rad))


def _mark_geometry() -> dict:
    """Arc endpoints, ray endpoints and arc flags, from the constants above."""
    # The arc runs the long way round -- from the far edge of the wedge,
    # clockwise through the left and top, back to the near edge.
    span = (_GAP_FROM + 360 - _GAP_TO) % 360
    return {
        "arc_start": _polar(_GAP_TO),
        "arc_end": _polar(_GAP_FROM),
        "rays": [
            (( _MARK_CX, _MARK_CY), _polar(a, _RAY_REACH))
            for a in (_GAP_TO, _GAP_FROM)
        ],
        "large_arc": 1 if span > 180 else 0,
        # sweep=1 is clockwise on screen, since y grows downward.
        "sweep": 1,
    }


def wordmark_markup(height: int = 24, label: str = "OpenApps") -> str:
    """Raw ``<svg>`` for the lockup: the wedged ring plus the word."""
    g = _mark_geometry()
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' viewBox="0 0 {_VIEWBOX_W} {_VIEWBOX_H:g}"'
        f' height="{height}" role="img" aria-label="{label}"'
        f' class="ui-wordmark">'
        # A subtle brand wash rising from the bottom: the ray tips pick up the
        # accent and it has faded into the text colour by roughly a third of the
        # way up. userSpaceOnUse rather than the default objectBoundingBox --
        # the bounding box of a *stroked* path is measured inconsistently across
        # renderers, so tying the stops to viewBox coordinates keeps the fade
        # landing in the same place everywhere.
        f'<defs>'
        f'<linearGradient id="{_GRADIENT_ID}" gradientUnits="userSpaceOnUse"'
        f' x1="0" y1="{_VIEWBOX_H:g}" x2="0" y2="0">'
        f'<stop offset="0%" stop-color="var(--color-accent, #0064e0)"/>'
        f'<stop offset="38%" stop-color="currentColor"/>'
        f'<stop offset="100%" stop-color="currentColor"/>'
        f'</linearGradient>'
        f'</defs>'
        # One group so the ring and both rays share a stroke and cannot drift
        # apart in weight. The class is a hook for the dark-mode flip in
        # ui/styles.py -- flipping the <svg> itself would take the word with it.
        f'<g class="ui-wordmark-mark" fill="none"'
        f' stroke="url(#{_GRADIENT_ID})"'
        f' stroke-width="{_MARK_STROKE}" stroke-linecap="round">'
        f'<path d="M {g["arc_start"][0]:.3f} {g["arc_start"][1]:.3f}'
        f' A {_MARK_R:.3f} {_MARK_R:.3f} 0 {g["large_arc"]} {g["sweep"]}'
        f' {g["arc_end"][0]:.3f} {g["arc_end"][1]:.3f}"/>'
        + "".join(
            f'<path d="M {a[0]:.3f} {a[1]:.3f} L {b[0]:.3f} {b[1]:.3f}"/>'
            for a, b in g["rays"]
        )
        + f'</g>'
        # The word stays flat currentColor. A gradient across lettering costs
        # legibility for no brand gain at this size.
        f'<text x="{_TEXT_X:g}" y="20.5" fill="currentColor"'
        f' font-family="var(--font-family)" font-size="19"'
        f' font-weight="700" letter-spacing="-0.5">{label}</text>'
        f'</svg>'
    )


def Wordmark(height: int = 24, label: str = "OpenApps") -> NotStr:
    """``wordmark_markup`` wrapped so FastHTML emits markup, not escaped text."""
    return NotStr(wordmark_markup(height=height, label=label))
