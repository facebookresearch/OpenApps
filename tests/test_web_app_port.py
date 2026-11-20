"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Whatever starts ``launch.py`` tells it the port it is going to poll.

The parent picks a free port (``OpenAppsLauncher.pick_empty_port``) and then
waits for the apps to answer there. ``launch.py`` builds its own launcher from
the saved config, which has no port in it, so a child left to itself picks
again -- the same port only while nothing else on the host is starting a
server. Sibling SLURM jobs, the composer media script's parallel servers and
``pytest -n`` workers all are, and the parent then times out on a port nobody
serves. Each spawner is checked by capturing the command it would run.
"""

import subprocess

import pytest


class _Spawned(Exception):
    pass


@pytest.fixture
def captured(monkeypatch):
    """The argv of the ``launch.py`` child, which is not started. Other
    ``Popen`` calls (gitpython reading the commit) go through."""
    seen: list[list[str]] = []
    real_popen = subprocess.Popen

    def fake_popen(command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and "launch.py" in command:
            seen.append(list(command))
            raise _Spawned
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return seen


@pytest.fixture
def picked(monkeypatch):
    """Every port the parent's launcher picks, in order."""
    from open_apps.launcher import OpenAppsLauncher

    ports: list[int] = []
    original = OpenAppsLauncher.pick_empty_port

    def pick(self, start_port=5001):
        ports.append(original(self, start_port=start_port))
        return ports[-1]

    monkeypatch.setattr(OpenAppsLauncher, "pick_empty_port", pick)
    return ports


def _port_override(argv: list[str]) -> int:
    (value,) = [a.split("=", 1)[1] for a in argv if a.startswith("++web_app_port=")]
    return int(value)


def test_the_composer_preview_passes_its_port(captured, picked, tmp_path):
    from open_apps.compose import live

    with pytest.raises(_Spawned):
        live.start_apps([], tmp_path / "run")
    assert _port_override(captured[0]) == picked[0]


def test_the_screenshot_harness_passes_its_port(captured, picked, tmp_path):
    from tests import save_screenshots

    with pytest.raises(_Spawned):
        save_screenshots.launch_variation("port", tmp_path / "run", overrides=[])
    assert _port_override(captured[0]) == picked[0]
