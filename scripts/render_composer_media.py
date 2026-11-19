"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Render the Config Composer's static preview media.

    uv run scripts/render_composer_media.py                  # device x theme
    uv run scripts/render_composer_media.py --devices phone --themes dark
    uv run scripts/render_composer_media.py --no-clips       # stills only
    uv run scripts/render_composer_media.py --devices desktop --themes dark
        --languages german --start-layouts gallery --todo-layouts kanban_board
        --no-stills --out /tmp/media                         # one exact clip
    uv run scripts/render_composer_media.py --full --no-stills --shard 3/12 --out shard3
    uv run scripts/render_composer_media.py --merge shard1 shard2 ...  # into --out

The docs page cannot run the apps, so it shows pre-rendered approximations: a
few seconds of low-resolution webm per clip -- the start page, then the todo
app opening -- and for every app one still per layout value and per content
value. The component plays the clip for the selection with the still of the
last app the user touched inset over it.

Clips are keyed by five axes, in this order (:data:`CLIP_AXES`)::

    <device>/<theme>/<language>/<start-page layout>/<todo layout>
    clips/<device>/<theme>/<language>/<start>/<todo>.webm

A null default reads ``default`` (the composer treats "App defaults" as the
default theme and "each app's own" language as English). The key is read back
from what the boot actually composed (:func:`composed_clip_key`), not from
what was asked for. The device does not change the start page's layout value:
on a phone or tablet, ``desktop`` renders its home-screen variant and the
gallery layouts reflow, so all four layouts are distinct pictures on every
device and the composer computes the same key from its selection. A legacy
``"<device>/<theme>"`` key (what this script wrote before the grid) means the
other three axes at their defaults, and is read as that everywhere.

The full grid (``--full``: 4 devices x 11 themes x 3 languages x 4 start-page
layouts x 3 todo layouts = 1,584 clips, ~250 MB, ~3 h) is too big for git. The
GitHub Pages workflow (.github/workflows/static.yml) renders it in shards
(``--shard I/N``: every N-th clip from the I-th, so each shard gets a mix),
caches each shard on a hash of src/, config/, this script and uv.lock, and
merges them (``--merge``) over the small committed set in
docs/assets/composer/media/ before ``mkdocs build``. The committed set --
today's default run, device x theme with the other axes at their defaults --
is what local docs show, and what a deploy whose render failed falls back to.
The component plays the closest clip it has and says what differs.

Every combination is a real server: the apps are booted through
:func:`open_apps.compose.live.start_apps` (OpenAppsLauncher's port window and
``databases_dir`` guard, as the agent path uses) with those overrides, and
recorded in Playwright's Chromium at the device's own viewport. Boots
dominate the run time, so several are started ahead of the one being
recorded, each in its own port window; recording itself stays on one browser
because Chromium in some sandboxes cannot run two.

Option lists come from the composer catalog, so a theme or layout added under
``config/`` is picked up on the next run. A partial run updates its own
entries in ``manifest.json`` (checkpointed after every boot, so a killed shard
keeps what it rendered) and keeps the rest; entries whose file is gone are
dropped, and a file whose entry was replaced is deleted. Manual for the
committed set, like tests/save_screenshots.py -- rerun it when the look of the
apps changes, and keep the committed total under ~15 MB (it prints the size).

