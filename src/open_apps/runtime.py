"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

In-process runtime SDK for embedding OpenApps in other frameworks.

OpenApps's primary surface is a Hydra-driven CLI (``launch.py`` →
``OpenAppsLauncher``). That works for headless agent eval, but is the
wrong shape for embedding OpenApps as a gym env: the consumer needs a
non-blocking, threadable, callable lifecycle they can drive themselves.

This module exposes that SDK surface. A ``Runtime`` is constructed once
per consumer (spawning the FastHTML server in a daemon thread, owning
the live Hydra config), and the consumer drives it with ``reset``,
``reconfigure``, ``get_state``, ``close``. The existing app registry
(``AVAILABLE_APPS``) and reset semantics (``reset_all_apps``) are
reused — this module is a wrapper, not a replacement.
"""

from __future__ import annotations

import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import requests
import uvicorn
from hydra import compose, initialize_config_dir
from omegaconf import DictConfig, OmegaConf, open_dict


__all__ = [
    "APP_URL_PATHS",
    "APP_CONFIG_DIRS",
    "Runtime",
    "config_dir",
    "config_dir_for",
    "list_variants",
    "make_runtime",
    "url_path_for",
]


# ---------------------------------------------------------------------------
# App registry metadata. Single source of truth — consumers should import
# from here rather than maintaining their own copies.

APP_URL_PATHS: dict[str, str] = {
    "todo": "/todo",
    "calendar": "/calendar",
    "messages": "/messages",
    "codeeditor": "/codeeditor/",
    "map": "/maps",
}

APP_CONFIG_DIRS: dict[str, str] = {
    "todo": "todo",
    "calendar": "calendar",
    "messages": "messenger",
    "codeeditor": "code_editor",
    "map": "maps",
}


def url_path_for(app_name: str) -> str:
    """URL path the FastHTML server exposes for an app key."""
    return APP_URL_PATHS.get(app_name, f"/{app_name}")


def config_dir_for(app_name: str) -> str:
    """``config/apps/<dir>`` name for an app key (differs from key for messages/map/codeeditor)."""
    return APP_CONFIG_DIRS.get(app_name, app_name)


def config_dir() -> Path:
    """Filesystem path to the OpenApps Hydra config directory.

    Resolved relative to the installed ``open_apps`` package. Works
    under editable installs (uv workspace, ``pip install -e``); for
    wheel installs the ``config/`` tree must ship with the package.
    """
    import open_apps

    return Path(open_apps.__file__).resolve().parents[2] / "config"


def list_variants(app_name: str, group: str) -> list[str]:
    """List Hydra variant yamls for an app's group (``appearance``/``content``).

    Returns a sorted list of variant stems (without ``.yaml``).
    ``"default"`` is forced to index 0 when present so it has a stable
    sampling identity. Returns ``["default"]`` if the group dir is
    missing.
    """
    group_dir = config_dir() / "apps" / config_dir_for(app_name) / group
    if not group_dir.is_dir():
        return ["default"]
    stems = sorted(p.stem for p in group_dir.glob("*.yaml"))
    if "default" in stems:
        stems.remove("default")
        return ["default"] + stems
    return stems or ["default"]


# ---------------------------------------------------------------------------
# Internal helpers.


def _pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _load_hydra_config(
    extra_overrides: list[str] | None = None,
) -> tuple[DictConfig, str]:
    """Compose the OpenApps Hydra config.

    Returns ``(cfg, tmp_logs_dir)``. The caller owns the tmp dir and
    must clean it up.
    """
    tmp_logs = tempfile.mkdtemp(prefix="openapps_logs_")
    overrides = [f"logs_dir={tmp_logs}", "use_wandb=False"]
    if extra_overrides:
        overrides.extend(extra_overrides)
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        cfg = compose(config_name="config", overrides=overrides)
    return cfg, tmp_logs


def _wait_until_healthy(
    base_url: str, timeout: float = 60.0, poll_interval: float = 1.0
) -> None:
    start = time.monotonic()
    last_error: Any = None
    while time.monotonic() - start < timeout:
        try:
            resp = requests.get(base_url, timeout=3)
            if resp.status_code < 500:
                return
        except requests.ConnectionError as e:
            last_error = e
        except requests.Timeout:
            last_error = "timeout"
        time.sleep(poll_interval)
    raise TimeoutError(
        f"OpenApps server at {base_url} did not become healthy "
        f"within {timeout}s (last error: {last_error})"
    )


# ---------------------------------------------------------------------------
# Runtime.


class Runtime:
    """Embeddable OpenApps runtime.

    Owns a FastHTML server thread, the live Hydra config, and the
    sqlite/filesystem state. Consumers construct one (typically per
    ``gym.Env`` instance) and drive its lifecycle directly.

    The FastHTML ``app`` and uvicorn server bind to module-level
    globals upstream, so only one Runtime can be alive per Python
    process.
    """

    base_url: str
    host: str
    port: int
    config: DictConfig
    app_name: str

    def __init__(
        self,
        app_name: str,
        *,
        port: int | None = None,
        host: str = "127.0.0.1",
        extra_overrides: list[str] | None = None,
    ) -> None:
        self.app_name = app_name
        self.host = host
        self.port = port if port is not None else _pick_free_port(host)
        self.base_url = f"http://{host}:{self.port}"

        self.config, self._tmp_logs = _load_hydra_config(extra_overrides)

        from open_apps.apps.start_page.main import (
            app as _fasthtml_app,
            initialize_routes_and_configure_task,
        )

        initialize_routes_and_configure_task(self.config.apps)
        self._asgi_app = _fasthtml_app

        self._server = uvicorn.Server(
            uvicorn.Config(
                self._asgi_app,
                host=self.host,
                port=self.port,
                log_level="warning",
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        _wait_until_healthy(self.base_url)

    def reset(self) -> None:
        """Reset every app's state (drop sqlite/filesystem, re-seed from config)."""
        from open_apps.apps.start_page.main import reset_all_apps

        reset_all_apps(self.config.apps)

    def reconfigure(
        self,
        *,
        appearance: str | None = None,
        content: str | None = None,
        seed: int | None = None,
        extras: dict[str, Any] | None = None,
    ) -> None:
        """Recompose the Hydra config with new variant choices and seed.

        FastHTML routes read ``app.config`` per-request, so the live
        config update propagates without restarting the server.

        Args:
            appearance: Variant yaml stem under
                ``config/apps/<app>/appearance/`` for ``self.app_name``.
            content: Variant yaml stem under
                ``config/apps/<app>/content/`` for ``self.app_name``.
            seed: Fresh integer seed for the OpenApps content samplers.
            extras: Additional dotpath overrides applied to the live
                config in-place after compose. Example for the maps
                app: ``{"apps.maps.init_location": [40.78, -73.97]}``.
        """
        cfg_dir = config_dir_for(self.app_name)
        overrides: list[str] = []
        if appearance is not None:
            overrides.append(f"apps/{cfg_dir}/appearance={appearance}")
        if content is not None:
            overrides.append(f"apps/{cfg_dir}/content={content}")
        if seed is not None:
            overrides.append(f"seed={int(seed)}")

        new_cfg, new_tmp_logs = _load_hydra_config(overrides)

        OmegaConf.set_struct(self.config, False)
        with open_dict(self.config):
            self.config.apps = new_cfg.apps
            self.config.seed = new_cfg.seed
            if extras:
                for dotted, value in extras.items():
                    OmegaConf.update(
                        self.config, dotted, value, merge=False
                    )

        shutil.rmtree(new_tmp_logs, ignore_errors=True)

    def get_state(self) -> dict:
        """Probe the running server for the current cross-app state."""
        from open_apps.state import get_current_state

        return get_current_state(self.base_url)

    def url_for(self, app_name: str | None = None) -> str:
        """Absolute URL of an app's landing page (defaults to ``self.app_name``)."""
        return f"{self.base_url}{url_path_for(app_name or self.app_name)}"

    def close(self) -> None:
        try:
            self._server.should_exit = True
            self._thread.join(timeout=5.0)
        except Exception:
            pass
        shutil.rmtree(self._tmp_logs, ignore_errors=True)


def make_runtime(
    app_name: str,
    *,
    port: int | None = None,
    host: str = "127.0.0.1",
    extra_overrides: list[str] | None = None,
) -> Runtime:
    """Construct a ``Runtime`` serving the OpenApps FastHTML app in this process."""
    return Runtime(
        app_name, port=port, host=host, extra_overrides=extra_overrides
    )
