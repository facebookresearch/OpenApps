"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
"""The web app stops when the run that started it is gone, however it went.

An agent run starts the apps as ``bash -c "... uv run launch.py ..."`` in a
session of its own (``start_new_session=True``), so that the terminal's
Ctrl-C reaches the run and not the server, and the run can stop the server in
order: ``AgentLauncher.cleanup`` signals the whole group. But that only
happens if the run gets to its cleanup. A run that is SIGKILLed -- a sweep's
second Ctrl-C, its grace period running out, the OOM killer, ``kill -9`` --
never does, and nothing else signals a separate session. The server kept
serving on the run's port until someone found it and killed it by hand.

So the server does not rely on being stopped. The run holds the write end of
a pipe and hands the read end to the server (``LIFELINE_FD_ENV`` names its
descriptor). Nothing is ever written; the server's watcher thread just blocks
on a read. When the run exits for any reason, the kernel closes its end, the
read returns end-of-file at once, and the server stops its own process group:
SIGTERM, then SIGKILL after a grace period. No polling, and no pid that could
be reused by an unrelated process.

The write end is non-inheritable, so programs the run starts (a browser, the
web app itself) do not hold it open. A child the run *forks* without exec
would, and the server would then outlive the run until that child exits too.

A server started by hand (``uv run launch.py``) has no lifeline and behaves
as before.
"""

import os
import signal
import threading
import time

#: Names the inherited descriptor of the lifeline's read end.
LIFELINE_FD_ENV = "OPENAPPS_LIFELINE_FD"

#: How long the group gets to stop on SIGTERM before it is killed.
STOP_GRACE_S = 10.0


def open_lifeline() -> tuple[int, int]:
    """A new lifeline: ``(read_end, write_end)``.

    Pass ``read_end`` to the child (``pass_fds`` plus ``LIFELINE_FD_ENV``),
    close it in the parent, and keep ``write_end`` open for as long as the
    child should live.
    """
    return os.pipe()


def watch_lifeline_from_env(grace: float = STOP_GRACE_S) -> threading.Thread | None:
    """Start watching the lifeline this process was given, if it was given one.

    Takes the variable out of the environment, so the server's own children do
    not mistake it for theirs.
    """
    fd = os.environ.pop(LIFELINE_FD_ENV, None)
    if fd is None:
        return None
    return watch_lifeline(int(fd), grace)


def watch_lifeline(fd: int, grace: float = STOP_GRACE_S) -> threading.Thread:
    """Stop this process's group once the other end of ``fd`` is closed."""
    os.set_inheritable(fd, False)

    def watch() -> None:
        try:
            while os.read(fd, 64):  # nothing is written: this is the wait
                pass
        except OSError:
            pass
        print(
            "The run that started OpenApps is gone; stopping the web app.",
            flush=True,
        )
        _stop_own_group(grace)

    thread = threading.Thread(target=watch, name="openapps-lifeline", daemon=True)
    thread.start()
    return thread


def _stop_own_group(grace: float) -> None:
    """SIGTERM this process's group, then SIGKILL whatever is left.

    Only the whole group when this process is in a session of its own making
    (the group is the session, as ``start_new_session`` sets up). Otherwise
    the group could be a terminal's job, shared with whatever else was typed
    on that line, and only this process is stopped.
    """
    pgid = os.getpgrp()
    own_session = os.getsid(0) == pgid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            if own_session:
                os.killpg(pgid, sig)
            else:
                os.kill(os.getpid(), sig)
        except ProcessLookupError:
            return
        # Reached only if this process outlives the signal: uvicorn handles
        # SIGTERM by shutting down, and the process exits with it.
        time.sleep(grace)