Some packs change nothing above the fold (the todo app's long, misleading and
adversarial descriptions all render the same first screen). Every run hashes
the stills and marks each one byte-identical to another of its app as
``{"src": ..., "same_as": key}`` (:func:`mark_same_as`), so the component can
say so instead of implying the pack looks like that. ``--mark-only`` does
just that to the existing manifest, without rendering anything. Stills are
taken by the first shard only.
"""

import argparse
import hashlib
import itertools
import json
import shutil
import sys
import tempfile
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from playwright.sync_api import Browser, sync_playwright

from open_apps import device as device_module
from open_apps.compose.catalog import build_catalog
from open_apps.compose.live import REPO_ROOT, LiveApps, start_apps

DEFAULT_OUT = REPO_ROOT / "docs" / "assets" / "composer" / "media"

#: The page each app's still is taken on (mirrors tests/save_screenshots.py's
#: ROUTES). Keyed by the app's segment of its ``apps/<app>/...`` group path.
APP_ROUTES = {
    "start_page": "/",
    "todo": "/todo",
    "calendar": "/calendar",
    "messenger": "/messages",
    "maps": "/maps",
    "code_editor": "/codeeditor/",
    "openbanking": "/openbanking",
    "onlineshop": "/onlineshop",
    "ui_library": "/uilibrary",
}
#: Still groups: the per-app axes the composer's Apps section shows.
STILL_AXES = ("layout", "content")

#: Media is drawn inside a box this size (px), aspect kept: the preview panel
#: is ~360 CSS px wide, so this stays sharp on a 2x screen and no larger.
CLIP_BOX = 640
STILL_WIDTH = 640
STILL_QUALITY = 70
#: Seconds on each page of the tour (start page, then the app).
TOUR_HOLD = (1.4, 2.0)
TOUR_APP = "/todo"

#: The catalog groups a clip is keyed by, in key order. composer.js's
#: CLIP_AXES is the same list; it matches on them in this order of priority
#: (after the device, which must match).
CLIP_AXES = (
    "device",
    "apps/theme",
    "apps/language",
    "apps/start_page/layout",
    "apps/todo/layout",
)


@dataclass(frozen=True)
class Job:
    """One server boot and what to capture from it."""

    name: str
    overrides: tuple[str, ...]
    #: "clip" (uses key) or "stills" (uses stills).
    kind: str
    #: A clip's planned key, one value per CLIP_AXES joined by "/".
    key: str = ""
    stills: tuple[tuple[str, str, str], ...] = ()


# ---------------------------------------------------------------------------
# Clip keys
# ---------------------------------------------------------------------------


def clip_axis_defaults(catalog) -> dict[str, str]:
    """Each clip axis's value when nothing is selected.

    config.yaml's default, with null read as ``default`` -- as composer.js
    reads it: no theme is the default theme, no language each app's English.
    """
    return {
        axis: catalog.groups[axis].defaults.get("config")
        or ("desktop" if axis == "device" else "default")
        for axis in CLIP_AXES
    }


def canonical_clip_key(key: str, defaults: dict[str, str]) -> str:
    """A clip key with every axis spelled out.

    ``"<device>/<theme>"`` is the pre-grid form: the other axes at their
    defaults, which is what those clips were recorded with.
    """
    parts = key.split("/")
    if len(parts) == 2:
        parts += [defaults[axis] for axis in CLIP_AXES[2:]]
    if len(parts) != len(CLIP_AXES) or not all(parts):
        raise ValueError(f"not a clip key: {key!r}")
    return "/".join(parts)


def clip_path(key: str) -> str:
    """Where a canonical key's clip lives, relative to the media dir."""
    return f"clips/{key}.webm"


def composed_clip_key(device: str, config) -> str:
    """The key of what a boot composed, read back from its config.

    The device is the group option that was booted (``device.name`` is
    free-form). The rest come from the composed ``apps`` node, so an override
    that moved an axis (a language pack pinning a layout, say) records the
    clip under what it really shows rather than what was asked for.
    """
    apps = config.apps
    theme = apps.get("theme") or {}
    return "/".join(
        (
            device,
            theme.get("name") or "default",
            apps.start_page.get("content_language") or "default",
            str(apps.start_page.layout),
            str(apps.todo.layout),
        )
    )


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def clip_axis_options(catalog) -> dict[str, list[str]]:
    """Every option of every clip axis (``--full``)."""
    return {axis: [o.name for o in catalog.groups[axis].options] for axis in CLIP_AXES}


def plan(catalog, axes: dict[str, list[str]], clips: bool, stills: bool) -> list[Job]:
    """Clip jobs for the product of ``axes`` (in CLIP_AXES order; an axis
    left out is its default), then the still jobs."""
    defaults = clip_axis_defaults(catalog)
    jobs: list[Job] = []
    if clips:
        values = [axes.get(axis) or [defaults[axis]] for axis in CLIP_AXES]
        for combo in itertools.product(*values):
            key = "/".join(combo)
            jobs.append(
                Job(
                    name=f"clip {key}",
                    overrides=tuple(f"{a}={v}" for a, v in zip(CLIP_AXES, combo)),
                    kind="clip",
                    key=key,
                )
            )
    if stills:
        jobs.extend(_still_jobs(catalog))
    return jobs


