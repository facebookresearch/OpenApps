"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The one OmegaConf -> plain-container conversion the alignment package uses.

Kept in its own module, importing nothing at load time, so the light modules
(``record``, which the hot path of every run imports) can use it without
dragging in OmegaConf until a config is actually handed to them.
"""

from __future__ import annotations

from typing import Any


def plain(node: Any, *, resolve: bool = True) -> Any:
    """``node`` as plain dicts/lists when it is an OmegaConf config; anything
    else unchanged.

    ``resolve=False`` for trees that interpolate run-time values (the
    ``apps`` tree's ``${hydra:...}`` and ``${databases_dir}``) that need not
    resolve where the caller only reads literal flags.
    """
    try:
        from omegaconf import OmegaConf
    except ImportError:  # pragma: no cover - omegaconf is a hard dependency
        return node
    if OmegaConf.is_config(node):
        return OmegaConf.to_container(node, resolve=resolve)
    return node
