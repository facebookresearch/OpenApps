"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

# Some app modules import each other through the legacy ``src.open_apps...``
# prefix instead of ``open_apps...``. For editable installs (uv workspace,
# pip install -e) that resolves only when the repo root (sibling of
# ``src/``) is on sys.path, so add it on package import. Wheel installs
# don't have a ``src/`` sibling and skip this branch silently.
import sys as _sys
from pathlib import Path as _Path


def _ensure_legacy_src_importable() -> None:
    pkg_dir = _Path(__file__).resolve().parent  # .../src/open_apps/
    src_dir = pkg_dir.parent  # .../src/
    repo_root = src_dir.parent  # .../<openapps-repo>/
    if src_dir.name == "src" and repo_root.is_dir():
        repo_root_str = str(repo_root)
        if repo_root_str not in _sys.path:
            _sys.path.insert(0, repo_root_str)


_ensure_legacy_src_importable()
del _ensure_legacy_src_importable, _sys, _Path


def hello() -> str:
    return "Hello from OpenApps!"
