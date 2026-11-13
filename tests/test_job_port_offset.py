"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from types import SimpleNamespace

import pytest
from omegaconf import OmegaConf

from open_apps.launcher import OpenAppsLauncher

#: pick_empty_port scans this many ports from the window's start.
SCAN = 1000


def offset(job_id) -> int:
    fake = SimpleNamespace(config=OmegaConf.create({"job_id": job_id}))
    return OpenAppsLauncher._job_port_offset(fake)


@pytest.mark.parametrize("job_id", [0, 1, 499, 500, 605, 606, 793, 5000])
def test_every_job_gets_a_full_window_under_65535(job_id):
    assert 5001 + offset(job_id) + SCAN <= 65535


def test_neighbouring_jobs_get_distinct_windows():
    assert len({offset(j) for j in range(500)}) == 500


def test_no_job_id_is_job_zero():
    assert offset(None) == 0
