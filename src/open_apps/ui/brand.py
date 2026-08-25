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

The mark is a ring with a vertical stem up through its centre, crossed by a
45-degree line, with the arc between that line's two intersections omitted so
the line reads as having cut the ring open. See ``_mark_geometry`` for why the
opening lands on the lower-right rather than the upper-right -- it is forced by
the chord geometry, not chosen.

Two further details worth knowing:

* **The gradient reads theme tokens**, not hex. ``stop-color="var(--ring-blue)"``
  resolves inside inline SVG the same way it does in CSS, so the mark follows a
  theme swap and the light/dark toggle instead of staying one fixed blue. Each
  ``var()`` carries a literal fallback for the case where a theme omits the
  token.
* **The lettering is SVG ``<text>``** in the theme's own font stack, not traced
  outlines. It stays selectable and searchable, it costs a few hundred bytes
  rather than a few kilobytes of path data, and it inherits ``currentColor`` so
  the word is legible in both modes while only the mark carries the gradient.
"""
from __future__ import annotations

import math

from fasthtml.common import NotStr

#: One wordmark per page, so a fixed gradient id is safe. If that ever stops
#: being true this needs a suffix -- duplicate ids would make every instance
#: resolve to the first one's stops.
_GRADIENT_ID = "oa-wordmark-gradient"

_MARK_STROKE = "2.4"

# --- the mark --------------------------------------------------------------
# An O with a vertical stem up through its centre, and a 45-degree line
# crossing it; the arc between that line's two intersections with the O is
# omitted, so the line reads as having cut the ring open.
#
# A note on which side opens, because it is a geometric constraint rather than
# a choice: a chord's offset from the centre is perpendicular to the chord. For
# a 45-degree "/" line that perpendicular runs down-right or up-left, so a "/"
# chord can only ever cut the LOWER-RIGHT or the UPPER-LEFT arc. Opening the
# upper-right would need a "\" line instead. Lower-right is used here.
#
# Everything below is derived from the three constants so the shape stays
# consistent if they are retuned -- the endpoints and arc flags are exactly the
# kind of thing that rots when hand-copied.
_MARK_CX, _MARK_CY, _MARK_R = 13.0, 15.0, 7.0

#: Chord offset from the centre, as a fraction of the radius, i.e. how deep the
#: 45-degree line bites into the ring. Small values put the chord near the
#: centre and take out most of the lower-right; large values leave a tight
#: notch. 0.5 removed so much of the ring that the diagonal dominated the mark
#: and the O stopped reading as an O.
_MARK_CUT_DEPTH = 0.72
_MARK_OVERSHOOT = 1.4   # how far the 45-degree line runs past the ring
_STEM_TOP = 5.5


def _mark_geometry() -> dict:
    """Endpoints and arc flags for the mark, computed from the constants."""
    k = math.sqrt(0.5)
    d = _MARK_R * _MARK_CUT_DEPTH                     # chord offset from centre
    half = math.sqrt(_MARK_R**2 - d**2)               # half the chord length
    # Chord midpoint, offset down-right; chord itself runs up-and-to-the-right.
    mx, my = _MARK_CX + d * k, _MARK_CY + d * k
    p1 = (mx + half * k, my - half * k)               # upper-right intersection
    p2 = (mx - half * k, my + half * k)               # lower-left intersection

    def angle(p):
        return math.degrees(math.atan2(p[1] - _MARK_CY, p[0] - _MARK_CX)) % 360

    span = (angle(p1) - angle(p2)) % 360               # arc we keep, clockwise
    return {
        "p1": p1,
        "p2": p2,
        # Line overshoots both intersections so it reads as crossing the ring
        # rather than as a chord closing it.
        "l1": (p1[0] + _MARK_OVERSHOOT * k, p1[1] - _MARK_OVERSHOOT * k),
        "l2": (p2[0] - _MARK_OVERSHOOT * k, p2[1] + _MARK_OVERSHOOT * k),
        # SVG sweep=1 is clockwise on screen (y grows downward), which is the
        # direction from p2 round through the left and top to p1.
        "large_arc": 1 if span > 180 else 0,
        "sweep": 1,
    }


def wordmark_markup(height: int = 24, label: str = "OpenApps") -> str:
    """Raw ``<svg>`` for the lockup: the cut ring plus the word."""
    g = _mark_geometry()
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 150 28"'
        f' height="{height}" role="img" aria-label="{label}"'
        f' class="ui-wordmark">'
        f'<defs>'
        f'<linearGradient id="{_GRADIENT_ID}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="var(--ring-dark-blue, #0033ff)"/>'
        f'<stop offset="50%" stop-color="var(--ring-violet, #931efa)"/>'
        f'<stop offset="100%" stop-color="var(--ring-pink, #f24eed)"/>'
        f'</linearGradient>'
        f'</defs>'
        # The mark. One group so the ring, stem and cut line share a stroke and
        # cannot drift apart in weight -- "equal weight" is the whole point of
        # the shape.
        f'<g fill="none" stroke="url(#{_GRADIENT_ID})"'
        f' stroke-width="{_MARK_STROKE}" stroke-linecap="round">'
        # Ring, opened between the two intersections.
        f'<path d="M {g["p2"][0]:.3f} {g["p2"][1]:.3f}'
        f' A {_MARK_R} {_MARK_R} 0 {g["large_arc"]} {g["sweep"]}'
        f' {g["p1"][0]:.3f} {g["p1"][1]:.3f}"/>'
        # Vertical stem, up through the centre and out past the top.
        f'<path d="M {_MARK_CX} {_MARK_CY} V {_STEM_TOP}"/>'
        # The 45-degree line.
        f'<path d="M {g["l2"][0]:.3f} {g["l2"][1]:.3f}'
        f' L {g["l1"][0]:.3f} {g["l1"][1]:.3f}"/>'
        f'</g>'
        # The word. font-family reads the theme token so the lockup changes
        # with the theme rather than pinning one family.
        f'<text x="26" y="19.5" fill="currentColor"'
        f' font-family="var(--font-family)" font-size="16.5"'
        f' font-weight="700" letter-spacing="-0.5">{label}</text>'
        f'</svg>'
    )


def Wordmark(height: int = 24, label: str = "OpenApps") -> NotStr:
    """``wordmark_markup`` wrapped so FastHTML emits markup, not escaped text."""
    return NotStr(wordmark_markup(height=height, label=label))
