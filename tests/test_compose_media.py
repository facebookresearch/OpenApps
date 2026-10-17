"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""The composer's committed preview media (``docs/assets/composer/media``).

Written by ``scripts/render_composer_media.py``. These keep the manifest
honest against the files beside it and against ``config/``: a renamed theme or
layout leaves a stale key here, which fails, rather than a clip the component
can never select. Missing combinations are allowed -- the component falls back
to its placeholder -- so adding a theme does not demand a re-render first.
"""

import json
from pathlib import Path

import pytest

from open_apps.compose.catalog import build_catalog

MEDIA = Path(__file__).parents[1] / "docs" / "assets" / "composer" / "media"
MANIFEST = MEDIA / "manifest.json"
BUDGET_BYTES = 15 * 1024 * 1024


@pytest.fixture(scope="module")
def manifest():
    assert MANIFEST.exists(), "run scripts/render_composer_media.py"
    return json.loads(MANIFEST.read_text())


@pytest.fixture(scope="module")
def catalog():
    return build_catalog()


def test_manifest_shape(manifest):
    assert set(manifest) == {"clips", "stills"}
    assert manifest["clips"] and manifest["stills"]


def test_every_manifest_path_exists_inside_the_media_dir(manifest):
    root = MEDIA.resolve()
    for section in ("clips", "stills"):
        for key, rel in manifest[section].items():
            path = (MEDIA / rel).resolve()
            assert path.is_relative_to(root), (key, rel)
            assert path.is_file() and path.stat().st_size > 0, (key, rel)


def test_clip_keys_are_device_and_theme_options(manifest, catalog):
    devices = {o.name for o in catalog.groups["device"].options}
    themes = {o.name for o in catalog.groups["apps/theme"].options}
    for key, rel in manifest["clips"].items():
        device, theme = key.split("/")
        assert device in devices, key
        assert theme in themes, key
        assert rel.endswith(".webm"), rel


def test_still_keys_are_app_axis_values(manifest, catalog):
    for key in manifest["stills"]:
        app, axis, value = key.split("/")
        group = catalog.groups.get(f"apps/{app}/{axis}")
        assert group is not None, key
        assert value in {o.name for o in group.options}, key


def test_media_stays_within_budget():
    total = sum(p.stat().st_size for p in MEDIA.rglob("*") if p.is_file())
    assert total <= BUDGET_BYTES, f"{total / 1e6:.1f} MB"


def test_no_stray_files(manifest):
    """Everything in the folder is referenced: a re-render that renamed a
    file must not leave the old one behind in git."""
    referenced = {
        (MEDIA / rel).resolve()
        for s in ("clips", "stills")
        for rel in manifest[s].values()
    }
    for path in MEDIA.rglob("*"):
        if path.is_file() and path != MANIFEST:
            assert path.resolve() in referenced, path