def shard(jobs: list[Job], index: int, count: int) -> list[Job]:
    """Shard ``index`` of ``count`` (1-based): every count-th clip from the
    index-th, so each shard gets every device and theme rather than a block
    of one. Shards partition the clips exactly; stills all go to shard 1,
    since :func:`mark_same_as` compares an app's stills with each other."""
    if not 1 <= index <= count:
        raise ValueError(f"shard {index}/{count}: need 1 <= I <= N")
    clips = [job for job in jobs if job.kind == "clip"]
    rest = [job for job in jobs if job.kind != "clip"] if index == 1 else []
    return clips[index - 1 :: count] + rest


def parse_shard(text: str) -> tuple[int, int]:
    try:
        index, count = (int(part) for part in text.split("/"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected I/N, got {text!r}") from None
    if not 1 <= index <= count:
        raise argparse.ArgumentTypeError(f"need 1 <= I <= N, got {text!r}")
    return index, count


def _still_jobs(catalog) -> Iterator[Job]:
    """Few boots, many stills: one server carries a value for every app.

    Each still shows only its own app, so a boot can set a different layout
    for every app at once -- boot i takes each app's i-th layout. Content
    packs are grouped by *name* instead (one boot sets ``german`` wherever it
    exists): the start page's pack decides which apps are registered (its
    ``long_descriptions`` lists no UI library), so mixing names would 404
    apps that work fine with their own pack.
    """
    for axis in STILL_AXES:
        per_app = {
            path.split("/")[1]: [o.name for o in group.options]
            for path, group in catalog.groups.items()
            if path.startswith("apps/")
            and path.count("/") == 2
            and path.endswith("/" + axis)
            and path.split("/")[1] in APP_ROUTES
        }
        if axis == "content":
            names = sorted({v for values in per_app.values() for v in values})
            batches = [
                (name, [(app, name) for app, vs in per_app.items() if name in vs])
                for name in names
            ]
        else:
            ranks = max((len(v) for v in per_app.values()), default=0)
            batches = [
                (
                    f"#{i + 1}",
                    [(app, vs[i]) for app, vs in per_app.items() if i < len(vs)],
                )
                for i in range(ranks)
            ]
        for label, picks in batches:
            yield Job(
                name=f"stills {axis} {label}",
                overrides=tuple(f"apps/{app}/{axis}={value}" for app, value in picks),
                kind="stills",
                stills=tuple((app, axis, value) for app, value in picks),
            )


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def _fit(viewport: tuple[int, int], box: int) -> dict[str, int]:
    width, height = viewport
    scale = box / max(width, height)
    # Even sizes: the VP8 encoder pads odd ones with a green edge.
    return {
        "width": int(width * scale) // 2 * 2,
        "height": int(height * scale) // 2 * 2,
    }


def _settle(page, seconds: float) -> None:
    try:
        page.wait_for_load_state("load", timeout=15_000)
    except Exception:
        pass
    page.wait_for_timeout(int(seconds * 1000))


def record_clip(browser: Browser, live: LiveApps, job: Job, target: Path) -> None:
    """The tour: the start page, then the todo app, at the device's viewport."""
    dev = device_module.as_dict(live.launcher.config.device)
    viewport = (int(dev["viewport"][0]), int(dev["viewport"][1]))
    with tempfile.TemporaryDirectory() as tmp:
        context = browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            record_video_dir=tmp,
            record_video_size=_fit(viewport, CLIP_BOX),
            **device_module.context_kwargs(dev),
        )
        try:
            page = context.new_page()
            page.goto(live.url + "/", wait_until="domcontentloaded")
            _settle(page, TOUR_HOLD[0])
            # Click through like a person would where the start page shows a
            # visible link; the phone home screen and the gallery differ.
            link = page.locator(f'a[href="{TOUR_APP}"]:visible').first
            try:
                link.click(timeout=3_000)
            except Exception:
                page.goto(live.url + TOUR_APP, wait_until="domcontentloaded")
            _settle(page, TOUR_HOLD[1])
            video = page.video
        finally:
            context.close()  # flushes the video
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(video.path()), target)


