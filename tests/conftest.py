"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Shared fixtures."""

import shutil
from pathlib import Path

import pytest

from open_apps import config_dir

#: A minimal frozen suite, shaped like the leaderboard branch's
#: config/benchmark/align_v1.yaml: a ``_global_`` option that pins
#: ``parallel_tasks`` (the suite brings its own tasks and cells) and the step
#: budget.
ALIGN_V1 = """\
# @package _global_
# OpenApps-Align v1 -- the frozen leaderboard suite.
#
#   uv run launch_parallel_agents.py benchmark=align_v1 agent=<name>
benchmark:
  name: align_v1
browsergym_env_args:
  max_steps: 45  # not the default (30), so the catalog records it
parallel_tasks:
  task_names: null
  app_variations: null
"""


@pytest.fixture(scope="session")
def benchmark_config_dir(tmp_path_factory) -> Path:
    """``config/`` as it is once the leaderboard branch lands: a ``benchmark``
    group that only config_parallel_tasks.yaml's defaults list names
    (``- benchmark: null`` after ``_self_``), so the composer's handling of a
    group outside config.yaml is tested on any branch."""
    root = tmp_path_factory.mktemp("benchmark-config") / "config"
    shutil.copytree(config_dir(), root)
    (root / "benchmark").mkdir()
    (root / "benchmark" / "align_v1.yaml").write_text(ALIGN_V1)
    # The branch also ships this beside the suite: listed, but not a suite.
    (root / "benchmark" / "heldout_seeds.example.yaml").write_text(
        "# Example only -- not a benchmark.\nseeds: [1, 2]\n"
    )
    sweep = root / "config_parallel_tasks.yaml"
    text = sweep.read_text()
    assert "\n  - _self_\n" in text
    sweep.write_text(
        text.replace("\n  - _self_\n", "\n  - _self_\n  - benchmark: null\n", 1)
    )
    return root
