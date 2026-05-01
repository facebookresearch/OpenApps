from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from killport import kill_ports
from omegaconf import DictConfig
from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from open_apps.apps.start_page.helper import get_java_version
from open_apps.launcher import OpenAppsLauncher


TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
DEFAULT_OUTPUT_DIR = TESTS_DIR / "generated_screenshots"
DEFAULT_REFERENCE_DIR = TESTS_DIR / "reference_screenshots"
DEFAULT_RUNTIME_DIR = DEFAULT_OUTPUT_DIR / ".runtime"
VIEWPORT = {"width": 1440, "height": 1100}
ROUTE_TIMEOUT_MS = 45_000


@dataclass(frozen=True)
class RouteSpec:
    name: str
    path: str
    selector: str
    full_page: bool = False


@dataclass(frozen=True)
class ComparisonResult:
    matches: bool
    summary: str
    mean_diff: float | None = None
    changed_ratio: float | None = None


ROUTES = (
    RouteSpec("start_page", "/", "#wrapper"),
    RouteSpec("todo", "/todo", "#todo-list"),
    RouteSpec("calendar", "/calendar", ".calendar-table, .agenda-list"),
    RouteSpec("messages", "/messages", "main a[href^='/messages/'], main"),
    RouteSpec("maps", "/maps", "#map"),
    RouteSpec("codeeditor", "/codeeditor/", "#editor"),
    RouteSpec("onlineshop", "/onlineshop/", "input[name='search_query']"),
)


def build_variation_overrides(include_onlineshop: bool) -> dict[str, list[str]]:
    appearance_apps = [
        "start_page",
        "todo",
        "calendar",
        "messenger",
        "maps",
        "code_editor",
    ]
    content_apps = list(appearance_apps)
    onlineshop_overrides = ["apps.onlineshop.enable=True"] if include_onlineshop else []

    if include_onlineshop:
        appearance_apps.append("onlineshop")
        content_apps.append("onlineshop")

    return {
        "default": onlineshop_overrides,
        "dark_theme": onlineshop_overrides
        + [f"apps/{app_name}/appearance=dark_theme" for app_name in appearance_apps],
        "challenging_font": onlineshop_overrides
        + [f"apps/{app_name}/appearance=challenging_font" for app_name in appearance_apps],
        "german": onlineshop_overrides
        + [f"apps/{app_name}/content=german" for app_name in content_apps],
        "long_descriptions": onlineshop_overrides
        + [f"apps/{app_name}/content=long_descriptions" for app_name in content_apps],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Launch OpenApps, save screenshots for selected variations, "
            "and compare them against tests/reference_screenshots when present."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where screenshots will be saved.",
    )
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=DEFAULT_REFERENCE_DIR,
        help="Directory containing reference screenshots.",
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=DEFAULT_RUNTIME_DIR,
        help="Directory used for launcher logs and temporary databases.",
    )
    parser.add_argument(
        "--variation",
        dest="variations",
        nargs="*",
        choices=["default", "dark_theme", "challenging_font", "german", "long_descriptions"],
        default=["default", "dark_theme", "challenging_font", "german", "long_descriptions"],
        help="Variation names to capture.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=ROUTE_TIMEOUT_MS,
        help="Per-page timeout in milliseconds.",
    )
    parser.add_argument(
        "--max-mean-diff",
        type=float,
        default=0.35,
        help="Maximum allowed mean per-channel image difference for a match.",
    )
    parser.add_argument(
        "--max-changed-ratio",
        type=float,
        default=0.0005,
        help="Maximum allowed fraction of changed pixels for a match.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser while capturing screenshots.",
    )
    return parser.parse_args()


def reset_hydra() -> None:
    if GlobalHydra.instance().is_initialized():
        GlobalHydra.instance().clear()


def compose_config(logs_dir: Path, overrides: list[str]) -> DictConfig:
    reset_hydra()
    with initialize_config_dir(version_base=None, config_dir=str(REPO_ROOT / "config")):
        return compose(
            config_name="config",
            overrides=[f"logs_dir={logs_dir}", "use_wandb=False", *overrides],
        )


