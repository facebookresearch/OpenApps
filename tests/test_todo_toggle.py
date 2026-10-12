"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Regression tests for the todo checkbox -> reward chain.

The endpoints (``/todo/toggle/{id}``, ``/todo_all``) and the reward comparison
were each covered in isolation, but nothing exercised the link between them:
whether *clicking the checkbox in a browser* actually reaches the database the
reward reads. An agent can only score on this task by clicking that box, so a
break anywhere along the chain (htmx not firing, the swap losing the handler,
the id drifting) silently zeroes every ``MarkToDoDoneTask`` run while the page
still looks correct in a screenshot.
"""

from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.start_page.main import (
    app,
    initialize_routes_and_configure_task,
)
from open_apps.state import get_current_state
from open_apps.tasks.tasks import MarkToDoDoneTask

TODO_TITLE = "Water plants"


def find_todo(todos: list[dict], title: str = TODO_TITLE) -> dict:
    matches = [t for t in todos if t["title"] == title]
    assert matches, f"{title!r} missing from todo state: {todos}"
    return matches[0]


def open_any_browser(playwright, playwright_api, viewport):
    """Open a page in chromium, or any other engine if chromium won't start.

    Chromium is what BrowserGym drives, so it goes first. But it refuses to
    start on some sandboxed/CI hosts (``mach_port_rendezvous`` on macOS, a
    missing shared library on slim images), and skipping there would quietly
    drop the only coverage of the click path. What's under test is the app's
    htmx wiring, not an engine quirk, so a fallback still catches the
    regression.

    A broken install can pass ``launch()`` and still fail ``new_page()``, so
    both run here and a half-working engine falls through to the next.
    Skips only if none of them yields a usable page.
    """
    errors = []
    for name in ("chromium", "firefox", "webkit"):
        browser = None
        try:
            browser = getattr(playwright, name).launch()
            return browser, browser.new_page(viewport=viewport)
        except playwright_api.Error as exc:
            errors.append(f"{name}: {str(exc).splitlines()[0]}")
            if browser is not None:
                try:
                    browser.close()
                except playwright_api.Error:
                    pass
    pytest.skip("no usable playwright browser — " + "; ".join(errors))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # A dedicated temp dir, not the shared ``getbasetemp()``: the todo app
    # seeds its table with fixed primary keys at startup, so re-initializing
    # over another module's database would collide on insert.
    logs_dir = tmp_path_factory.mktemp("todo_toggle")
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(config.apps)
    return TestClient(app)


def test_toggle_endpoint_reaches_reward_endpoint(client):
    """``/todo/toggle`` must be visible to ``/todo_all``, which reward reads."""
    todo = find_todo(client.get("/todo_all").json())
    assert not todo["done"], "fixture expects 'Water plants' to start undone"

    assert client.put(f"/todo/toggle/{todo['id']}").status_code == 200
    assert find_todo(client.get("/todo_all").json())["done"]

    # Toggling back must also propagate — an agent that double-clicks the box
    # has *not* completed the task, and the reward has to reflect that.
    assert client.put(f"/todo/toggle/{todo['id']}").status_code == 200
    assert not find_todo(client.get("/todo_all").json())["done"]


@pytest.mark.slow
def test_browser_checkbox_click_marks_task_complete(tmp_path_factory):
    """Click the real checkbox in a real browser and score the real task.

    This is the path an agent takes. Everything below the click — the htmx
    ``hx_put``, the outerHTML swap, the sqlite write, ``/todo_all``, and the
    DeepDiff in ``MarkToDoDoneTask`` — has to line up for the run to score.
    """
    playwright_api = pytest.importorskip("playwright.sync_api")

    from tests.save_screenshots import launch_variation, stop_server

    runtime_dir = tmp_path_factory.mktemp("todo_toggle_browser")
    launcher, process = launch_variation("todo_toggle", runtime_dir, overrides=[])
    try:
        base_url = launcher.web_app_url
        initial_state = get_current_state(base_url)
        todo = find_todo(initial_state["todo"])
        assert not todo["done"], "fixture expects 'Water plants' to start undone"

        task = MarkToDoDoneTask(
            goal=f"Mark '{TODO_TITLE}' as done in my todo list.",
            todo_name=TODO_TITLE,
        )
        assert not task.check_if_task_is_complete(
            initial_state, get_current_state(base_url)
        ), "task scored as complete before anything was clicked"

        checkbox = f"#todo-{todo['id']} input[type=checkbox]"
        with playwright_api.sync_playwright() as p:
            browser, page = open_any_browser(
                p, playwright_api, viewport={"width": 1024, "height": 640}
            )
            try:
                page.goto(f"{base_url}/todo")
                page.wait_for_selector(checkbox)
                assert not page.is_checked(checkbox)

                # Wait on the htmx round-trip rather than a fixed sleep: the
                # swap is async, and a bare timeout would make this flaky in
                # exactly the way the bug it guards against looks.
                with page.expect_response(
                    lambda r: r.request.method == "PUT"
                    and r.url.endswith(f"/todo/toggle/{todo['id']}")
                ) as put:
                    page.click(checkbox)
                assert put.value.status == 200
                page.wait_for_function(
                    "sel => document.querySelector(sel)?.checked === true",
                    arg=checkbox,
                )
            finally:
                browser.close()

        final_state = get_current_state(base_url)
        assert find_todo(final_state["todo"])["done"], (
            "checkbox click did not reach /todo_all — the page can still render "
            "as checked while every reward call returns 0"
        )
        assert task.check_if_task_is_complete(initial_state, final_state)
    finally:
        stop_server(launcher, process)
