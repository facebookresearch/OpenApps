"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The device axis.

One Hydra group (``config/device/``) describes what the environment is being
rendered and driven on: how big the window is, whether the pointer can hover,
what a layout should look like. Everything that needs to know reads it through
this module rather than reaching into the config, so there is one place that
decides what happens when a key is missing.

Two consumers, deliberately kept apart:

* the **browser** -- :func:`context_kwargs` produces the Playwright emulation
  flags, applied by :class:`open_apps.agent.env_args.DeviceEnvArgs` for agent
  runs and by :class:`open_apps.mcp.session.Session` for MCP ones. The window
  size does *not* come from here; it is ``${device.viewport}`` in the env args
  config, where it is visible in the saved config and in W&B.
* the **apps** -- :func:`form_factor` tells a server-rendered page which
  composition to build. ``config/config.yaml`` mirrors the device node into
  ``apps.device`` because the web server is handed ``cfg.apps`` and nothing
  else.

Everything here degrades rather than raises. A config that predates this group,
a hand-built one in a test, an app started straight from ``launch.py`` with an
older saved config -- all resolve to the desktop defaults, because the failure
mode of a strict lookup is a blank page halfway through a sweep.
"""
from __future__ import annotations

from typing import Any

from omegaconf import DictConfig, OmegaConf

#: Every field a device config carries, with the values that mean "a plain
#: desktop browser". Also the fallback for a config that has no device node.
DEFAULT_DEVICE: dict[str, Any] = {
    "name": "desktop",
    "form_factor": "desktop",
    "viewport": [1920, 1080],
    "device_scale_factor": 1,
    "is_mobile": False,
    "has_touch": False,
    "user_agent": None,
}

#: Form factors a layout may branch on. Anything else falls back to `desktop`,
#: so adding a device file with a novel form factor renders *something* rather
#: than nothing while its layouts are being written.
FORM_FACTORS = ("desktop", "tablet", "phone")


def as_dict(device: Any) -> dict[str, Any]:
    """Normalize a device node into a plain dict with every key present.

    Accepts a ``DictConfig``, a dict, or ``None``. Unknown keys are kept --
    a device file is allowed to carry extra fields for a consumer this module
    has not been told about.
    """
    if device is None:
        return dict(DEFAULT_DEVICE)
    if OmegaConf.is_config(device):
        device = OmegaConf.to_container(device, resolve=True)
    if not isinstance(device, dict):
        return dict(DEFAULT_DEVICE)
    return {**DEFAULT_DEVICE, **device}


def from_config(config: Any) -> dict[str, Any]:
    """The device node out of an apps config (or any config carrying one).

    ``config`` is typically ``app.config`` inside an app -- i.e. the ``apps``
    package, which mirrors the top-level ``device`` node.
    """
    if config is None:
        return dict(DEFAULT_DEVICE)
    if isinstance(config, (dict, DictConfig)):
        return as_dict(config.get("device"))
    return as_dict(getattr(config, "device", None))


def form_factor(config: Any) -> str:
    """``desktop`` | ``tablet`` | ``phone`` for the config's device."""
    value = str(from_config(config).get("form_factor") or "desktop")
    return value if value in FORM_FACTORS else "desktop"


def is_touch(config: Any) -> bool:
    """Whether the configured device has a touch pointer."""
    return bool(from_config(config).get("has_touch", False))


def viewport(config: Any) -> tuple[int, int]:
    """``(width, height)`` in CSS pixels for the config's device."""
    size = from_config(config).get("viewport") or DEFAULT_DEVICE["viewport"]
    return int(size[0]), int(size[1])


def context_kwargs(device: Any) -> dict[str, Any]:
    """Playwright ``new_context`` kwargs for a device, minus the viewport.

    Chromium-only (``is_mobile`` raises on Firefox), which is what BrowserGym
    and the MCP session both drive.

    Only non-default values are returned. Passing ``is_mobile=False`` and
    ``device_scale_factor=1`` explicitly would be identical in effect, but an
    empty dict lets the caller skip touching the context at all on a desktop
    run, which keeps the stock code path stock.
    """
    resolved = as_dict(device)
    kwargs: dict[str, Any] = {}
    if resolved.get("is_mobile"):
        kwargs["is_mobile"] = True
    if resolved.get("has_touch"):
        kwargs["has_touch"] = True
    scale = resolved.get("device_scale_factor") or 1
    if scale != 1:
        # See config/device/desktop.yaml: screenshots are captured in device
        # pixels and actions dispatched in CSS pixels, and nothing rescales
        # between them, so this is a foot-gun the configs keep at 1.
        kwargs["device_scale_factor"] = scale
    if resolved.get("user_agent"):
        kwargs["user_agent"] = resolved["user_agent"]
    return kwargs
