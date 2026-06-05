"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

import sys
from pathlib import Path


def _ensure_legacy_src_importable() -> None:
    # Some app modules import each other through the legacy ``src.open_apps...``
    # prefix instead of ``open_apps...``. For editable installs (uv workspace,
    # pip install -e) that resolves only when the repo root (sibling of
    # ``src/``) is on sys.path, so add it on package import. Wheel installs
    # don't have a ``src/`` sibling and skip this branch silently.
    pkg_dir = Path(__file__).resolve().parent  # .../src/open_apps/
    src_dir = pkg_dir.parent  # .../src/
    repo_root = src_dir.parent  # .../<openapps-repo>/
    if src_dir.name == "src" and repo_root.is_dir():
        repo_root_str = str(repo_root)
        if repo_root_str not in sys.path:
            sys.path.insert(0, repo_root_str)


_ensure_legacy_src_importable()


def config_dir() -> Path:
    """Filesystem path to the OpenApps Hydra config directory (the repo ``config/``).

    Resolved relative to this package. Works under editable installs (uv
    workspace, ``pip install -e``); for wheel installs the ``config/`` tree
    must ship with the package.
    """
    return Path(__file__).resolve().parents[2] / "config"


def hello() -> str:
    return "Hello from OpenApps!"
