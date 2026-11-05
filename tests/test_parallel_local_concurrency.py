"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from open_apps.parallel_launcher import RUN_LOG, run_concurrently


# Jobs run in spawned processes, which import them by name: module level only.
def sleepy_job(config) -> None:
    start = time.time()
    print(f"python-out {config.job_id}", flush=True)
    subprocess.run(["echo", f"child-out {config.job_id}"], check=True)
    time.sleep(config.sleep)
    Path(config.logs_dir, "span.json").write_text(json.dumps([start, time.time(), os.getpid()]))


def failing_job(config) -> None:
    if config.job_id == 1:
        raise RuntimeError("boom in job 1")
    sleepy_job(config)


def _configs(tmp_path, n: int, sleep: float = 0.0):
    return [
        OmegaConf.create(
            {"job_id": i, "task_name": f"task_{i}", "logs_dir": str(tmp_path / str(i)), "sleep": sleep}
        )
        for i in range(n)
    ]


def _spans(configs):
    return [json.loads(Path(c.logs_dir, "span.json").read_text()) for c in configs]


def _max_overlap(spans) -> int:
    events = sorted([(s, 1) for s, _, _ in spans] + [(e, -1) for _, e, _ in spans], key=lambda x: (x[0], x[1]))
    running = peak = 0
    for _, delta in events:
        running += delta
        peak = max(peak, running)
    return peak


@pytest.fixture(scope="module")
def two_at_a_time(tmp_path_factory):
    configs = _configs(tmp_path_factory.mktemp("pool"), n=4, sleep=1.5)
    return configs, run_concurrently(configs, max_parallel=2, task=sleepy_job)


def test_runs_at_most_max_parallel_at_once(two_at_a_time):
    configs, failures = two_at_a_time
    assert failures == []
    assert _max_overlap(_spans(configs)) == 2


def test_each_job_gets_a_fresh_process(two_at_a_time):
    configs, _ = two_at_a_time
    assert len({pid for _, _, pid in _spans(configs)}) == len(configs)


def test_run_log_captures_python_and_subprocess_output(two_at_a_time):
    configs, _ = two_at_a_time
    for c in configs:
        log = Path(c.logs_dir, RUN_LOG).read_text()
        assert f"python-out {c.job_id}" in log
        # A child process writes to the inherited file descriptor, not to sys.stdout.
        assert f"child-out {c.job_id}" in log


def test_a_failing_job_is_reported_and_the_rest_still_run(tmp_path):
    configs = _configs(tmp_path, n=3)
    failures = run_concurrently(configs, max_parallel=3, task=failing_job)
    assert [c.job_id for c in failures] == [1]
    assert "boom in job 1" in Path(configs[1].logs_dir, RUN_LOG).read_text()
    assert all(Path(c.logs_dir, "span.json").exists() for c in (configs[0], configs[2]))


def test_run_locally_hands_max_parallel_jobs_to_the_pool_with_their_launcher(monkeypatch, tmp_path):
    from open_apps import parallel_launcher
    from open_apps.launcher import OpenAppsLauncher

    seen = {}

    def fake_pool(configs, max_parallel, task):
        seen.update(configs=configs, max_parallel=max_parallel, task=task)
        return []

    monkeypatch.setattr(parallel_launcher, "run_concurrently", fake_pool)
    configs = _configs(tmp_path, n=3)
    parallel_launcher.run_locally(OmegaConf.create({"max_parallel": 2}), configs, 3, OpenAppsLauncher)
    assert seen["configs"] == configs and seen["max_parallel"] == 2
    assert seen["task"].func is parallel_launcher.run_task
    assert seen["task"].keywords == {"launcher_class": OpenAppsLauncher}


def test_run_locally_exits_non_zero_when_a_pooled_job_fails(monkeypatch, tmp_path):
    from open_apps import parallel_launcher

    monkeypatch.setattr(parallel_launcher, "run_concurrently", lambda configs, n, task: configs[:1])
    with pytest.raises(SystemExit) as exit_info:
        parallel_launcher.run_locally(OmegaConf.create({"max_parallel": 2}), _configs(tmp_path, n=2), 2)
    assert exit_info.value.code == 1
