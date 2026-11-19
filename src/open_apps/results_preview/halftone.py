"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The results preview's background: a faint, cloudy, pixel-halftone texture.

Each call draws a fresh square tile with Pillow. The tile is periodic (its
noise wraps at the edges), so repeated across the page it has no seams and no
edge: the texture is unbounded however large the window. A low-frequency
cloud field sets, cell by cell, the size of a crisp square "dot", which reads
as a halftone gradient drifting across the page. Ink is a few percent off the
paper colour, so it stays behind the table.

    from open_apps.results_preview.halftone import tile_png
    png = tile_png("light", seed=0)        # bytes of a PNG
"""

from __future__ import annotations

import io
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

#: (paper RGB, ink RGBA) per scheme. Ink alpha is what keeps it subtle.
SCHEMES = {
    "light": ((246, 246, 244), (24, 26, 30, 22)),
    "dark": ((15, 16, 18), (235, 238, 242, 14)),
}
TILE = 960  # px, a multiple of CELL; large, so a screen rarely shows a repeat
CELL = 6  # px per halftone cell; dots are whole pixels
# (grid size, weight) of each noise octave. Every grid size divides the tile's
# cell count, which is what makes the noise wrap. The 2x2 octave is the broad
# gradient; the finer ones are the cloud.
OCTAVES = ((2, 1.0), (4, 0.6), (8, 0.3), (16, 0.14), (32, 0.06))


def _smooth(t: np.ndarray) -> np.ndarray:
    return t * t * (3 - 2 * t)


def periodic_noise(n: int, rng: np.random.Generator, octaves=OCTAVES) -> np.ndarray:
    """``n x n`` value noise in [0, 1] that tiles: row 0 continues row n-1."""
    out = np.zeros((n, n))
    for k, weight in octaves:
        grid = rng.random((k, k))
        pos = np.arange(n) / n * k
        i0 = np.floor(pos).astype(int)
        t = _smooth(pos - i0)
        i1 = (i0 + 1) % k
        i0 %= k
        tx, ty = t[None, :], t[:, None]
        top = grid[np.ix_(i0, i0)] * (1 - tx) + grid[np.ix_(i0, i1)] * tx
        bottom = grid[np.ix_(i1, i0)] * (1 - tx) + grid[np.ix_(i1, i1)] * tx
        out += weight * (top * (1 - ty) + bottom * ty)
    out -= out.min()
    return out / (out.max() or 1.0)


def tile(scheme: str = "light", seed: Optional[int] = None) -> Image.Image:
    """One seamless tile. ``seed=None`` draws a new texture each time."""
    paper, ink = SCHEMES[scheme]
    rng = np.random.default_rng(seed)
    cells = TILE // CELL
    # Skewed so most of the page is near-empty paper and dots gather in clouds.
    field = periodic_noise(cells, rng) ** 1.8
    image = Image.new("RGBA", (TILE, TILE), (*paper, 255))
    dots = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(dots)
    largest = CELL - 2  # always a pixel of paper between neighbouring dots
    for y in range(cells):
        for x in range(cells):
            side = int(round(field[y, x] * largest))
            if side <= 0:
                continue
            x0 = x * CELL + (CELL - side) // 2
            y0 = y * CELL + (CELL - side) // 2
            draw.rectangle((x0, y0, x0 + side - 1, y0 + side - 1), fill=ink)
    return Image.alpha_composite(image, dots).convert("RGB")


def tile_png(scheme: str = "light", seed: Optional[int] = None) -> bytes:
    buf = io.BytesIO()
    tile(scheme, seed).save(buf, format="PNG", optimize=True)
    return buf.getvalue()