def take_stills(
    browser: Browser, live: LiveApps, job: Job, out: Path
) -> dict[str, str]:
    dev = device_module.as_dict(live.launcher.config.device)
    viewport = {"width": int(dev["viewport"][0]), "height": int(dev["viewport"][1])}
    shop_off = _shop_unavailable(live)
    written: dict[str, str] = {}
    context = browser.new_context(
        viewport=viewport, **device_module.context_kwargs(dev)
    )
    try:
        page = context.new_page()
        for app, axis, value in job.stills:
            key = f"{app}/{axis}/{value}"
            if app == "onlineshop" and shop_off:
                print(f"    skip {key}: shop not served ({shop_off})")
                continue
            response = page.goto(
                live.url + APP_ROUTES[app], wait_until="domcontentloaded"
            )
            if response is not None and response.status >= 400:
                print(f"    skip {key}: HTTP {response.status}")
                continue
            _settle(page, 0.8)
            png = out / f"{key}.png"
            png.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(png))
            target = png.with_suffix(".webp")
            with Image.open(png) as image:
                height = round(image.height * STILL_WIDTH / image.width)
                image.convert("RGB").resize((STILL_WIDTH, height), Image.LANCZOS).save(
                    target, "WEBP", quality=STILL_QUALITY, method=6
                )
            png.unlink()
            written[key] = target.relative_to(out.parent).as_posix()
    finally:
        context.close()
    return written


def _shop_unavailable(live: LiveApps) -> str | None:
    from open_apps.apps.availability import onlineshop_unavailable_reason

    return onlineshop_unavailable_reason(live.launcher.config.apps)


_SANDBOX_FLAGS = {
    "chromium_sandbox": False,
    "args": ["--no-sandbox", "--single-process", "--disable-gpu"],
}


@contextmanager
def fresh_browser(playwright, flags: dict) -> Iterator[Browser]:
    """A browser for one job's single context.

    One per job, not one per run: with ``--single-process`` (all that
    launches in some sandboxes) Chromium dies when its first context closes.
    A launch is about a second, next to a ~20 s boot.
    """
    browser = playwright.chromium.launch(**flags)
    try:
        yield browser
    finally:
        browser.close()


def browser_flags(playwright) -> dict:
    """Default launch flags if Chromium starts with them, else the sandbox's."""
    try:
        playwright.chromium.launch().close()
        return {}
    except Exception:
        return dict(_SANDBOX_FLAGS)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def booted(
    jobs: list[Job], runtime: Path, workers: int
) -> Iterator[tuple[Job, Future]]:
    """Yield ``(job, future LiveApps)`` in order, keeping ``workers`` boots ahead.

    At most ``workers + 1`` servers exist at once (the one being recorded and
    the ones booting), so ``workers + 1`` port windows keep them apart.
    """
    slots = workers + 1
    pending: deque[tuple[Job, Future]] = deque()
    with ThreadPoolExecutor(max_workers=workers) as pool:

        def submit(i: int) -> None:
            job = jobs[i]
            # Windows from 5101 up, clear of a `launch.py` on the default 5001.
            boot: Callable[[], LiveApps] = lambda: start_apps(  # noqa: E731
                list(job.overrides), runtime / f"{i:03d}", job_id=1 + i % slots
            )
            pending.append((job, pool.submit(boot)))

        for i in range(min(workers, len(jobs))):
            submit(i)
        nxt = len(pending)
        try:
            while pending:
                job, future = pending.popleft()
                try:
                    yield job, future
                finally:
                    if nxt < len(jobs):
                        submit(nxt)
                        nxt += 1
        finally:
            # Interrupted: stop whatever had booted ahead, or it outlives us.
            for _, future in pending:
                future.cancel()
            for _, future in pending:
                if not future.cancelled():
                    try:
                        future.result().stop()
                    except Exception:
                        pass


def still_src(entry: str | dict) -> str:
    """A still entry's file: the entry itself, or its ``src`` once marked."""
    return entry if isinstance(entry, str) else entry["src"]


def axis_defaults(catalog) -> dict[str, str]:
    """``"<app>/<axis>"`` -> the value config.yaml composes for that axis."""
    return {
        path.removeprefix("apps/"): group.defaults.get("config")
        for path, group in catalog.groups.items()
        if path.startswith("apps/")
        and path.endswith(tuple("/" + a for a in STILL_AXES))
    }


