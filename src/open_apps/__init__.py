"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

from pathlib import Path


def config_dir() -> Path:
    """Filesystem path to the OpenApps Hydra config directory (the repo ``config/``).

    Resolved relative to this package. Works under editable installs (uv
    workspace, ``pip install -e``); for wheel installs the ``config/`` tree
    must ship with the package.
    """
    return Path(__file__).resolve().parents[2] / "config"


def hello() -> str:
    return "Hello from OpenApps!"
