"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Setup tests for apps
"""

import pytest
from omegaconf import OmegaConf
from open_apps.tasks.tasks import AddEventTask, RemoveEventTask, are_dicts_similar
from hydra.utils import instantiate
from starlette.testclient import TestClient
from hydra import initialize, compose
from pathlib import Path
from open_apps.apps.start_page.main import (
    app,
    initialize_routes_and_configure_task,
)
from open_apps.apps.start_page.helper import (
    get_java_version,
)


@pytest.fixture(scope="module")
def client(tmpdir_factory):
    logs_dir = str(tmpdir_factory.getbasetemp())
    alt_config = [
        "apps/messenger/appearance=challenging_font",
        "apps/messenger/content=misleading_descriptions",
        "apps/calendar/appearance=dark_theme",
    ]
    standard_overrides = [f"logs_dir={logs_dir}"]

    with initialize(version_base=None, config_path="../config/"):
        config = compose(
            config_name="config", overrides=standard_overrides + alt_config
        )
    # make dir
    Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(config.apps)
    print("config apps", config.apps)
    return TestClient(app)


class TestApps:

    def test_homepage(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_messages(self, client):
        response = client.get("/messages")
        assert response.status_code == 200

    def test_messages_all(self, client):
        """checks url used for rewards"""
        response = client.get("/messages_all")
        assert response.status_code == 200

        response_json = response.json()
        assert isinstance(response_json, list)

    def test_todo(self, client):
        response = client.get("/todo")
        assert response.status_code == 200

    def test_calendar(self, client):
        response = client.get("/calendar")
        assert response.status_code == 200

    def test_codeeditor(self, client):
        response = client.get("/codeeditor")
        assert response.status_code == 200

    def test_map(self, client):
        response = client.get("/maps")
        assert response.status_code == 200

    def test_onlineshop(self, client):
        if get_java_version().startswith("21"):
            response = client.get("/onlineshop")
            assert response.status_code == 200
        else:
            # Skip the test if Java version is not 21 or higher
            pytest.skip("Java version is not 21 or higher, skipping onlineshop test.")


class TestTasks:
    def test_homepage(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_get_current_state(self, client):
        response = client.get("/todo_all")
        todo_state = response.json()
        assert type(todo_state) is list
        assert len(todo_state) > 1
        assert "done" in todo_state[0]

    def test_task_instantiation(self):
        tasks_file = Path(__file__).parent.parent / "config/tasks/all_tasks.yaml"
        tasks_config = OmegaConf.load(tasks_file)
        task_config = tasks_config.add_meeting_with_dennis
        task = instantiate(task_config)
        assert isinstance(task, AddEventTask)
        assert "Go to the Calendar" in task.goal

    def test_remove_event_task_instantiation(self):
        tasks_file = Path(__file__).parent.parent / "config/tasks/all_tasks.yaml"
        tasks_config = OmegaConf.load(tasks_file)
        task_config = tasks_config.remove_wacv_abstract_deadline
        task = instantiate(task_config)
        assert isinstance(task, RemoveEventTask)
        assert "Remove the WACV 2026" in task.goal

    def test_state_comparison(self):
        dict1 = {"name": "Alice", "city": "NEW YORK "}
        dict2 = {"name": "alice", "city": "new york"}
        assert are_dicts_similar(dict1, dict2)

    def test_homepage(self, client):
        tasks_file = Path(__file__).parent.parent / "config/tasks/all_tasks.yaml"
        tasks_config = OmegaConf.load(tasks_file)
        task_config = tasks_config.add_meeting_with_dennis
        task = instantiate(task_config)

        initial_state = dict()
        initial_state["calendar"] = client.get("/calendar_all").json()
        current_state = initial_state.copy()
        # add event
        current_state["calendar"].append(task.event)

        assert task.check_if_task_is_complete(initial_state, current_state)