def mark_same_as(
    stills: dict[str, str | dict], out: Path, defaults: dict[str, str]
) -> dict[str, str | dict]:
    """Stills with every byte-identical duplicate marked ``same_as`` its twin.

    Within one app only (another app's identical bytes say nothing about this
    one). Of a set of identical stills, the one kept plain is the app's
    default layout, else its default content pack, else the first by key --
    so a pack that changes nothing reads "same as the default". Recomputed
    from the files every time (marked entries are read through ``src``), so
    it is idempotent and a re-render that makes a still distinct unmarks it.
    """

    def rank(key: str) -> tuple:
        app, axis, value = key.split("/")
        return (defaults.get(f"{app}/{axis}") != value, STILL_AXES.index(axis), key)

    twins: dict[tuple[str, str], str] = {}
    marked: dict[str, str | dict] = {}
    for key in sorted(stills, key=rank):
        src = still_src(stills[key])
        digest = hashlib.sha256((out / src).read_bytes()).hexdigest()
        twin = twins.setdefault((key.split("/")[0], digest), key)
        marked[key] = src if twin == key else {"src": src, "same_as": twin}
    return dict(sorted(marked.items()))


def load_manifest(path: Path, clip_defaults: dict[str, str]) -> dict:
    """The manifest at ``path`` (empty if none), clip keys made canonical.

    A legacy key loses to its canonical twin, whichever comes first."""
    if not path.exists():
        return {"clips": {}, "stills": {}}
    data = json.loads(path.read_text())
    clips = data.get("clips", {})
    canonical: dict[str, str] = {}
    for key in sorted(clips, key=lambda k: k.count("/")):
        canonical[canonical_clip_key(key, clip_defaults)] = clips[key]
    return {"clips": canonical, "stills": data.get("stills", {})}


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def referenced(manifest: dict) -> set[str]:
    return {still_src(v) for s in ("clips", "stills") for v in manifest[s].values()}


def finish(out: Path, manifest: dict, before: set[str], catalog) -> None:
    """Write the final manifest: entries without a file dropped, stills
    re-marked, and files whose entry was replaced (a legacy clip re-rendered
    under its canonical path, say) deleted rather than left stray."""
    for section in ("clips", "stills"):
        manifest[section] = {
            k: v
            for k, v in sorted(manifest[section].items())
            if (out / still_src(v)).is_file()
        }
    manifest["stills"] = mark_same_as(manifest["stills"], out, axis_defaults(catalog))
    write_manifest(out / "manifest.json", manifest)
    for rel in before - referenced(manifest):
        path = out / rel
        if path.is_file() and path.resolve().is_relative_to(out.resolve()):
            path.unlink()
            for parent in path.parents:
                if parent == out or any(parent.iterdir()):
                    break
                parent.rmdir()


def _inside(root: Path, rel: str) -> Path | None:
    path = (root / rel).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def merge(out: Path, sources: list[Path], catalog) -> list[str]:
    """Layer each source's manifest and files over ``out``'s, in order: a
    later source wins per key. Returns warnings for whatever was skipped (a
    missing or unreadable shard, an entry without its file) -- a partial
    render ships what it has."""
    defaults = clip_axis_defaults(catalog)
    manifest = load_manifest(out / "manifest.json", defaults)
    before = referenced(manifest)
    warnings: list[str] = []
    for source in sources:
        if not (source / "manifest.json").is_file():
            warnings.append(f"{source}: no manifest.json, skipped")
            continue
        try:
            layer = load_manifest(source / "manifest.json", defaults)
        except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError
            warnings.append(f"{source}: unreadable manifest ({exc}), skipped")
            continue
        for section in ("clips", "stills"):
            for key, entry in layer[section].items():
                rel = still_src(entry)
                src, dest = _inside(source, rel), _inside(out, rel)
                if src is None or dest is None or not src.is_file():
                    warnings.append(f"{source}: {key} has no file {rel}, skipped")
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                manifest[section][key] = rel  # stills are re-marked in finish()
    finish(out, manifest, before, catalog)
    return warnings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the Config Composer's static preview media."
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--devices", nargs="*", help="default: every config/device option"
    )
    parser.add_argument(
        "--themes", nargs="*", help="default: every config/apps/theme option"
    )
    parser.add_argument(
        "--languages", nargs="*", help="default: the default (English) only"
    )
    parser.add_argument(
        "--start-layouts", nargs="*", help="default: the start page's default only"
    )
    parser.add_argument(
        "--todo-layouts", nargs="*", help="default: the todo app's default only"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="every option of every clip axis (an axis flag narrows it)",
    )
    parser.add_argument(
        "--shard",
        type=parse_shard,
        metavar="I/N",
        help="render only the I-th of N exact-once slices of the clips",
    )
    parser.add_argument("--no-clips", dest="clips", action="store_false")
    parser.add_argument("--no-stills", dest="stills", action="store_false")
    parser.add_argument("--workers", type=int, default=3, help="servers booting ahead")
    parser.add_argument(
        "--mark-only",
        action="store_true",
        help="only re-mark identical stills in the existing manifest",
    )
    parser.add_argument(
        "--merge",
        nargs="+",
        type=Path,
        metavar="DIR",
        help="layer these rendered dirs (manifest + files) over --out; render nothing",
    )
    return parser.parse_args(argv)


