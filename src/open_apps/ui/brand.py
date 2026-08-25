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

Two details worth knowing:

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

from fasthtml.common import NotStr

#: One wordmark per page, so a fixed gradient id is safe. If that ever stops
#: being true this needs a suffix -- duplicate ids would make every instance
#: resolve to the first one's stops.
_GRADIENT_ID = "oa-wordmark-gradient"

_MARK_STROKE = "2.4"


def wordmark_markup(height: int = 24, label: str = "OpenApps") -> str:
    """Raw ``<svg>`` for the lockup: two interlocking rings plus the word.

    The rings are an abstract nod to a connected pair, drawn from scratch --
    deliberately not a reproduction of any existing corporate mark.
    """
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
        # The mark: two overlapping rings.
        f'<circle cx="10.5" cy="14" r="6.6" fill="none"'
        f' stroke="url(#{_GRADIENT_ID})" stroke-width="{_MARK_STROKE}"/>'
        f'<circle cx="20.5" cy="14" r="6.6" fill="none"'
        f' stroke="url(#{_GRADIENT_ID})" stroke-width="{_MARK_STROKE}"/>'
        # The word. font-family reads the theme token so the lockup changes
        # with the theme rather than pinning one family.
        f'<text x="34" y="19.5" fill="currentColor"'
        f' font-family="var(--font-family)" font-size="16.5"'
        f' font-weight="700" letter-spacing="-0.5">{label}</text>'
        f'</svg>'
    )


def Wordmark(height: int = 24, label: str = "OpenApps") -> NotStr:
    """``wordmark_markup`` wrapped so FastHTML emits markup, not escaped text."""
    return NotStr(wordmark_markup(height=height, label=label))
