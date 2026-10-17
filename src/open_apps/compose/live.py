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

Stopping signals the whole group, TERM then KILL: ``serve()`` runs uvicorn,
and killing only the direct child can leave a worker holding the port.
"""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

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
    _log: IO[bytes] | None = field(default=None, repr=False)

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
        """Kill the whole process group; idempotent."""
        kill_group(self.process, timeout)
        if self._log is not None:
            self._log.close()
            self._log = None


def group_alive(pgid: int) -> bool:
    """Whether any process is left in group ``pgid``."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_group(process: subprocess.Popen, timeout: float = 10.0) -> None:
    """TERM, then KILL, the process group ``process`` leads; wait until empty.

    Waits on the group, not just the leader: a uvicorn worker or a Chromium
    the leader started can outlive it, still holding a port or a file.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            process.poll()  # reap the leader, or its zombie keeps the group "alive"
            if not group_alive(process.pid):
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
    # A file, never an unread PIPE: a full pipe buffer blocks the server on
    # write (the deadlock launcher.launch_apps_via_shell documents).
    log = open(log_path, "ab")
    command = [
        sys.executable,
        "launch.py",
        "--config-path",
        str(launcher.config_path.parent),
        "--config-name",
        launcher.config_path.name,
        *BASE_OVERRIDES,
    ]
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    live = LiveApps(launcher, process, log_path, log)

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