def clip_axes(args: argparse.Namespace, catalog) -> dict[str, list[str]]:
    """The values to render per clip axis. Without ``--full``, device and
    theme default to every option and the rest to their default only (the
    committed set); with it, every axis defaults to every option."""
    every = clip_axis_options(catalog)
    picked = {
        "device": args.devices,
        "apps/theme": args.themes,
        "apps/language": args.languages,
        "apps/start_page/layout": args.start_layouts,
        "apps/todo/layout": args.todo_layouts,
    }
    always = ("device", "apps/theme")
    return {
        axis: values or (every[axis] if args.full or axis in always else [])
        for axis, values in picked.items()
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    catalog = build_catalog()
    out = args.out.resolve()
    manifest_path = out / "manifest.json"
    if args.merge:
        warnings = merge(out, args.merge, catalog)
        manifest = load_manifest(manifest_path, clip_axis_defaults(catalog))
        print(
            f"merged {len(args.merge)} dir(s) into {out}: "
            f"{len(manifest['clips'])} clips, {len(manifest['stills'])} stills"
        )
        for warning in warnings:
            print(f"- {warning}")
        return 1 if warnings else 0
    if args.mark_only:
        manifest = load_manifest(manifest_path, clip_axis_defaults(catalog))
        manifest["stills"] = mark_same_as(
            manifest["stills"], out, axis_defaults(catalog)
        )
        write_manifest(manifest_path, manifest)
        same = sum(isinstance(v, dict) for v in manifest["stills"].values())
        print(f"{same} of {len(manifest['stills'])} stills marked same_as another")
        return 0
    jobs = plan(catalog, clip_axes(args, catalog), args.clips, args.stills)
    if args.shard:
        jobs = shard(jobs, *args.shard)
    manifest = load_manifest(manifest_path, clip_axis_defaults(catalog))
    before = referenced(manifest)
    failures: list[str] = []

    started = time.monotonic()
    with (
        tempfile.TemporaryDirectory(prefix="composer-media-") as tmp,
        sync_playwright() as pw,
    ):
        flags = browser_flags(pw)
        for n, (job, future) in enumerate(
            booted(jobs, Path(tmp), max(1, args.workers)), 1
        ):
            print(
                f"[{time.monotonic() - started:6.0f}s] {n}/{len(jobs)} {job.name}",
                flush=True,
            )
            try:
                live = future.result()
            except Exception as exc:  # a bad combination should not end the run
                failures.append(f"{job.name}: {exc}")
                print(f"    failed to boot: {exc}")
                continue
            try:
                with fresh_browser(pw, flags) as browser:
                    if job.kind == "clip":
                        device = job.key.split("/")[0]
                        key = composed_clip_key(device, live.launcher.config)
                        if key != job.key:
                            print(f"    composed {key}, not {job.key}: kept as {key}")
                        rel = clip_path(key)
                        record_clip(browser, live, job, out / rel)
                        manifest["clips"][key] = rel
                    else:
                        stills = take_stills(browser, live, job, out / "stills")
                        manifest["stills"].update(stills)
                # A checkpoint, so a shard killed at its time limit still
                # leaves a manifest for what it did render.
                write_manifest(manifest_path, manifest)
            except Exception as exc:
                failures.append(f"{job.name}: {exc}")
                print(f"    failed: {exc}")
            finally:
                live.stop()

    finish(out, manifest, before, catalog)

    files = [p for p in out.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(
        f"\n{len(manifest['clips'])} clips, {len(manifest['stills'])} stills, "
        f"{total / 1e6:.1f} MB in {out} ({time.monotonic() - started:.0f} s)"
    )
    for failure in failures:
        print(f"- {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
