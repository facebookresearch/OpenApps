"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Programmatic desktop wallpaper.

A futuristic reading of the Bliss hillside: layered sine-wave ridges under a
gradient sky, rendered small, ordered-dithered against the Meta brand palette,
then scaled back up with nearest-neighbour so it reads as chunky 8-bit
halftone rather than a smooth gradient.

Nothing is downloaded and nothing is random. The whole image is a function of
``(variant, width, height)``, so the same variant renders byte-identically on
any machine -- which matters here, because a wallpaper that differed per run
would change every screenshot an agent is scored against.

## Rendering

Pillow and numpy, both already declared dependencies -- no shell-out, no system
package to install, nothing to detect at runtime. An earlier version preferred
ImageMagick and fell back to Pillow; the fallback was doing all the work, and
a second code path producing the same picture was a second thing to keep
correct for no benefit in a Python project.

If rendering fails for any reason, :func:`ensure_wallpaper` returns ``None``
and the caller drops to a pure-CSS gradient of the same hues. This is
decoration; it should never be able to take a page down.

## Regenerating

Variant 0 is committed under ``apps/assets/img/`` so a fresh clone paints
without generating anything. Other variants are rendered on demand into the
same directory and cached by filename::

    from open_apps.wallpaper import ensure_wallpaper
    ensure_wallpaper(variant=3)

Or from the command line, to refresh the committed default::

    uv run python -m open_apps.wallpaper --variant 0 --force
