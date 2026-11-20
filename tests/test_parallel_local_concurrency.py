"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
import json
import os
import signal
import subprocess
import sys
import textwrap
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


def interruptible_job(config) -> None:
    """Stands in for AgentLauncher.launch: cleanup lives in a `finally`."""
    Path(config.logs_dir, "started").touch()
    try:
        time.sleep(config.sleep)
    finally:
        Path(config.logs_dir, "cleaned_up").touch()


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


_SWEEP = textwrap.dedent(
    """
    import sys
    from omegaconf import OmegaConf
    from open_apps.parallel_launcher import run_concurrently
    from tests.test_parallel_local_concurrency import interruptible_job

    if __name__ == "__main__":
        root = sys.argv[1]
        configs = [
            OmegaConf.create({"job_id": i, "task_name": f"task_{i}", "logs_dir": f"{root}/{i}", "sleep": 60})
            for i in range(4)
        ]
        run_concurrently(configs, 2, interruptible_job)
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_ctrl_c_stops_the_sweep_without_starting_queued_jobs(tmp_path):
    """Ctrl-C used to leave through the executor's shutdown(wait=True), which
    started and finished every queued job before the sweep exited."""
    script = tmp_path / "sweep.py"
    script.write_text(_SWEEP)
    root = tmp_path / "runs"
    repo = Path(__file__).resolve().parent.parent
    # Its own session, so the SIGINT below reaches it and its workers -- the
    # foreground process group a terminal Ctrl-C signals -- and not pytest.
    sweep = subprocess.Popen(
        [sys.executable, str(script), str(root)],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(repo)},
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while len(list(root.glob("*/started"))) < 2:
            assert sweep.poll() is None, sweep.stdout.read()
            assert time.monotonic() < deadline, "the first two jobs never started"
            time.sleep(0.1)
        os.killpg(sweep.pid, signal.SIGINT)
        # `uv run` forwards the SIGINT its child already got from the
        # terminal: one Ctrl-C, delivered twice. The echo must not escalate
        # to "kill now" and skip the jobs' cleanup.
        time.sleep(0.05)
        os.kill(sweep.pid, signal.SIGINT)
        output, _ = sweep.communicate(timeout=20)
    finally:
        if sweep.poll() is None:
            os.killpg(sweep.pid, signal.SIGKILL)
    assert sweep.returncode != 0
    assert "cancelled 2 queued job(s), stopping 2 running" in output
    assert sorted(p.parent.name for p in root.glob("*/started")) == ["0", "1"]
    # The running jobs unwound through their `finally` -- where AgentLauncher
    # stops its web app -- rather than being killed outright.
    assert sorted(p.parent.name for p in root.glob("*/cleaned_up")) == ["0", "1"]
