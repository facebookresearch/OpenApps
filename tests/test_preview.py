"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
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
