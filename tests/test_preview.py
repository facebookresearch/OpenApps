"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from pathlib import Path

import pytest

from open_apps.preview import fit_to_screen

#: A 1440x900 laptop with a 25px menu bar and an 87px-tall browser toolbar.
LAPTOP = {"left": 0, "top": 25, "width": 1440, "height": 875, "frameW": 0, "frameH": 87}


@pytest.mark.parametrize(
    "wanted, expected",
    [
        ((1920, 1080), (1440, 788)),  # desktop: clamped on both axes
        ((1280, 800), (1280, 788)),  # laptop device: only the height gives
        ((820, 1180), (820, 788)),  # tablet: keeps its width, loses height
        ((390, 844), (390, 788)),
        ((390, 600), (390, 600)),  # fits: untouched
    ],
)
def test_window_never_outgrows_the_screen(wanted, expected):
    assert fit_to_screen(wanted, LAPTOP) == expected


def test_a_big_monitor_gets_the_exact_device():
    big = {"left": 0, "top": 0, "width": 3840, "height": 2160, "frameW": 16, "frameH": 90}
    assert fit_to_screen((1920, 1080), big) == (1920, 1080)


def _device(name: str) -> dict:
    from omegaconf import OmegaConf

    from open_apps import device as device_module

    path = Path(__file__).resolve().parent.parent / "config" / "device" / f"{name}.yaml"
    return device_module.as_dict(OmegaConf.load(path))


@pytest.mark.slow
@pytest.mark.parametrize("name", ["phone", "tablet"])
def test_an_emulated_device_opens_in_a_popup_at_its_exact_size(name):
    """Chromium won't make a tabbed window narrower than ~500px, so a 390px
    phone shown in one sat beside a ~110px blank strip. A popup window has no
    toolbar and no such floor: its page area is the device, edge to edge.
    (Headless doesn't enforce the floor, so what's pinned here is the popup.)"""
    playwright_api = pytest.importorskip("playwright.sync_api")
    from open_apps.preview import open_window

    device = _device(name)
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright_api.Error as exc:
            pytest.skip(f"no chromium: {str(exc).splitlines()[0]}")
        try:
            page, size = open_window(browser, device, screen=LAPTOP | {"height": 2000})
            assert size == tuple(device["viewport"])
            assert page.evaluate("() => window.toolbar.visible") is False
            # Like every app page: without it a mobile layout viewport is 980px.
            page.set_content('<meta name="viewport" content="width=device-width">')
            assert page.evaluate("() => [innerWidth, innerHeight]") == list(device["viewport"])
            # The window it was opened from is gone: one window per preview.
            assert page.context.pages == [page]
        finally:
            browser.close()
