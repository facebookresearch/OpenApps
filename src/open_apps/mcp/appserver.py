"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

OpenApps control plane: the FastHTML server + live Hydra config.

``AppServer`` spawns the OpenApps FastHTML app in a uvicorn daemon
thread, owns the live Hydra config, and drives app state
(reset/reconfigure/get_state). It is **browser-free** — actions and
observations live one layer up in :class:`open_apps.mcp.session.Session`.
This split lets reward/state/reconfigure run without Playwright.

The FastHTML ``app`` and its route table bind to module-level globals
upstream, so only one ``AppServer`` can be alive per Python process
(use one process per session for parallelism).
"""

from __future__ import annotations

import shutil
import socket
import tempfile
import threading
import time
from typing import Any

import requests
import uvicorn
from hydra import compose, initialize_config_dir
from omegaconf import DictConfig, OmegaConf, open_dict

from open_apps import config_dir
from open_apps.apps.start_page.main import (
    AVAILABLE_APPS,
    app as _fasthtml_app,
    initialize_routes_and_configure_task,
    reset_all_apps,
)
from open_apps.mcp.registry import config_dir_for, url_path_for
from open_apps.state import get_current_state


__all__ = ["AppServer"]


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
# AppServer.


class AppServer:
    """Embeddable OpenApps control plane (FastHTML server + Hydra config).

    Owns a FastHTML server thread, the live Hydra config, and the
    sqlite/filesystem state. Browser-free: reward (``get_state``) and
    ``reconfigure`` run without Playwright.
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

        # Called exactly once per process. Upstream's route getters all
        # return the shared global ``app.routes`` and this extends it, so
        # a second call would duplicate routes — never call it again
        # (reset/reconfigure mutate config + sqlite, not routes).
        initialize_routes_and_configure_task(self.config.apps)
        self._asgi_app = _fasthtml_app

        self._server = uvicorn.Server(
            uvicorn.Config(
                self._asgi_app,
                host=self.host,
                port=self.port,
                log_level="warning",
                # Plain asyncio, not uvloop: uvloop installs an event-loop
                # policy without a child watcher, which breaks the async
                # Playwright driver subprocess on Python < 3.12 (see
                # open_apps.mcp.session._ensure_subprocess_capable_policy).
                loop="asyncio",
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        _wait_until_healthy(self.base_url)

    def reset(self) -> None:
        """Reset every app's state (drop sqlite/filesystem, re-seed from config)."""
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
        config update propagates without restarting the server. This
        only swaps appearance/content/seed/extras + re-seeds sqlite; it
        cannot add or remove apps (the registered set is fixed at init).

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
                    OmegaConf.update(self.config, dotted, value, merge=False)

        # Routes read ``app.config`` per request, but the assignment above
        # rebinds ``self.config.apps`` to a fresh node — so re-point the
        # FastHTML app at it, otherwise the page keeps rendering the
        # pre-reconfigure appearance/content.
        _fasthtml_app.config = self.config.apps

        shutil.rmtree(new_tmp_logs, ignore_errors=True)

    def get_state(self) -> dict:
        """Probe the running server for the current cross-app state."""
        return get_current_state(self.base_url)

    def url_for(self, app_name: str | None = None) -> str:
        """Absolute URL of an app's landing page (defaults to ``self.app_name``)."""
        return f"{self.base_url}{url_path_for(app_name or self.app_name)}"

    def registered_apps(self) -> list[str]:
        """App keys actually registered this process (Java-aware, post-init).

        ``onlineshop`` is only present if config-enabled AND Java 21+ is
        installed; map planning is likewise gated. Reflects the live
        ``AVAILABLE_APPS`` after ``initialize_routes_and_configure_task``,
        not the static registry.
        """
        return list(AVAILABLE_APPS.keys())

    def close(self) -> None:
        try:
            self._server.should_exit = True
            self._thread.join(timeout=5.0)
        except Exception:
            pass
        shutil.rmtree(self._tmp_logs, ignore_errors=True)
