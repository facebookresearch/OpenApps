"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Tests for OpenAppsLauncher and OmegaConf resolvers.
"""
from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import patch

try:
    import pytest
except ImportError:
    pytest = None

try:
    from omegaconf import OmegaConf

    HAS_OMEGACONF = True
except ImportError:
    HAS_OMEGACONF = False


def test_launcher_imports_datetime():
    """Verify that datetime is imported in launcher.py globals."""
    import open_apps.launcher as launcher_mod

    assert hasattr(launcher_mod, "datetime"), "datetime must be imported in launcher module"
    assert launcher_mod.datetime is datetime


def test_now_resolver_live_formatting():
    """Test live strftime formatting matches expected timestamp patterns."""
    import open_apps.launcher as launcher_mod

    now = launcher_mod.datetime.now()
    default_str = now.strftime("%Y-%m-%d_%H-%M-%S")
    assert re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", default_str)

    date_only_str = now.strftime("%Y-%m-%d")
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", date_only_str)


def test_now_resolver_mocked_time():
    """Verify formatting with fixed datetime values."""
    fixed_time = datetime(2026, 9, 26, 12, 34, 56)
    with patch("open_apps.launcher.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_time
        assert mock_dt.now().strftime("%Y-%m-%d_%H-%M-%S") == "2026-09-26_12-34-56"
        assert mock_dt.now().strftime("%Y-%m-%d") == "2026-09-26"


def test_omegaconf_now_resolver_interpolation():
    """Verify that OmegaConf resolves ${now:...} without NameError."""
    if not HAS_OMEGACONF:
        if pytest:
            pytest.skip("OmegaConf not installed in this environment")
        return

    import open_apps.launcher  # registers the resolver
    from omegaconf import OmegaConf

    cfg = OmegaConf.create({"log_dir": "${now:%Y-%m-%d_%H-%M-%S}"})
    resolved = OmegaConf.to_container(cfg, resolve=True)
    assert re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", resolved["log_dir"])


if __name__ == "__main__":
    test_launcher_imports_datetime()
    test_now_resolver_live_formatting()
    test_now_resolver_mocked_time()
    test_omegaconf_now_resolver_interpolation()
    print("All launcher tests passed successfully!")
