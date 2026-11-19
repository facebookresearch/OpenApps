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


def test_clip_keys_are_clip_axis_options(manifest, catalog, renderer):
    """A full key (one option per clip axis) at its canonical path, or a
    legacy ``<device>/<theme>`` one."""
    for key, rel in manifest["clips"].items():
        parts = key.split("/")
        assert len(parts) in (2, len(renderer.CLIP_AXES)), key
        for axis, value in zip(renderer.CLIP_AXES, parts):
            assert value in {o.name for o in catalog.groups[axis].options}, key
        if len(parts) == len(renderer.CLIP_AXES):
            assert rel == renderer.clip_path(key), (key, rel)
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


# ---------------------------------------------------------------------------
# The clip grid: planning, shards, keys, merging
# ---------------------------------------------------------------------------


def _clip_jobs(renderer, catalog, *argv):
    args = renderer.parse_args(list(argv))
    return renderer.plan(catalog, renderer.clip_axes(args, catalog), True, False)


def test_full_plans_every_combination(renderer, catalog):
    jobs = _clip_jobs(renderer, catalog, "--full")
    sizes = [len(catalog.groups[a].options) for a in renderer.CLIP_AXES]
    assert sizes == [4, 11, 3, 4, 3]
    assert len(jobs) == 1584 == len({job.key for job in jobs})
    first = jobs[0]
    assert first.overrides == tuple(
        f"{axis}={value}"
        for axis, value in zip(renderer.CLIP_AXES, first.key.split("/"))
    )


def test_the_default_run_is_still_device_by_theme(renderer, catalog):
    jobs = _clip_jobs(renderer, catalog)
    assert len(jobs) == 4 * 11
    rest = {tuple(job.key.split("/")[2:]) for job in jobs}
    assert rest == {("default", "desktop", "default")}


def test_axis_flags_narrow_the_grid(renderer, catalog):
    jobs = _clip_jobs(
        renderer, catalog,
        "--devices", "desktop", "--themes", "default", "dark",
        "--languages", "german", "--start-layouts", "gallery",
        "--todo-layouts", "kanban_board",
    )  # fmt: skip
    assert [job.key for job in jobs] == [
        "desktop/default/german/gallery/kanban_board",
        "desktop/dark/german/gallery/kanban_board",
    ]
    # --full with a flag: that axis narrowed, every other one in full.
    assert len(_clip_jobs(renderer, catalog, "--full", "--devices", "phone")) == 396


@pytest.mark.parametrize("count", [1, 5, 12, 13, 2000])
def test_shards_partition_the_clips_exactly_once(renderer, catalog, count):
    args = renderer.parse_args(["--full"])
    jobs = renderer.plan(catalog, renderer.clip_axes(args, catalog), True, True)
    clips = [job for job in jobs if job.kind == "clip"]
    shards = [renderer.shard(jobs, i, count) for i in range(1, count + 1)]
    keys = [job.key for part in shards for job in part if job.kind == "clip"]
    assert sorted(keys) == sorted(job.key for job in clips)  # each exactly once
    sizes = [sum(job.kind == "clip" for job in part) for part in shards]
    assert max(sizes) - min(sizes) <= 1  # balanced
    # Stills compare an app's stills with each other: all on shard 1.
    assert [job.kind for part in shards[1:] for job in part].count("stills") == 0
    assert sum(job.kind == "stills" for job in shards[0]) == len(jobs) - len(clips)
    # Deterministic: the same split every time.
    assert renderer.shard(jobs, 1, count) == shards[0]


@pytest.mark.parametrize("bad", ["0/4", "5/4", "1", "a/b", "1/0"])
def test_a_bad_shard_is_refused(renderer, bad):
    with pytest.raises(SystemExit):
        renderer.parse_args(["--shard", bad])


def test_clip_keys_are_canonical(renderer, catalog):
    defaults = renderer.clip_axis_defaults(catalog)
    assert defaults == {
        "device": "desktop",
        "apps/theme": "default",
        "apps/language": "default",
        "apps/start_page/layout": "desktop",
        "apps/todo/layout": "default",
    }
    full = "phone/dark/german/gallery/kanban_board"
    assert renderer.canonical_clip_key(full, defaults) == full
    assert (
        renderer.canonical_clip_key("phone/dark", defaults)
        == "phone/dark/default/desktop/default"
    )
    assert renderer.clip_path(full) == f"clips/{full}.webm"
    for bad in ("phone", "phone/dark/german", "phone//default/desktop/default"):
        with pytest.raises(ValueError):
            renderer.canonical_clip_key(bad, defaults)


