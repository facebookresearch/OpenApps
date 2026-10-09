"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

BrowserGym env args that can emulate a device.

BrowserGym's ``BrowserEnv`` already accepts everything needed to make Chromium
behave like a phone -- it forwards ``pw_context_kwargs`` straight to
``browser.new_context`` (browsergym/core/env.py:255). Its ``EnvArgs``
dataclass, however, only ever populates that dict with ``storage_state``
(browsergym/experiments/loop.py:67), so from a config file the reachable part
of a device is the window size and nothing else: no touch, no mobile viewport,
no user agent.

This subclass closes that gap without forking the pin. It builds the env the
normal way and then updates the environment's ``pw_context_kwargs`` before the
browser exists -- ``BrowserEnv.__init__`` only stores the dict; the context is
created later, on ``reset()``.

With ``device`` unset it is exactly ``EnvArgs``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from browsergym.experiments import EnvArgs

from open_apps import device as device_module

logger = logging.getLogger(__name__)


@dataclass
class DeviceEnvArgs(EnvArgs):
    """``EnvArgs`` plus the Playwright device-emulation flags.

    ``device`` is the ``config/device/`` node, as a plain dict -- the env args
    config sets ``_convert_: all`` so Hydra hands over containers rather than
    OmegaConf nodes, which matters because ``ExpArgs`` serializes env args to
    JSON and a ``DictConfig`` does not survive that round trip.

    The window size is *not* read from here. It stays
    ``task_kwargs.screen_resolution: ${device.viewport}`` in the config, where
    it is one grep away in a saved run and lands in W&B with everything else.
    """

    device: Optional[dict[str, Any]] = field(default=None)

    def make_env(self, *args, **kwargs):
        env = super().make_env(*args, **kwargs)

        context_kwargs = device_module.context_kwargs(self.device)
        if not context_kwargs:
            # Desktop: nothing to emulate, leave the stock path untouched.
            return env

        target = getattr(env.unwrapped, "pw_context_kwargs", None)
        if target is None:
            # A BrowserGym that no longer keeps this attribute. The viewport
            # still applies, so the run is degraded rather than broken -- but
            # say so, because "the phone eval scored like a desktop one" is
            # otherwise a very quiet failure.
            logger.warning(
                "BrowserGym env exposes no pw_context_kwargs; device emulation "
                "(%s) was not applied. Window size still comes from "
                "task_kwargs.screen_resolution.",
                ", ".join(sorted(context_kwargs)),
            )
            return env

        target.update(context_kwargs)
        return env
