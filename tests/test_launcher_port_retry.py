"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The web app retries on a fresh port when the one it picked was taken before it
could bind (two jobs on one node with the same job id scan from the same port),
and fails at once on any other startup error.
"""
import subprocess
import sys

import pytest
from omegaconf import OmegaConf

from open_apps.launcher import AgentLauncher

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def _launcher(tmp_path, outcomes):
    """An AgentLauncher whose web app start plays ``outcomes`` in order:
    "taken" (exits after a bind error), "crash" (exits, other error), "ok"."""
    launcher = object.__new__(AgentLauncher)
    launcher.config = OmegaConf.create({"logs_dir": str(tmp_path), "use_wandb": False})
    launcher.web_app_host = "localhost"
    launcher.web_app_port = 14701
    launcher.web_app_url = "http://localhost:14701"
    launcher._webapp_log_fh = None
    started = []
    plan = iter(outcomes)

    def launch_apps_via_shell():
        outcome = next(plan)
        started.append((outcome, launcher.web_app_port))
        log = tmp_path / "webapp.log"
        with open(log, "a") as fh:
            if outcome == "taken":
                fh.write("ERROR:    [Errno 98] error while attempting to bind: address already in use\n")
            elif outcome == "crash":
                fh.write("Traceback: something else broke\n")
        code = "import time; time.sleep(600)" if outcome == "ok" else "pass"
        launcher._webapp_log_fh = open(log, "a")
        return subprocess.Popen([sys.executable, "-c", code], start_new_session=True)

    def wait_until_apps_start(process, times_to_wait=10):
        process.wait(timeout=10) if started[-1][0] != "ok" else None
        if process.poll() is not None:
            raise RuntimeError("Web app process exited before becoming ready")

    launcher.launch_apps_via_shell = launch_apps_via_shell
    launcher.wait_until_apps_start = wait_until_apps_start
    launcher.pick_empty_port = lambda start_port: start_port + 6
    return launcher, started


def test_a_taken_port_is_retried_on_a_new_one(tmp_path):
    launcher, started = _launcher(tmp_path, ["taken", "ok"])
    process = launcher.start_apps()
    try:
        assert [o for o, _ in started] == ["taken", "ok"]
        assert started[1][1] != started[0][1]
        assert launcher.web_app_url == f"http://localhost:{started[1][1]}"
    finally:
        launcher.cleanup(process)


def test_any_other_startup_failure_raises_without_retrying(tmp_path):
    launcher, started = _launcher(tmp_path, ["crash", "ok"])
    with pytest.raises(RuntimeError):
        launcher.start_apps()
    assert [o for o, _ in started] == ["crash"]


def test_a_port_taken_every_time_gives_up_after_the_attempts(tmp_path):
    launcher, started = _launcher(tmp_path, ["taken"] * AgentLauncher.APP_START_ATTEMPTS + ["ok"])
    with pytest.raises(RuntimeError):
        launcher.start_apps()
    assert len(started) == AgentLauncher.APP_START_ATTEMPTS


def test_a_bind_error_from_an_earlier_attempt_does_not_count_again(tmp_path):
    # The log is appended across attempts; only this attempt's part counts.
    launcher, started = _launcher(tmp_path, ["taken", "crash", "ok"])
    with pytest.raises(RuntimeError):
        launcher.start_apps()
    assert [o for o, _ in started] == ["taken", "crash"]
