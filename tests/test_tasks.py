"""
Test the logic of tasks
"""

import pytest
from starlette.testclient import TestClient
from hydra import initialize, compose
from pathlib import Path
from open_apps.apps.start_page.main import (
    app,
    initialize_routes_and_configure_task,
)
from open_apps.tasks.add_tasks_to_browsergym import get_current_state
from omegaconf import OmegaConf
from pathlib import Path
from hydra.utils import instantiate
from open_apps.tasks.tasks import AddEventTask, are_dicts_similar


class TestTasks:

    @pytest.fixture(scope="module")
    def client(self, tmpdir_factory):
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
        task_config = tasks_config.add_meeting_with_dennis_task
        task = instantiate(task_config)
        assert isinstance(task, AddEventTask)
        assert "Go to the Calendar" in task.goal


class TestTaskReward:
    def test_state_comparison(self):
        dict1 = {"name": "Alice", "city": "NEW YORK "}
        dict2 = {"name": "alice", "city": "new york"}
        assert are_dicts_similar(dict1, dict2)
