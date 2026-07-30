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
from open_apps.tasks.tasks import (
    AddEventTask,
    RemoveEventTask,
    AppStateComparison,
    AddToDoTask,
)
from open_apps.tasks import load_task, _load_tasks_cfg
from dataclasses import fields
from starlette.testclient import TestClient
from hydra import initialize, compose
from pathlib import Path
import json
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
        task = load_task("add_meeting_with_dennis")
        assert isinstance(task, AddEventTask)
        assert "Go to the Calendar" in task.goal

    def test_remove_event_task_instantiation(self):
        task = load_task("remove_wacv_abstract_deadline")
        assert isinstance(task, RemoveEventTask)
        assert "Remove the WACV 2026" in task.goal

    def test_state_comparison(self):
        dict1 = {"name": "Alice", "city": "NEW YORK "}
        dict2 = {"name": "alice", "city": "new york"}
        assert AppStateComparison.are_dicts_similar(dict1, dict2)

    def test_homepage(self, client):
        task = load_task("add_meeting_with_dennis")

        initial_state = dict()
        initial_state["calendar"] = client.get("/calendar_all").json()
        initial_state["todo"] = client.get("/todo_all").json()
        initial_state["messenger"] = client.get("/messages_all").json()
        initial_state["map"] = client.get("/maps/landmarks").json()
        current_state = initial_state.copy()
        # add event
        current_state["calendar"].append(task.event)

        assert task.check_if_task_is_complete(initial_state, current_state)

    def test_state_comparison_for_add_todo(self):
        initial_state_path = Path(__file__).parent / "states" / "initial_state.json"
        call_mom_todo_state_path = (
            Path(__file__).parent / "states" / "call_mom_todo_state.json"
        )

        with open(call_mom_todo_state_path, "r", encoding="utf-8") as file:
            call_mom_todo_state = json.load(file)

        with open(initial_state_path, "r", encoding="utf-8") as file:
            initial_state = json.load(file)

        todo_task = AddToDoTask(
            goal="Add a to-do item to call mom", todo_name="Call Mom", is_done=False
        )

        assert todo_task.check_if_task_is_complete(initial_state, call_mom_todo_state)

    def test_state_comparison_for_add_event(self):
        initial_state_path = Path(__file__).parent / "states" / "initial_state.json"
        add_christmas_shopping_state_path = (
            Path(__file__).parent / "states" / "add_christmas_shopping_event.json"
        )

        with open(add_christmas_shopping_state_path, "r", encoding="utf-8") as file:
            add_christmas_shopping_state = json.load(file)

        with open(initial_state_path, "r", encoding="utf-8") as file:
            initial_state = json.load(file)

        add_event_task = AddEventTask(
            goal="Add Shopping for Christmas gifts to my calendar on 2025-12-14",
            title="Shopping for Christmas gifts",
            date="2025-12-14",
            description="",
            url="",
            location="",
            invitees="",
        )

        assert add_event_task.check_if_task_is_complete(
            initial_state, add_christmas_shopping_state
        )


# ``__with_context`` keys and their base tasks, discovered once so the
# coverage/parity test below is parametrized over the real config.
_CONTEXT_KEYS = [
    k for k in _load_tasks_cfg().keys() if k.endswith("__with_context")
]

# Reward-irrelevant fields: everything a context variant is allowed to differ
# on from its base task.
_NON_REWARD_FIELDS = {"goal", "context", "goal_style"}


class TestTaskContext:
    """The optional ``context`` field: loading, prompt composition, task ids,
    reward invariance, and config coverage."""

    def test_base_task_has_no_context(self):
        # Existing (context-free) tasks are unaffected.
        assert load_task("add_meeting_with_dennis").context is None

    def test_context_task_loads_with_reward_fields_intact(self):
        base = load_task("remove_wacv_abstract_deadline")
        ctx = load_task("remove_wacv_abstract_deadline__with_context")
        assert isinstance(ctx, RemoveEventTask)
        assert "WACV 2026" in ctx.context and "later venue" in ctx.context
        # Reward fields are copied verbatim from the base task.
        assert ctx.goal == base.goal
        assert ctx.title == base.title
        assert ctx.date == base.date

    def test_get_goal_prepends_context(self):
        from open_apps.tasks.add_tasks_to_browsergym import OpenAppsTask

        ctx = load_task("remove_wacv_abstract_deadline__with_context")
        env_task = OpenAppsTask(task_config=ctx, base_url="http://localhost:5001")
        goal_text = env_task._get_goal()
        assert goal_text.startswith(ctx.context.rstrip())
        assert goal_text.endswith(f"User goal: {ctx.goal}")

        base = load_task("remove_wacv_abstract_deadline")
        base_task = OpenAppsTask(task_config=base, base_url="http://localhost:5001")
        # No context -> the goal stands alone, exactly as before.
        assert base_task._get_goal() == base.goal

    def test_task_id_stability_and_uniqueness(self):
        base = load_task("remove_wacv_abstract_deadline")
        ctx = load_task("remove_wacv_abstract_deadline__with_context")
        # context=None hashes to the goal-only id (backward compatible)...
        assert base.task_id == RemoveEventTask(
            goal=base.goal, title=base.title, date=base.date
        ).task_id
        # ...and adding context yields a distinct id despite the same goal.
        assert ctx.goal == base.goal
        assert ctx.task_id != base.task_id

    def test_context_does_not_change_reward(self):
        # The context variant rewards identically to the base task.
        initial_state = {
            "calendar": [{"title": "WACV 2026 Abstract Deadline", "date": "2025-07-11"}],
            "todo": [],
            "messenger": [],
            "map": [],
        }
        current_state = {"calendar": [], "todo": [], "messenger": [], "map": []}
        base = load_task("remove_wacv_abstract_deadline")
        ctx = load_task("remove_wacv_abstract_deadline__with_context")
        assert base.check_if_task_is_complete(initial_state, current_state)
        assert ctx.check_if_task_is_complete(initial_state, current_state)

    def test_context_variants_exist(self):
        assert _CONTEXT_KEYS, "expected at least one __with_context task"

    @pytest.mark.parametrize("key", _CONTEXT_KEYS)
    def test_context_variant_matches_base(self, key):
        cfg = _load_tasks_cfg()
        base_key = key[: -len("__with_context")]
        assert base_key in cfg, f"base task {base_key!r} missing for {key!r}"

        ctx = load_task(key)
        base = load_task(base_key)

        # (a) context is a non-empty string.
        assert isinstance(ctx.context, str) and ctx.context.strip()
        # (b) every reward-relevant field matches the base task verbatim.
        for f in fields(base):
            if f.name in _NON_REWARD_FIELDS:
                continue
            assert getattr(ctx, f.name) == getattr(base, f.name), (
                f"{key}: field {f.name!r} drifted from base task"
            )
