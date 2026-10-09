"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
``POST /todo`` has to respond, not just write.

Everything here asserts on the HTTP response rather than on ``/todo_all``, and
that distinction is the whole point of the file. A real bug lived in this
handler: it inserted the row and *then* raised on the way out, returning 500.

Nothing state-based noticed. The row was in the database, so ``/todo_all`` was
correct and ``AddToDoTask`` scored 1.0. But the browser received an error, htmx
had nothing to swap in, and the new todo never appeared on the page. An agent
sees no change, concludes the click missed, and clicks Add again -- so the
observable symptom is duplicate todos, or a trajectory that reads as a model
failing at a task it actually completed.

The cause was ``todos[-1]``, which reads like "the last row" and is not:
fastlite indexes a table by primary key, so it asked for the row with
``id == -1`` and raised ``NotFoundError``.

The general lesson, and the reason these assertions look redundant next to the
existing todo tests: for an agent driving a UI, a write that never reaches the
DOM is indistinguishable from no write at all. Checking the database proves the
handler ran; only checking the response proves the user can see it.
"""

from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.start_page.main import (
    app,
    initialize_routes_and_configure_task,
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # A dedicated temp dir rather than the shared ``getbasetemp()``: the apps
    # seed their tables with fixed primary keys at startup, so re-initializing
    # over another module's database collides on insert.
    logs_dir = tmp_path_factory.mktemp("todo_add")
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(config.apps)
    # raise_server_exceptions=False so a handler that blows up surfaces as the
    # 500 a browser would get, instead of propagating and failing the test with
    # the handler's own traceback. The status code is what is under test.
    return TestClient(app, raise_server_exceptions=False)


def test_add_returns_ok(client):
    """The regression itself: this returned 500 while still writing the row."""
    response = client.post("/todo", data={"title": "Buy milk"})
    assert response.status_code == 200, (
        f"POST /todo returned {response.status_code}. The row is probably still "
        "in the database -- check the response, not /todo_all."
    )


def test_response_contains_the_new_row(client):
    """htmx appends whatever comes back; an empty body renders nothing."""
    response = client.post("/todo", data={"title": "Walk the dog"})
    assert "Walk the dog" in response.text, (
        "the response carries no rendered row, so nothing gets appended to the "
        "list even when the write succeeded"
    )


def test_response_resets_the_input(client):
    """The out-of-band swap that clears the text box after adding.

    Without it the title stays in the field, which is its own duplicate-entry
    trap for an agent re-reading the form.
    """
    response = client.post("/todo", data={"title": "Pay water bill"})
    assert "hx-swap-oob" in response.text


def test_add_creates_exactly_one_row(client):
    """Guards the retry path a 500 invites: click Add again, get two todos."""
    client.post("/todo", data={"title": "Call the vet"})
    rows = client.get("/todo_all").json()
    assert sum(t["title"] == "Call the vet" for t in rows) == 1


def test_added_row_is_not_done(client):
    """A new todo starts unchecked; MarkToDoDoneTask depends on it."""
    client.post("/todo", data={"title": "Rotate the tyres"})
    rows = client.get("/todo_all").json()
    added = next(t for t in rows if t["title"] == "Rotate the tyres")
    assert not added["done"]
