"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Serve the apps for a set of overrides, in a process group we can kill.

Shared by ``compose.py``'s live preview and ``scripts/render_composer_media.py``.
It is the path ``tests/save_screenshots.py`` and ``AgentLauncher`` already
take, not a new one:

1. compose ``config.yaml`` with the overrides and hand it to
   :class:`~open_apps.launcher.OpenAppsLauncher`, which resolves it, picks a
   free port from its window and saves the config under ``logs_dir``;
2. start ``launch.py`` on that saved config in a new session, so the server
   and anything it spawns share one process group;
3. poll :meth:`~open_apps.launcher.OpenAppsLauncher.is_app_running` -- the
   ``databases_dir`` guard -- so a sibling server that happens to answer on
   the port is never mistaken for ours.

Stopping signals the whole process tree, TERM then KILL (:func:`kill_group`):
``serve()`` runs uvicorn, and killing only the direct child can leave a worker
holding the port.
"""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from open_apps import config_dir

REPO_ROOT = config_dir().parent

#: Overrides every live server gets: no W&B run for a preview, and no
#: Playwright window from launch.py -- the caller brings its own browser.
BASE_OVERRIDES = ("use_wandb=False", "headless=True")


class AppsStartError(RuntimeError):
    """The apps did not come up; ``log`` holds the server's output."""

    def __init__(self, message: str, log: str = "") -> None:
        super().__init__(message)
        self.log = log


@dataclass
class LiveApps:
    """A running ``launch.py`` and the launcher that configured it."""

    launcher: Any  # OpenAppsLauncher; Any keeps browsergym out of import time
    process: subprocess.Popen
    log_path: Path

    @property
    def url(self) -> str:
        return self.launcher.web_app_url

    @property
    def pgid(self) -> int:
        # start_new_session=True made the child a group leader.
        return self.process.pid

    def alive(self) -> bool:
        return self.process.poll() is None

    def log_text(self) -> str:
        try:
            return self.log_path.read_text("utf-8", errors="replace")
        except OSError:
            return ""

    def stop(self, timeout: float = 10.0) -> None:
        """Kill the whole process tree; idempotent."""
        kill_group(self.process, timeout)


def group_alive(pgid: int) -> bool:
    """Whether any process is left in group ``pgid``."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _alive(proc: psutil.Process) -> bool:
    try:
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def descendants(pid: int) -> list[psutil.Process]:
    """Every live descendant of ``pid``, however deep, whatever its session."""
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return []


def kill_group(process: subprocess.Popen, timeout: float = 10.0) -> None:
    """TERM, then KILL, ``process``'s group and every descendant; wait until gone.

    Groups, plural. A job's own group is not enough: AgentLauncher starts
    ``uv run launch.py`` in a session of its own, and launch_agent.py does no
    cleanup on SIGTERM, so the app server would be orphaned -- still holding
    its port. So the tree is read first, then each descendant's group is
    signalled as well as the descendant itself. First, because a child whose
    parent dies is re-parented to init and can no longer be found from here.

    psutil is not a new dependency: uv.lock already resolves it (killport,
    a direct dependency, needs it), and it walks the tree the same way on
    macOS and Linux. Its ``send_signal`` re-checks a pid's creation time, so
    a pid recycled mid-stop is never signalled.

    A ``process`` already reaped is left alone: once reaped, its pid -- and
    so its group id -- may belong to an unrelated process.
    """
    if process.returncode is not None:
        return
    tree = descendants(process.pid)
    groups = {process.pid}
    for proc in tree:
        try:
            groups.add(os.getpgid(proc.pid))
        except OSError:
            pass
    groups.discard(os.getpgrp())  # never our own (or pytest's) group
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pgid in groups:
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        for proc in tree:
            try:
                proc.send_signal(sig)
            except psutil.Error:
                pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            process.poll()  # reap the leader, or its zombie keeps the group "alive"
            if not any(group_alive(g) for g in groups) and not any(
                _alive(p) for p in tree
            ):
                break
            time.sleep(0.1)
        else:
            continue  # still there: escalate
        break
    process.poll()


#: Hydra's config state is a process-wide singleton; callers on several
#: threads (the media script's boots, compose.py's request handlers) take
#: turns.
HYDRA_LOCK = threading.Lock()


def compose_config(overrides: list[str], logs_dir: Path) -> Any:
    """``config.yaml`` with ``overrides``, logging under ``logs_dir``.

    Raises Hydra's own exception on a bad override, which callers report.
    """
    from hydra import compose

    from open_apps.compose.catalog import hydra_context

    with HYDRA_LOCK, hydra_context():
        return compose(
            config_name="config",
            overrides=[f"logs_dir={logs_dir}", *BASE_OVERRIDES, *overrides],
        )


def start_apps(
    overrides: list[str],
    runtime_dir: Path,
    timeout: float = 120.0,
    poll_interval: float = 0.5,
    job_id: int = 0,
) -> LiveApps:
    """Serve the apps for ``overrides`` and return once *ours* answer.

    ``runtime_dir`` is this server's ``logs_dir`` (config, databases,
    ``webapp.log``); use a fresh one per server, since the databases are
    seeded on start and ``databases_dir`` is what the hijack guard matches.

    ``job_id`` picks the launcher's port window (``5001 + job_id*100``), as
    for the jobs of a sweep: servers started side by side with distinct ids
    do not race each other for a port.

    The server runs under this interpreter rather than ``uv run``: same
    environment, and no lockfile check on every preview.
    """
    from open_apps.launcher import OpenAppsLauncher

    runtime_dir = Path(runtime_dir).resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if job_id:
        overrides = [*overrides, f"+job_id={job_id}"]
    config = compose_config(list(overrides), runtime_dir)
    launcher = OpenAppsLauncher(config)
    Path(launcher.config.databases_dir).mkdir(parents=True, exist_ok=True)

    log_path = runtime_dir / "webapp.log"
    command = [
        sys.executable,
        "launch.py",
        "--config-path",
        str(launcher.config_path.parent),
        "--config-name",
        launcher.config_path.name,
        *BASE_OVERRIDES,
    ]
    # A file, never an unread PIPE: a full pipe buffer blocks the server on
    # write (the deadlock launcher.launch_apps_via_shell documents). Closed
    # as soon as Popen returns (or raises): the child has its own copy.
    with open(log_path, "ab") as log:
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    live = LiveApps(launcher, process, log_path)

    deadline = time.monotonic() + timeout
    while True:
        if not live.alive():
            live.stop()
            raise AppsStartError("the apps exited while starting", live.log_text())
        # The guard prints on every miss, so knock first: only ask it once
        # something accepts connections on the port. (Not redirect_stdout --
        # that swaps sys.stdout for every thread, and the media script boots
        # several servers at once.)
        if _port_open(launcher.web_app_port) and launcher.is_app_running():
            return live
        if time.monotonic() > deadline:
            live.stop()
            raise AppsStartError(
                f"the apps did not answer on {launcher.web_app_url} within "
                f"{timeout:.0f} s",
                live.log_text(),
            )
        time.sleep(poll_interval)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0
