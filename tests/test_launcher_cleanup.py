"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
import os
import subprocess
import sys
import time

import pytest

from open_apps.launcher import stop_process_group

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