def launch_variation(variation: str, runtime_dir: Path, overrides: list[str]) -> tuple[OpenAppsLauncher, subprocess.Popen[bytes]]:
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    config = compose_config(runtime_dir, overrides)
    launcher = OpenAppsLauncher(config)

    command = [
        "uv",
        "run",
        "launch.py",
        "--config-path",
        str(launcher.config_path.parent),
        "--config-name",
        launcher.config_path.name,
        "use_wandb=False",
    ]
    if launcher.config.apps.onlineshop.enable:
        command.append("apps.onlineshop.enable=True")

    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    wait_for_server(launcher, process, variation)
    return launcher, process


def wait_for_server(
    launcher: OpenAppsLauncher,
    process: subprocess.Popen[bytes],
    variation: str,
    timeout_seconds: int = 120,
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if process.poll() is not None:
            output = read_process_output(process)
            raise RuntimeError(
                f"OpenApps exited early for {variation}.\n{output}"
            )
        if launcher.is_app_running():
            return
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for OpenApps to start for {variation}.")


def stop_server(launcher: OpenAppsLauncher, process: subprocess.Popen[bytes]) -> None:
    try:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
    finally:
        try:
            kill_ports(ports=[launcher.web_app_port])
        except Exception:
            pass


def read_process_output(process: subprocess.Popen[bytes]) -> str:
    if process.stdout is None:
        return ""
    try:
        output = process.stdout.read()
    except Exception:
        return ""
    return output.decode("utf-8", errors="replace")


def route_url(base_url: str, route: RouteSpec) -> str:
    return f"{base_url.rstrip('/')}{route.path}"


def wait_for_page_ready(page, route: RouteSpec, timeout_ms: int) -> None:
    page.locator(route.selector).first.wait_for(state="attached", timeout=timeout_ms)

    for load_state in ("domcontentloaded", "load"):
        try:
            page.wait_for_load_state(load_state, timeout=min(timeout_ms, 10_000))
        except PlaywrightTimeoutError:
            pass

    wait_for_function(
        page,
        """
        () => document.readyState === "complete"
        """,
        timeout_ms,
    )
    wait_for_function(
        page,
        """
        () => !document.fonts || document.fonts.status === "loaded"
        """,
        timeout_ms,
        optional=True,
    )
    wait_for_function(
        page,
        """
        () => Array.from(document.images).every((img) => img.complete)
        """,
        timeout_ms,
        optional=True,
    )

    route_waiters = {
        "maps": """
            () => typeof window.L !== "undefined"
                && document.querySelector("#map") !== null
                && document.querySelector(".leaflet-pane, .leaflet-layer") !== null
        """,
        "codeeditor": """
            () => {
                const editor = document.getElementById("editor");
                return editor !== null
                    && (typeof window.CodeMirror === "undefined" || document.querySelector(".CodeMirror") !== null);
            }
        """,
        "onlineshop": """
            () => {
                const search = document.querySelector("input[name='search_query']");
                const cards = document.querySelectorAll(".card");
                return search !== null && cards.length > 0;
            }
        """,
    }
    route_waiter = route_waiters.get(route.name)
    if route_waiter:
        wait_for_function(page, route_waiter, min(timeout_ms, 15_000), optional=True)
    page.wait_for_timeout(1_000)


def wait_for_function(page, expression: str, timeout_ms: int, optional: bool = False) -> None:
    try:
        page.wait_for_function(expression, timeout=timeout_ms)
    except PlaywrightTimeoutError:
        if not optional:
            raise


def capture_route(page, base_url: str, route: RouteSpec, target_path: Path, timeout_ms: int) -> int:
    response = page.goto(route_url(base_url, route), wait_until="domcontentloaded", timeout=timeout_ms)
    if response is not None and response.status >= 400:
        return response.status

    wait_for_page_ready(page, route, timeout_ms)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target_path), full_page=route.full_page)
    return response.status if response is not None else 200


