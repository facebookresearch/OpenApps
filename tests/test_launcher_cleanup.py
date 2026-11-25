"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
import contextlib
import os
import subprocess
import sys
import time

import pytest

from open_apps.launcher import _group_alive, stop_process_group

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Reparented to init and not yet reaped counts as gone too.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")


def _wrapper_with_grandchild(tmp_path, child_code: str):
    """Start what launch_apps_via_shell starts: bash wrapping a long-lived child.

    `&&` keeps bash alive as the parent (no exec optimization), which is the
    shape that leaked: terminate() stopped bash and orphaned the child.
    """
    pid_file = tmp_path / "child.pid"
    child = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); {child_code}"
    process = subprocess.Popen(
        f"cd {str(tmp_path)!r} && {sys.executable} -c {child!r}",
        shell=True,
        start_new_session=True,
        executable="/bin/bash",
    )
    deadline = time.monotonic() + 10
    while not (pid_file.exists() and pid_file.read_text()):
        assert time.monotonic() < deadline, "child never started"
        time.sleep(0.05)
    return process, int(pid_file.read_text())


def test_stops_the_server_under_the_shell_wrapper(tmp_path):
    process, child = _wrapper_with_grandchild(tmp_path, "time.sleep(600)")
    stop_process_group(process, timeout=5)
    assert process.poll() is not None
    assert not _alive(child), "the web server outlived cleanup"


def test_kills_a_server_that_ignores_sigterm(tmp_path):
    ignore = "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"
    process, child = _wrapper_with_grandchild(tmp_path, ignore)
    stop_process_group(process, timeout=1)
    assert not _alive(child)


def test_already_exited_process_is_a_no_op(tmp_path):
    process = subprocess.Popen(["true"], start_new_session=True)
    process.wait()
    stop_process_group(process, timeout=1)  # must not raise


def test_a_ctrl_c_during_the_startup_wait_still_stops_the_web_app(monkeypatch):
    """The 30 s head start used to sit in launch_apps_via_shell, before
    launch()'s try/finally, so interrupting it left the web app serving."""
    from open_apps import launcher as launcher_module

    launcher = object.__new__(launcher_module.AgentLauncher)
    started = object()
    stopped = []
    monkeypatch.setattr(launcher, "check_task_can_run", lambda: None, raising=False)
    monkeypatch.setattr(launcher, "launch_apps_via_shell", lambda: started, raising=False)
    monkeypatch.setattr(launcher, "cleanup", stopped.append, raising=False)

    def interrupted(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(launcher_module, "sleep", interrupted)
    with pytest.raises(KeyboardInterrupt):
        launcher.launch()
    assert stopped == [started]


def test_stopping_the_web_app_is_not_cut_short_by_ctrl_c():
    """One Ctrl-C under `uv run` arrives twice; the echo must not land mid-stop."""
    import signal

    from open_apps.launcher import sigint_deferred

    before = signal.getsignal(signal.SIGINT)
    with sigint_deferred():
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.1)  # would raise KeyboardInterrupt here if not deferred
    assert signal.getsignal(signal.SIGINT) is before


# --------------------------------------------------------------------------
# The lifeline: the web app stops when its run is gone (open_apps.lifeline)
# --------------------------------------------------------------------------


def _server_on_a_lifeline(tmp_path, ignore_sigterm: bool = False):
    """A stand-in web app, started the way launch_apps_via_shell starts one:
    bash in a session of its own, holding the read end of a lifeline, with a
    child of its own (as the real server has uv above it and Java beside it).
    Returns (wrapper, write_end, [server pid, grandchild pid])."""
    from open_apps.lifeline import LIFELINE_FD_ENV, open_lifeline

    pids = tmp_path / "pids"
    code = "\n".join(
        [
            "import os, signal, subprocess, time",
            "from open_apps.lifeline import watch_lifeline_from_env",
            *(["signal.signal(signal.SIGTERM, signal.SIG_IGN)"] if ignore_sigterm else []),
            "kid = subprocess.Popen(['sleep', '600'])",
            f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{kid.pid}}')",
            "watch_lifeline_from_env(grace=0.5)",
            "time.sleep(600)",
        ]
    )
    script = tmp_path / "server.py"
    script.write_text(code)
    read_end, write_end = open_lifeline()
    try:
        wrapper = subprocess.Popen(
            f"cd {str(tmp_path)!r} && {sys.executable} {str(script)!r}",
            shell=True,
            start_new_session=True,
            executable="/bin/bash",
            pass_fds=(read_end,),
            env={**os.environ, LIFELINE_FD_ENV: str(read_end)},
        )
    finally:
        os.close(read_end)
    deadline = time.monotonic() + 20
    while not (pids.exists() and len(pids.read_text().split()) == 2):
        assert time.monotonic() < deadline, "the stand-in server never started"
        time.sleep(0.05)
    return wrapper, write_end, [int(p) for p in pids.read_text().split()]


