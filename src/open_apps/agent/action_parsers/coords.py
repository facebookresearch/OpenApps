"""Coordinate-space conversion shared by every action_parser.

Models disagree on what an (x, y) in their output means. Three conventions are
in play here:

* raw viewport pixels -- UI-TARS 1.5, GPT-4o-style computer use. ``coord_scale=None``.
* 0-1000 normalized -- Qwen-VL, GLM-VL. ``coord_scale=1000``.
* any other normalized [0, N) grid -- e.g. PaliGemma/Gemma-lineage ``<locNNNN>``
  bins are 0-1024. ``coord_scale=1024``.

Getting this wrong is silent: the click lands somewhere plausible-looking on the
page and the episode just scores 0. Set ``coord_scale`` per model family (see
``ActionParser.coord_scale``), never per call site.
"""
from __future__ import annotations


def rescale_xy(
    x: float,
    y: float,
    coord_scale: int | None,
    viewport: tuple[int, int],
) -> tuple[int, int]:
    """Map (x, y) into viewport pixels.

    coord_scale=None: raw pixels (UI-TARS, GPT-4o). coord_scale=1000:
    Qwen-VL / GLM-VL 0-1000 normalized. coord_scale=N: any normalized
    [0, N) convention.

    ``coord_scale`` is a single scalar applied against each viewport axis
    independently, which is what a square normalized grid means. It cannot
    express a model that predicts in its own non-square resized image space
    (that needs a separate scale per axis).
    """
    vw, vh = viewport
    if coord_scale:
        return (
            int(round(float(x) * vw / coord_scale)),
            int(round(float(y) * vh / coord_scale)),
        )
    return int(round(float(x))), int(round(float(y)))