def test_legacy_keys_load_as_their_canonical_key(renderer, catalog, manifest):
    loaded = renderer.load_manifest(MANIFEST, renderer.clip_axis_defaults(catalog))
    assert len(loaded["clips"]) == len(manifest["clips"])
    assert loaded["clips"]["phone/dark/default/desktop/default"] == manifest[
        "clips"
    ].get("phone/dark", manifest["clips"].get("phone/dark/default/desktop/default"))
    assert loaded["stills"] == manifest["stills"]


def _layer(root: Path, clips: dict[str, bytes], stills: dict | None = None) -> Path:
    """A rendered dir: files plus the manifest naming them."""
    stills = stills or {}
    manifest = {"clips": {}, "stills": {}}
    for section, entries, ext in (("clips", clips, "webm"), ("stills", stills, "webp")):
        for key, data in entries.items():
            rel = f"{section}/{key}.{ext}"
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
            manifest[section][key] = rel
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def test_merge_layers_shards_with_later_ones_winning(tmp_path, renderer, catalog):
    base = _layer(
        tmp_path / "base",
        {"desktop/dark": b"legacy", "phone/dark": b"phone"},
        {"todo/layout/default": b"S"},
    )
    a = _layer(
        tmp_path / "a",
        {
            "desktop/dark/default/desktop/default": b"rendered",
            "desktop/dark/german/gallery/kanban_board": b"a",
        },
    )
    b = _layer(tmp_path / "b", {"desktop/dark/german/gallery/kanban_board": b"b"})
    warnings = renderer.merge(base, [a, tmp_path / "missing", b], catalog)
    assert warnings == [f"{tmp_path / 'missing'}: no manifest.json, skipped"]

    merged = json.loads((base / "manifest.json").read_text())
    clip = lambda key: (base / merged["clips"][key]).read_bytes()  # noqa: E731
    assert set(merged["clips"]) == {
        "desktop/dark/default/desktop/default",
        "desktop/dark/german/gallery/kanban_board",
        "phone/dark/default/desktop/default",
    }
    assert clip("desktop/dark/default/desktop/default") == b"rendered"
    assert clip("desktop/dark/german/gallery/kanban_board") == b"b"
    assert clip("phone/dark/default/desktop/default") == b"phone"  # fallback kept
    assert merged["stills"] == {
        "todo/layout/default": "stills/todo/layout/default.webp"
    }
    # The superseded legacy file is gone, not shipped beside its replacement.
    assert not (base / "clips" / "desktop" / "dark.webm").exists()


def test_merge_skips_entries_without_a_file_or_outside_the_shard(
    tmp_path, renderer, catalog
):
    base = _layer(tmp_path / "base", {})
    shard = _layer(tmp_path / "shard", {"desktop/dark": b"x"})
    data = json.loads((shard / "manifest.json").read_text())
    data["clips"]["phone/dark"] = "../base/manifest.json"
    data["clips"]["tablet/dark"] = "clips/tablet/dark.webm"  # never written
    (shard / "manifest.json").write_text(json.dumps(data))
    warnings = renderer.merge(base, [shard], catalog)
    assert len(warnings) == 2
    merged = json.loads((base / "manifest.json").read_text())
    assert list(merged["clips"]) == ["desktop/dark/default/desktop/default"]


@pytest.mark.parametrize("device", ["phone", "tablet"])
def test_a_touch_device_composes_the_start_layout_it_was_given(
    tmp_path, renderer, catalog, device
):
    """The device never moves the start page's layout value (on a phone or
    tablet ``desktop`` renders its home screen; it is still ``desktop``), so
    composer.js's key from the selection is the key the clip is filed under."""
    from open_apps.compose.live import compose_config

    for option in catalog.groups["apps/start_page/layout"].options:
        (job,) = _clip_jobs(
            renderer, catalog, "--devices", device, "--themes", "dark",
            "--languages", "german", "--start-layouts", option.name,
        )  # fmt: skip
        config = compose_config(list(job.overrides), tmp_path / option.name)
        assert renderer.composed_clip_key(device, config) == job.key
