"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
import json
import os
import re
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
    import sys, threading
    from omegaconf import OmegaConf
    from open_apps.parallel_launcher import run_concurrently
    from tests.test_parallel_local_concurrency import interruptible_job

    def sweep(root):
        configs = [
            OmegaConf.create({"job_id": i, "task_name": f"task_{i}", "logs_dir": f"{root}/{i}", "sleep": 60})
            for i in range(4)
        ]
        run_concurrently(configs, 2, interruptible_job)

    if __name__ == "__main__":
        root, where = sys.argv[1], sys.argv[2]
        if where == "main":
            sweep(root)
        else:
            # As a downstream package embedding the launcher might: the sweep
            # off the main thread, where it cannot install signal handlers.
            stopped, done = [], threading.Event()
            def run():
                try:
                    sweep(root)
                except KeyboardInterrupt:
                    stopped.append(True)
                finally:
                    done.set()
            threading.Thread(target=run).start()
            # An Event, not Thread.join: a join interrupted by Ctrl-C can leave
            # is_alive() reporting False for a thread that is still running.
            while not done.is_set():
                try:
                    done.wait(0.1)
                except KeyboardInterrupt:
                    pass  # the main thread's Ctrl-C; the jobs got theirs too
            print("sweep thread stopped" if stopped else "sweep thread finished", flush=True)
    """
)


def _start_sweep(tmp_path, where: str):
    script = tmp_path / "sweep.py"
    script.write_text(_SWEEP)
    root = tmp_path / "runs"
    repo = Path(__file__).resolve().parent.parent
    # Its own session, so a SIGINT to the group reaches it and its jobs -- the
    # foreground process group a terminal Ctrl-C signals -- and not pytest.
    sweep = subprocess.Popen(
        [sys.executable, str(script), str(root), where],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(repo)},
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 60
    while len(list(root.glob("*/started"))) < 2:
        if sweep.poll() is not None or time.monotonic() > deadline:
            sweep.kill()
            pytest.fail(f"the first two jobs never started:\n{sweep.communicate()[0]}")
        time.sleep(0.1)
    return sweep, root


def _stop_and_collect(sweep, root, send) -> str:
    try:
        send(sweep.pid)
        output, _ = sweep.communicate(timeout=20)
    finally:
        if sweep.poll() is None:
            os.killpg(sweep.pid, signal.SIGKILL)
    # Off the main thread a job that stopped on its own Ctrl-C has already
    # exited, so "stopping" may count only the other one.
    assert re.search(r"cancelled 2 queued job\(s\), stopping [12] running", output), output
    assert sorted(p.parent.name for p in root.glob("*/started")) == ["0", "1"]
    # The running jobs unwound through their `finally` -- where AgentLauncher
    # stops its web app -- rather than being killed outright.
    assert sorted(p.parent.name for p in root.glob("*/cleaned_up")) == ["0", "1"]
    return output


def _ctrl_c(pid: int) -> None:
    os.killpg(pid, signal.SIGINT)
    # `uv run` forwards the SIGINT its child already got from the terminal:
    # one Ctrl-C, delivered twice. The echo must not escalate to "kill now"
    # and skip the jobs' cleanup.
    time.sleep(0.05)
    os.kill(pid, signal.SIGINT)


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


@posix_only
def test_ctrl_c_stops_the_sweep_without_starting_queued_jobs(tmp_path):
    """Ctrl-C used to leave through the executor's shutdown(wait=True), which
    started and finished every queued job before the sweep exited."""
    sweep, root = _start_sweep(tmp_path, "main")
    _stop_and_collect(sweep, root, _ctrl_c)
    assert sweep.returncode != 0


@posix_only
def test_sigterm_to_the_launcher_stops_the_sweep_the_same_way(tmp_path):
    """`kill <pid>` reaches only the launcher, not the jobs: it has to stop them."""
    sweep, root = _start_sweep(tmp_path, "main")
    _stop_and_collect(sweep, root, lambda pid: os.kill(pid, signal.SIGTERM))


@posix_only
def test_ctrl_c_stops_a_sweep_running_off_the_main_thread(tmp_path):
    """No signal handler can be installed there, so the jobs take Ctrl-C
    themselves; the first to stop on it stops the sweep."""
    sweep, root = _start_sweep(tmp_path, "thread")
    output = _stop_and_collect(sweep, root, _ctrl_c)
    assert "sweep thread stopped" in output


def test_a_stopped_sweep_exits_with_its_signal():
    from open_apps.parallel_launcher import SweepStopped, _sweep_stopped_exit

    assert _sweep_stopped_exit(SweepStopped(signal.SIGINT)).code == 130
    assert _sweep_stopped_exit(SweepStopped(signal.SIGTERM)).code == 143
    assert _sweep_stopped_exit(KeyboardInterrupt()).code == 130
