"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Shared fixtures."""

import os
import shutil
from pathlib import Path

import pytest

from open_apps import config_dir

#: Ports per ``pytest -n`` worker. Wider than a sweep's 100-port job windows
#: (``OpenAppsLauncher._job_port_offset``), so a test that launches job 0..19
#: stays inside its own worker's block.
XDIST_PORT_BLOCK = 2000


@pytest.fixture(scope="session", autouse=True)
def _port_block_per_xdist_worker():
    """Under ``pytest -n``, start each worker's port search in its own block.

    ``pick_empty_port`` takes the first port it can bind and then lets go of
    it, so two workers starting a web app at the same moment both see 5001
    free, and the second server dies on bind -- or worse, the first worker's
    test polls the second's server. Workers are separate processes on one
    ``localhost``, the same shape as sibling SLURM jobs on a node, which get
    the same treatment from ``job_id``. A run without ``-n`` is untouched.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    if not worker.startswith("gw"):
        yield
        return
    from open_apps.launcher import OpenAppsLauncher

    # gw0 starts at 7001; wraps before the search would run past 65535.
    offset = (int(worker[2:]) % 29 + 1) * XDIST_PORT_BLOCK
    original = OpenAppsLauncher.pick_empty_port

    def pick_in_block(self, start_port=5001):
        return original(self, start_port=start_port + offset)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(OpenAppsLauncher, "pick_empty_port", pick_in_block)
        yield


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
