"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The initial app state a suite cell starts from, composed the way a run
composes it -- without a browser or a server port.

A run's initial state is ``get_current_state`` against apps seeded from the
cell's ``apps`` config, after the launcher's ``merge_plus_keys`` (a content
pack's ``+filesystem`` is appended to ``filesystem`` there, not by Hydra).
This seeds the same apps in-process with that config and reads the same
``/<app>_all`` endpoints through Starlette's ``TestClient``.

The apps keep their seeded data in module globals, so one process can seed
one variation only: each variation runs in its own subprocess, a few in
parallel (~10 s each, dominated by importing the apps).

    uv run python -m tests._cell_states '["apps/theme=default", "apps/todo/content=german"]' out.json
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]


def _probe(overrides: Sequence[str], out: Path) -> None:
    """Seed the apps for ``overrides`` and write their state to ``out``."""
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf

    from open_apps import config_dir
    from open_apps.state import STATE_ENDPOINTS
    from open_apps.utils import merge_plus_keys

    logs = tempfile.mkdtemp(prefix="openapps-cell-state-")
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        config = compose(
            config_name="config", overrides=[f"logs_dir={logs}", *overrides]
        )
    OmegaConf.resolve(config)
    with contextlib.redirect_stdout(io.StringIO()):  # the apps print as they seed
        config = merge_plus_keys(config)
        Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
        from starlette.testclient import TestClient

        from open_apps.apps.start_page.main import (
            app,
            initialize_routes_and_configure_task,
        )

        initialize_routes_and_configure_task(config.apps)
        client = TestClient(app)
        state = {}
        for key, endpoint in STATE_ENDPOINTS:
            response = client.get(endpoint)
            # A 4xx is an app that is not served: empty is its true state.
            state[key] = response.json() if response.status_code == 200 else []
        state["openbanking"] = state["openbanking"] or {}
    out.write_text(json.dumps(state))


def initial_state(overrides: Sequence[str]) -> dict:
    """The initial state for one variation, from a fresh subprocess."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "state.json"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests._cell_states",
                json.dumps(list(overrides)),
                str(out),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError(
                f"seeding {list(overrides)} failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
            )
        return json.loads(out.read_text())


def initial_states(variations: Iterable[Sequence[str]]) -> dict[tuple[str, ...], dict]:
    """``{variation: initial state}`` for each distinct variation."""
    distinct = sorted({tuple(v) for v in variations})
    workers = max(1, min(len(distinct), (os.cpu_count() or 2) // 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(distinct, pool.map(initial_state, distinct)))


if __name__ == "__main__":
    _probe(json.loads(sys.argv[1]), Path(sys.argv[2]))