"""
from __future__ import annotations

import math
from pathlib import Path

#: Every colour the wallpaper may contain. Dithering maps each pixel to the
#: nearest entry, so the image is made only of brand colours -- no blends, no
#: off-palette midtones introduced by a gradient ramp.
META_PALETTE: list[tuple[int, int, int]] = [
    (0x00, 0x33, 0xFF),  # ring dark blue
    (0x00, 0x6F, 0xFF),  # ring blue
    (0x18, 0x77, 0xF2),  # Meta Blue 50
    (0x00, 0x64, 0xE0),  # Meta Blue
    (0x01, 0x43, 0xB5),  # Blue 800
    (0x93, 0x1E, 0xFA),  # Violet 650
    (0xF2, 0x4E, 0xED),  # ring pink
    (0xF3, 0x51, 0xC0),  # Pink 500
    (0x00, 0xD1, 0xAE),  # ring teal
    (0xA5, 0xF0, 0xE6),  # Teal 300
    (0xCD, 0xE5, 0xFF),  # Blue 90
    (0x1C, 0x2B, 0x33),  # Meta Gray 1000
    (0xFF, 0xFF, 0xFF),  # White
]

#: Rendered at 1/PIXEL_SCALE, then scaled up nearest-neighbour. This is what
#: produces the blocky halftone; dithering at full size would give fine noise
#: that vanishes into the page instead of reading as a deliberate texture.
PIXEL_SCALE = 4

DEFAULT_WIDTH = 1600
DEFAULT_HEIGHT = 1000

_IMG_DIR = Path(__file__).resolve().parent / "apps" / "assets" / "img"

#: URL path the desktop CSS points at, matching the static route in
#: apps/start_page/helper.py.
URL_PREFIX = "/assets/img"


def wallpaper_filename(variant: int) -> str:
    return f"desktop-meta-hills-{variant}.png"


def wallpaper_path(variant: int) -> Path:
    return _IMG_DIR / wallpaper_filename(variant)


def wallpaper_url(variant: int) -> str:
    return f"{URL_PREFIX}/{wallpaper_filename(variant)}"


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _ridges(variant: int) -> list[dict]:
    """Wave parameters for each hill layer, front to back.

    Derived from ``variant`` arithmetically rather than from a seeded RNG, so
    the same variant renders identically on any machine and any Python build
    without depending on a generator's stream staying stable.
    """
    layers = []
    for i in range(5):
        layers.append(
            {
                # Back layers sit higher and are shallower, which is what makes
                # the stack read as depth rather than as stripes.
                "base": 0.42 + i * 0.115,
                "amplitude": 0.030 + 0.016 * ((variant + i) % 3),
                "frequency": 1.0 + 0.5 * ((variant + 2 * i) % 4),
                "phase": (variant * 0.7 + i * 1.3) % (2 * math.pi),
                # Back to front: pale teal haze in the distance down to deep
                # blue up close. Reads as depth; the reverse reads as stripes.
                "colour": [
                    (0xA5, 0xF0, 0xE6),
                    (0x00, 0xD1, 0xAE),
                    (0x00, 0x6F, 0xFF),
                    (0x00, 0x64, 0xE0),
                    (0x01, 0x43, 0xB5),
                ][i],
            }
        )
    return layers


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def _render(variant: int, width: int, height: int, out: Path) -> Path:
    import numpy as np
    from PIL import Image

    w, h = width // PIXEL_SCALE, height // PIXEL_SCALE
    ys, xs = np.mgrid[0:h, 0:w]
    u, v = xs / w, ys / h

    # Sky: a vertical ramp through the brand blues into violet, then pink at
    # the horizon. Built in float RGB; the palette snap happens at the end.
    # The first ridge crests near v=0.37, so the whole sky ramp has to finish
    # above that. Stops past the horizon are painted over and never seen.
    stops = [
        (0.00, (0x00, 0x33, 0xFF)),
        (0.16, (0x00, 0x6F, 0xFF)),
        (0.28, (0x93, 0x1E, 0xFA)),
        (0.38, (0xF2, 0x4E, 0xED)),
    ]
    img = np.zeros((h, w, 3), dtype=np.float64)
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        band = (v >= p0) & (v < p1)
        t = np.clip((v - p0) / (p1 - p0), 0, 1)
        for ch in range(3):
            img[..., ch] = np.where(band, c0[ch] + (c1[ch] - c0[ch]) * t, img[..., ch])
    img[v >= stops[-1][0]] = stops[-1][1]

    # Hills, painted back to front so nearer ridges occlude farther ones.
    # _ridges() is ordered by increasing `base`, i.e. already back-to-front:
    # a smaller base sits higher on screen and is farther away. Iterating it
    # reversed painted the farthest ridge last, so it covered every nearer one
    # and five hills rendered as a single silhouette.
    for layer in _ridges(variant):
        ridge = layer["base"] + layer["amplitude"] * (
            np.sin(2 * math.pi * layer["frequency"] * u + layer["phase"])
            + 0.4 * np.sin(4 * math.pi * layer["frequency"] * u + layer["phase"] * 1.7)
        )
        under = v >= ridge
        # Fade each ridge toward its own colour with depth, so the front bands
        # darken instead of every hill being one flat fill.
        # np.maximum, not max: ridge is a per-column array, so the scalar
        # builtin would try to truth-test the whole thing.
        shade = np.clip(0.55 + 0.45 * (v - ridge) / np.maximum(1e-6, 1 - ridge), 0, 1)
        for ch in range(3):
            img[..., ch] = np.where(under, layer["colour"][ch] * shade, img[..., ch])

    # Bayer 8x8 ordered dither. Deterministic, and the regular threshold grid
    # is what gives the halftone its structure -- error diffusion would look
    # like noise at this block size.
    bayer = np.array(
        [[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
         [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
         [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
         [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]],
        dtype=np.float64,
    ) / 64.0 - 0.5
    threshold = np.tile(bayer, (h // 8 + 1, w // 8 + 1))[:h, :w]
    img = np.clip(img + threshold[..., None] * 56.0, 0, 255)

    # Snap to the palette: nearest entry per pixel, vectorised.
    pal = np.array(META_PALETTE, dtype=np.float64)
    d = ((img[:, :, None, :] - pal[None, None, :, :]) ** 2).sum(axis=3)
    quantised = pal[d.argmin(axis=2)].astype(np.uint8)

    small = Image.fromarray(quantised, mode="RGB")
    big = small.resize((width, height), Image.NEAREST)
    # Mode "P" keeps it a true 8-bit indexed bitmap and makes the file tiny --
    # flat blocks over 13 colours compress extremely well.
    big.convert("P", palette=Image.ADAPTIVE, colors=len(META_PALETTE)).save(out, optimize=True)
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ensure_wallpaper(
    variant: int = 0,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    force: bool = False,
) -> str | None:
    """Return the URL for ``variant``'s wallpaper, rendering it if needed.

    Cached by filename: an existing file is reused unless ``force``. Returns
    ``None`` if rendering failed, which the caller should treat as "use the CSS
    gradient" rather than as an error -- this is decoration, and nothing about
    it should be able to take a page down.
    """
    out = wallpaper_path(variant)
    if out.exists() and not force:
        return wallpaper_url(variant)

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        _render(variant, width, height, out)
        return wallpaper_url(variant)
    except Exception:
        return None


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", type=int, default=0)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--force", action="store_true")
    ns = parser.parse_args()

    out = wallpaper_path(ns.variant)
    out.parent.mkdir(parents=True, exist_ok=True)
    _render(ns.variant, ns.width, ns.height, out)
    print(f"{out}  ({out.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    _main()
