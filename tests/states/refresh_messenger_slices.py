"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Re-capture the messenger and contacts slices of the state fixtures.

The fixtures here are hand-curated snapshots: their other slices are trimmed
to what the offline task tests need, so a wholesale re-capture would churn
them for no reason. Only the two slices whose shape the seed owns are
refreshed, from a fresh launch of the default config:

    uv run python tests/states/refresh_messenger_slices.py
"""

import json
import tempfile
from pathlib import Path

from hydra import compose, initialize_config_dir
from starlette.testclient import TestClient

from open_apps import config_dir
from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task

STATES = Path(__file__).parent
FIXTURES = ["initial_state.json", "call_mom_todo_state.json", "add_christmas_shopping_event.json"]


def capture() -> dict:
    logs_dir = tempfile.mkdtemp(prefix="openapps-states-")
    with initialize_config_dir(version_base=None, config_dir=str(config_dir())):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    client = TestClient(app)
    return {
        "messenger": client.get("/messages_all").json(),
        "contacts": client.get("/contacts_all").json(),
    }


def main() -> None:
    fresh = capture()
    for name in FIXTURES:
        path = STATES / name
        state = json.loads(path.read_text(encoding="utf-8"))
        state.update(fresh)
        path.write_text(json.dumps(state, indent=4), encoding="utf-8")
        print(f"refreshed {path.name}")


if __name__ == "__main__":
    main()
