"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Render the Config Composer's static preview media.

    uv run scripts/render_composer_media.py                  # everything
    uv run scripts/render_composer_media.py --devices phone --themes dark
    uv run scripts/render_composer_media.py --no-clips       # stills only

The docs page cannot run the apps, so it shows pre-rendered approximations
(docs/assets/composer/media/, committed): for every device x theme a few
seconds of low-resolution webm -- the start page, then the todo app opening --
and for every app one still per layout value and per content value. The
component plays the clip for the selected device and theme with the still of
the last app the user touched inset over it.

Every combination is a real server: the apps are booted through
:func:`open_apps.compose.live.start_apps` (OpenAppsLauncher's port window and
``databases_dir`` guard, as the agent path uses) with that device and theme,
and recorded in Playwright's Chromium at the device's own viewport. Boots
dominate the run time, so several are started ahead of the one being
recorded, each in its own port window; recording itself stays on one browser
because Chromium in some sandboxes cannot run two.

Option lists come from the composer catalog, so a theme or layout added under
``config/`` is picked up on the next run. A partial run updates its own
entries in ``manifest.json`` and keeps the rest; entries whose file is gone
are dropped. Manual, like tests/save_screenshots.py -- rerun it when the look
of the apps changes, and keep the total under ~15 MB (it prints the size).
"""

import argparse
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


@dataclass(frozen=True)
class Job:
    """One server boot and what to capture from it."""

    name: str
    overrides: tuple[str, ...]
    #: "clip" (uses device, theme) or "stills" (uses stills).
    kind: str
    device: str = "desktop"
    theme: str | None = None
    stills: tuple[tuple[str, str, str], ...] = ()


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan(
    catalog, devices: list[str], themes: list[str], clips: bool, stills: bool
) -> list[Job]:
    jobs: list[Job] = []
    if clips:
        for device in devices:
            for theme in themes:
                jobs.append(
                    Job(
                        name=f"clip {device}/{theme}",
                        overrides=(f"device={device}", f"apps/theme={theme}"),
                        kind="clip",
                        device=device,
                        theme=theme,
                    )
                )
    if stills:
        jobs.extend(_still_jobs(catalog))
    return jobs


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


def load_manifest(path: Path) -> dict:
    if path.exists():
        data = json.loads(path.read_text())
        return {"clips": data.get("clips", {}), "stills": data.get("stills", {})}
    return {"clips": {}, "stills": {}}


def main(argv: list[str] | None = None) -> int:
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
    parser.add_argument("--no-clips", dest="clips", action="store_false")
    parser.add_argument("--no-stills", dest="stills", action="store_false")
    parser.add_argument("--workers", type=int, default=3, help="servers booting ahead")
    args = parser.parse_args(argv)

    catalog = build_catalog()
    devices = args.devices or [o.name for o in catalog.groups["device"].options]
    themes = args.themes or [o.name for o in catalog.groups["apps/theme"].options]
    jobs = plan(catalog, devices, themes, args.clips, args.stills)
    out = args.out.resolve()
    manifest_path = out / "manifest.json"
    manifest = load_manifest(manifest_path)
    failures: list[str] = []

    started = time.monotonic()
    with (
        tempfile.TemporaryDirectory(prefix="composer-media-") as tmp,
        sync_playwright() as pw,
    ):
        flags = browser_flags(pw)
        for job, future in booted(jobs, Path(tmp), max(1, args.workers)):
            print(f"[{time.monotonic() - started:6.0f}s] {job.name}", flush=True)
            try:
                live = future.result()
            except Exception as exc:  # a bad combination should not end the run
                failures.append(f"{job.name}: {exc}")
                print(f"    failed to boot: {exc}")
                continue
            try:
                with fresh_browser(pw, flags) as browser:
                    if job.kind == "clip":
                        rel = f"clips/{job.device}/{job.theme}.webm"
                        record_clip(browser, live, job, out / rel)
                        manifest["clips"][f"{job.device}/{job.theme}"] = rel
                    else:
                        stills = take_stills(browser, live, job, out / "stills")
                        manifest["stills"].update(stills)
            except Exception as exc:
                failures.append(f"{job.name}: {exc}")
                print(f"    failed: {exc}")
            finally:
                live.stop()

    for section in ("clips", "stills"):
        manifest[section] = {
            k: v for k, v in sorted(manifest[section].items()) if (out / v).exists()
        }
    out.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

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