def compare_images(
    actual_path: Path,
    reference_path: Path,
    diff_path: Path,
    max_mean_diff: float,
    max_changed_ratio: float,
) -> ComparisonResult:
    with Image.open(actual_path) as actual_image, Image.open(reference_path) as reference_image:
        actual = actual_image.convert("RGBA")
        reference = reference_image.convert("RGBA")

        if actual.size != reference.size:
            return ComparisonResult(
                matches=False,
                summary=f"size mismatch: actual={actual.size}, reference={reference.size}",
            )

        diff = ImageChops.difference(actual, reference)
        if diff.getbbox() is None:
            if diff_path.exists():
                diff_path.unlink()
            return ComparisonResult(matches=True, summary="exact match", mean_diff=0.0, changed_ratio=0.0)

        stat = ImageStat.Stat(diff)
        mean_diff = sum(stat.mean) / len(stat.mean)
        alpha_threshold = 3
        changed_pixels = sum(
            1
            for pixel in diff.getdata()
            if max(pixel[:3]) > alpha_threshold or pixel[3] > alpha_threshold
        )
        changed_ratio = changed_pixels / float(actual.size[0] * actual.size[1])
        matches = mean_diff <= max_mean_diff and changed_ratio <= max_changed_ratio

        if matches:
            if diff_path.exists():
                diff_path.unlink()
        else:
            diff_path.parent.mkdir(parents=True, exist_ok=True)
            diff.convert("RGB").save(diff_path)

        return ComparisonResult(
            matches=matches,
            summary=(
                f"mean_diff={mean_diff:.4f}, "
                f"changed_ratio={changed_ratio:.6f}"
            ),
            mean_diff=mean_diff,
            changed_ratio=changed_ratio,
        )


def reference_root_exists(reference_dir: Path) -> bool:
    return reference_dir.exists() and any(reference_dir.rglob("*.png"))


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    runtime_dir = args.runtime_dir.resolve()
    reference_dir = args.reference_dir.resolve()

    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    java_version = get_java_version()
    include_onlineshop = java_version.startswith("21")
    variation_overrides = build_variation_overrides(include_onlineshop)
    compare_against_reference = reference_root_exists(reference_dir)

    saved_paths: list[Path] = []
    skipped_routes: list[str] = []
    missing_references: list[Path] = []
    mismatches: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        try:
            for variation in args.variations:
                launcher = None
                process = None
                context = None
                try:
                    print(f"Capturing variation: {variation}")
                    launcher, process = launch_variation(
                        variation,
                        runtime_dir / variation,
                        variation_overrides[variation],
                    )

                    context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
                    page = context.new_page()

                    for route in ROUTES:
                        if route.name == "onlineshop" and not include_onlineshop:
                            skipped_routes.append(
                                f"{variation}/{route.name}: skipped because Java 21 is unavailable ({java_version})"
                            )
                            continue

                        target_path = output_dir / variation / f"{route.name}.png"
                        print(f"  - {route.name}")
                        try:
                            status = capture_route(
                                page,
                                launcher.web_app_url,
                                route,
                                target_path,
                                args.timeout_ms,
                            )
                        except PlaywrightTimeoutError as exc:
                            skipped_routes.append(
                                f"{variation}/{route.name}: timeout while waiting for page rendering ({exc})"
                            )
                            continue
                        except Exception as exc:
                            skipped_routes.append(
                                f"{variation}/{route.name}: failed to capture screenshot ({exc})"
                            )
                            continue

                        if status >= 400:
                            skipped_routes.append(
                                f"{variation}/{route.name}: server responded with HTTP {status}"
                            )
                            continue

                        saved_paths.append(target_path)

                        if compare_against_reference:
                            reference_path = reference_dir / variation / f"{route.name}.png"
                            if not reference_path.exists():
                                missing_references.append(reference_path)
                                continue

                            diff_path = output_dir / "_diffs" / variation / f"{route.name}.png"
                            comparison = compare_images(
                                target_path,
                                reference_path,
                                diff_path,
                                args.max_mean_diff,
                                args.max_changed_ratio,
                            )
                            if not comparison.matches:
                                mismatches.append(
                                    f"{variation}/{route.name}: {comparison.summary}"
                                )

                finally:
                    if context is not None:
                        context.close()
                    if launcher is not None and process is not None:
                        stop_server(launcher, process)
        finally:
            browser.close()

    print(f"Saved {len(saved_paths)} screenshot(s) under {output_dir}.")

    if compare_against_reference:
        print(f"Compared screenshots against {reference_dir}.")
    else:
        print(f"No reference screenshots found in {reference_dir}; comparison skipped.")

    if skipped_routes:
        print("\nSkipped routes:")
        for skipped in skipped_routes:
            print(f"- {skipped}")

    if missing_references:
        print("\nMissing reference screenshots:")
        for path in missing_references:
            print(f"- {path}")

    if mismatches:
        print("\nMismatched screenshots:")
        for mismatch in mismatches:
            print(f"- {mismatch}")

    if missing_references or mismatches:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