def _gone_within(pids, wrapper, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if wrapper.poll() is not None and not any(_alive(p) for p in pids):
            return True
        time.sleep(0.1)
    return False


def test_the_web_app_stops_its_group_when_the_run_is_gone(tmp_path):
    wrapper, write_end, pids = _server_on_a_lifeline(tmp_path)
    time.sleep(0.5)
    assert all(_alive(p) for p in pids), "stopped before its run was gone"
    os.close(write_end)  # what the kernel does when the run dies, however
    assert _gone_within(pids, wrapper, 10), "the web app outlived its run"


def test_a_web_app_that_ignores_sigterm_is_killed(tmp_path):
    wrapper, write_end, pids = _server_on_a_lifeline(tmp_path, ignore_sigterm=True)
    os.close(write_end)
    assert _gone_within(pids, wrapper, 10), "SIGKILL never followed SIGTERM"


def test_a_web_app_started_by_hand_has_no_lifeline(monkeypatch):
    from open_apps.lifeline import LIFELINE_FD_ENV, watch_lifeline_from_env

    monkeypatch.delenv(LIFELINE_FD_ENV, raising=False)
    assert watch_lifeline_from_env() is None


_KILLED_RUN = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, {repo!r})
from tests.save_screenshots import compose_config
from open_apps.launcher import OpenAppsLauncher

launcher = OpenAppsLauncher(compose_config(Path({logs!r}), []))
process = launcher.launch_apps_via_shell()
if {wait_until_serving!r}:
    deadline = time.monotonic() + 120
    while not launcher.is_app_running():
        assert time.monotonic() < deadline and process.poll() is None, "no web app"
        time.sleep(1)
else:
    time.sleep(1)  # uv has started launch.py, which is still importing the apps
print("READY", process.pid, launcher.web_app_port, flush=True)
time.sleep(600)
"""


@pytest.mark.slow
@pytest.mark.parametrize("wait_until_serving", [True, False], ids=["serving", "starting"])
def test_a_run_killed_outright_does_not_leave_its_web_app_serving(
    tmp_path, wait_until_serving
):
    """SIGKILL: what a sweep's second Ctrl-C or expired grace period sends,
    and what the OOM killer sends. The run never reaches cleanup(). Killed
    while its web app is still starting, too: the server watches from the
    top of launch.py, not from after its seconds of imports."""
    import signal
    import socket
    from pathlib import Path

    repo = str(Path(__file__).resolve().parents[1])
    code = _KILLED_RUN.format(
        repo=repo, logs=str(tmp_path / "run"), wait_until_serving=wait_until_serving
    )
    run = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        for line in run.stdout:
            if line.startswith("READY"):
                _, pgid, port = line.split()
                pgid, port = int(pgid), int(port)
                break
        else:
            pytest.fail("the run never got its web app up")

        run.send_signal(signal.SIGKILL)
        run.wait()

        def serving() -> bool:
            with socket.socket() as sock:
                sock.settimeout(0.5)
                return sock.connect_ex(("127.0.0.1", port)) == 0

        # ~1 s either way. Watching only from launch_apps(), after launch.py's
        # imports, a server killed while starting took ~28 s.
        deadline = time.monotonic() + 10
        while (group_alive := _group_alive(pgid)) or serving():
            assert time.monotonic() < deadline, (
                f"web app still {'running' if group_alive else 'serving'} "
                f"10 s after its run was killed"
            )
            time.sleep(0.2)
    finally:
        if run.poll() is None:
            run.kill()
        with contextlib.suppress(ProcessLookupError, NameError):
            os.killpg(pgid, signal.SIGKILL)
