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
from open_apps.tasks import load_task
from starlette.testclient import TestClient
from hydra import initialize, compose
from pathlib import Path
import json
from open_apps.apps.start_page.main import (
    app,
    initialize_routes_and_configure_task,
)
from open_apps.apps.uilibrary_app.main import ALL_VARIANTS


@pytest.fixture(scope="module")
def client(tmpdir_factory):
    # Its own directory, not the shared base temp: other modules seed their
    # databases there too, and re-seeding on top of them violates UNIQUE keys.
    logs_dir = str(tmpdir_factory.mktemp("test_apps"))
    # Exercise a non-default selection on every axis: the shared theme
    # (global), a per-app theme override, and per-app content.
    alt_config = [
        "apps/theme=challenging_font",
        "apps/messenger/content=misleading_descriptions",
        "apps.calendar.theme=dark",
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

    def test_onlineshop_is_present_by_default(self, client):
        """The shipped default is `content=webshop`, so the shop is there.

        The catalog is committed, so no setup step stands between a clean
        checkout and a storefront. `tests/test_onlineshop.py::TestCatalogGate`
        covers the other side -- a `content` pack with no products leaves the
        routes unregistered rather than serving an empty shop.
        """
        assert client.get("/onlineshop").status_code == 200

    def test_homepage_shows_the_shop_tile(self, client):
        assert 'href="/onlineshop"' in client.get("/").text

    def test_uilibrary(self, client):
        response = client.get("/uilibrary")
        assert response.status_code == 200

    @pytest.mark.parametrize("section", ["tokens", "atoms", "molecules", "brand"])
    def test_uilibrary_sections(self, client, section):
        response = client.get(f"/uilibrary/{section}")
        assert response.status_code == 200

    def test_uilibrary_state_endpoint(self, client):
        """`/uilibrary_all` is the reward substrate: one row per component."""
        response = client.get("/uilibrary_all")
        assert response.status_code == 200
        rows = response.json()
        assert {row["component"] for row in rows} == set(ALL_VARIANTS)
        assert all(
            row["variant"] in ALL_VARIANTS[row["component"]] for row in rows
        )

    def test_uilibrary_variant_knob_persists(self, client):
        """Switching a variant changes stored state, not just the fragment."""
        response = client.post("/uilibrary/atoms/UIButton/variant/danger")
        assert response.status_code == 200
        assert "is-danger" in response.text

        stored = {row["component"]: row for row in client.get("/uilibrary_all").json()}
        assert stored["UIButton"]["variant"] == "danger"

    def test_uilibrary_pin_toggles(self, client):
        before = {r["component"]: r for r in client.get("/uilibrary_all").json()}
        client.post("/uilibrary/atoms/Divider/pin")
        after = {r["component"]: r for r in client.get("/uilibrary_all").json()}
        assert bool(after["Divider"]["pinned"]) != bool(before["Divider"]["pinned"])

    def test_uilibrary_rejects_unknown_variant(self, client):
        """A mistyped agent action must not corrupt the scored state."""
        before = {r["component"]: r for r in client.get("/uilibrary_all").json()}
        response = client.post("/uilibrary/atoms/UIButton/variant/not-a-variant")
        assert response.status_code == 200
        after = {r["component"]: r for r in client.get("/uilibrary_all").json()}
        assert after["UIButton"]["variant"] == before["UIButton"]["variant"]


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
