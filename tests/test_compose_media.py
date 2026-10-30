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

A still entry is its path, or ``{"src": path, "same_as": key}`` when its file
is byte-identical to another still of the same app (a content pack that
changes nothing above the fold); the component captions those as such.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from open_apps.compose.catalog import build_catalog

ROOT = Path(__file__).parents[1]
MEDIA = ROOT / "docs" / "assets" / "composer" / "media"
MANIFEST = MEDIA / "manifest.json"
BUDGET_BYTES = 15 * 1024 * 1024


@pytest.fixture(scope="module")
def manifest():
    assert MANIFEST.exists(), "run scripts/render_composer_media.py"
    return json.loads(MANIFEST.read_text())


@pytest.fixture(scope="module")
def catalog():
    return build_catalog()


@pytest.fixture(scope="module")
def renderer():
    """scripts/render_composer_media.py as a module (scripts/ is no package)."""
    path = ROOT / "scripts" / "render_composer_media.py"
    spec = importlib.util.spec_from_file_location("render_composer_media", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _src(entry) -> str:
    return entry if isinstance(entry, str) else entry["src"]


def _digest(rel: str) -> str:
    return hashlib.sha256((MEDIA / rel).read_bytes()).hexdigest()


def test_manifest_shape(manifest):
    assert set(manifest) == {"clips", "stills"}
    assert manifest["clips"] and manifest["stills"]


def test_every_manifest_path_exists_inside_the_media_dir(manifest):
    root = MEDIA.resolve()
    for section in ("clips", "stills"):
        for key, entry in manifest[section].items():
            rel = _src(entry)
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
        (MEDIA / _src(entry)).resolve()
        for s in ("clips", "stills")
        for entry in manifest[s].values()
    }
    for path in MEDIA.rglob("*"):
        if path.is_file() and path != MANIFEST:
            assert path.resolve() in referenced, path


def test_identical_stills_are_marked_same_as(manifest):
    """Every byte-identical pair within an app is linked, to an unmarked
    still with the same bytes; nothing else is."""
    stills = manifest["stills"]
    canonical: dict[tuple[str, str], str] = {}
    for key, entry in stills.items():
        if isinstance(entry, str):
            slot = (key.split("/")[0], _digest(entry))
            assert slot not in canonical, f"{key} is identical to {canonical.get(slot)}"
            canonical[slot] = key
    for key, entry in stills.items():
        if isinstance(entry, dict):
            assert set(entry) == {"src", "same_as"}, key
            twin = entry["same_as"]
            assert isinstance(stills.get(twin), str), (key, twin)
            assert twin.split("/")[0] == key.split("/")[0], (key, twin)
            assert _digest(entry["src"]) == _digest(stills[twin]), (key, twin)


def test_the_known_duplicates_name_their_twin(manifest):
    stills = manifest["stills"]
    expected = {
        "calendar/content/long_descriptions": "calendar/layout/default",
        "maps/content/misleading_descriptions": "maps/layout/default",
        "todo/content/long_descriptions": "todo/content/adversarial_descriptions",
        "todo/content/misleading_descriptions": "todo/content/adversarial_descriptions",
        "start_page/content/misleading_descriptions": "start_page/content/long_descriptions",
    }
    for key, twin in expected.items():
        assert stills[key]["same_as"] == twin, key
    assert isinstance(stills["todo/layout/kanban_board"], str)


def test_the_committed_manifest_is_what_the_renderer_marks(manifest, catalog, renderer):
    """Post-processed with the renderer's own detection, not by hand."""
    plain = {k: _src(v) for k, v in manifest["stills"].items()}
    marked = renderer.mark_same_as(plain, MEDIA, renderer.axis_defaults(catalog))
    assert marked == manifest["stills"]


def test_mark_same_as_prefers_the_axis_default(tmp_path, renderer):
    files = {
        "todo/content/default": b"A",
        "todo/content/long": b"B",
        "todo/content/misleading": b"B",
        "todo/content/german": b"A",
        "todo/layout/default": b"A",
        "maps/layout/default": b"B",  # another app's identical bytes: unrelated
    }
    for key, data in files.items():
        (tmp_path / f"{key}.webp").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f"{key}.webp").write_bytes(data)
    stills = {key: f"{key}.webp" for key in files}
    defaults = {"todo/content": "default", "todo/layout": "default"}
    marked = renderer.mark_same_as(stills, tmp_path, defaults)
    assert marked["todo/layout/default"] == "todo/layout/default.webp"
    assert marked["todo/content/default"] == {
        "src": "todo/content/default.webp",
        "same_as": "todo/layout/default",
    }
    assert marked["todo/content/german"]["same_as"] == "todo/layout/default"
    assert marked["todo/content/long"] == "todo/content/long.webp"
    assert marked["todo/content/misleading"]["same_as"] == "todo/content/long"
    assert marked["maps/layout/default"] == "maps/layout/default.webp"
    # Idempotent: marking a marked manifest changes nothing.
    assert renderer.mark_same_as(marked, tmp_path, defaults) == marked
