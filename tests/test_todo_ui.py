"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Markup contracts for the todo app's two layouts.

Chromium can't run everywhere these tests do, so the realism pass is checked
on the server-rendered HTML: the hooks the stylesheet relies on are present,
and the things agents and rewards depend on (ids, hx targets, button text,
the home link) are unchanged. ``<style>``/``<script>`` are stripped before
asserting so CSS text can't satisfy a markup check.
"""

import re
from pathlib import Path

import pytest
from fasthtml.common import to_xml
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.todo_app import main as todo_main


def _client(tmp_path: Path, *overrides: str) -> TestClient:
    with initialize(version_base=None, config_path="../config/"):
        config = compose(
            config_name="config",
            overrides=[f"logs_dir={tmp_path}", *overrides],
        )
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    todo_main.set_environment(config.apps)
    return TestClient(todo_main.app)


def _markup(html: str) -> str:
    html = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    return re.sub(r"<script.*?</script>", "", html, flags=re.S)


def _row(html: str, todo_id: int) -> str:
    """The markup of one list row, from its opening tag to the next row."""
    start = html.index(f'id="todo-{todo_id}"')
    nxt = html.find('id="todo-', start + 1)
    return html[start : nxt if nxt != -1 else len(html)]


@pytest.fixture
def list_client(tmp_path):
    return _client(tmp_path)


@pytest.fixture
def board_client(tmp_path):
    return _client(tmp_path, "apps/todo/layout=kanban_board")


class TestListLayout:
    def test_rows_carry_done_state_for_styling(self, list_client):
        html = _markup(list_client.get("/todo").text)
        for todo in todo_main.todos():
            row = _row(html, todo.id)
            checked = re.search(r'<input type="checkbox" checked', row) is not None
            assert checked == bool(todo.done)
            assert ("todo-row is-done" in row) == bool(todo.done)
            assert f'aria-label="{todo.title}"' in row

    def test_row_dom_order_matches_visual_order(self, list_client):
        row = _row(_markup(list_client.get("/todo").text), 0)
        order = [row.index(s) for s in ('type="checkbox"', "todo-title", ">Edit<", ">Remove<")]
        assert order == sorted(order)

    def test_secondary_actions_stay_in_the_dom(self, list_client):
        """Edit/Remove are visually quieted, never removed or hidden."""
        html = _markup(list_client.get("/todo").text)
        n = len(todo_main.todos())
        assert html.count('class="todo-btn edit-btn">Edit</button>') == n
        assert html.count('class="todo-btn remove-btn">Remove</button>') == n

    def test_eval_facing_hooks_unchanged(self, list_client):
        html = _markup(list_client.get("/todo").text)
        assert '<ul id="todo-list">' in html
        assert 'hx-post="/todo"' in html and 'hx-target="#todo-list"' in html
        assert 'name="title" placeholder="New Todo" id="new-title"' in html
        assert '<button id="submit-button" class="add-btn"' in html
        assert '<div id="current-todo"></div>' in html

    def test_empty_state_is_rendered_for_css(self, list_client):
        html = _markup(list_client.get("/todo").text)
        assert 'class="todo-empty"' in html
        # It follows the list so DOM order matches where it would appear.
        assert html.index('id="todo-list"') < html.index("todo-empty")

    def test_toggle_flips_done_class(self, list_client):
        target = next(t for t in todo_main.todos() if not t.done)
        row = list_client.put(f"/todo/toggle/{target.id}").text
        assert "todo-row is-done" in row and "checked" in row
        row = list_client.put(f"/todo/toggle/{target.id}").text
        assert "is-done" not in row

    def test_no_kanban_markup(self, list_client):
        html = _markup(list_client.get("/todo").text)
        assert "kanban" not in html
        assert "todo-page--list" in html


class TestBoardLayout:
    def test_column_counts_match_cards(self, board_client):
        html = _markup(board_client.get("/todo").text)
        columns = html.split('<div class="kanban-column">')[1:]
        assert len(columns) == len(todo_main.kanban_columns)
        for col in columns:
            count = int(re.search(r'<span class="kanban-count">(\d+)</span>', col).group(1))
            assert count == col.count('class="kanban-card"')

    def test_column_heading_text_is_only_the_title(self, board_client):
        """The count sits beside the <h3>, so renaming/reading a column is unchanged."""
        html = _markup(board_client.get("/todo").text)
        headings = re.findall(r'<h3[^>]*class="kanban-column-title"[^>]*>([^<]*)</h3>', html)
        assert headings == [todo_main.kanban_titles[c] for c in todo_main.kanban_columns]

    def test_count_updates_after_add(self, board_client):
        before = _markup(board_client.get("/todo").text)
        first = int(re.search(r'kanban-count">(\d+)<', before).group(1))
        after = _markup(board_client.post("/todo/kanban/add/todo", data={"title": "Pay rent"}).text)
        assert int(re.search(r'kanban-count">(\d+)<', after).group(1)) == first + 1

    def test_no_list_markup(self, board_client):
        html = _markup(board_client.get("/todo").text)
        assert 'id="todo-list"' not in html
        assert "todo-page--board" in html


class TestChromeAndThemes:
    @pytest.mark.parametrize(
        "layout, cls",
        [("default", "todo-home-button"), ("kanban_board", "todo-home-link")],
    )
    def test_home_link_keeps_text_href_and_role(self, tmp_path, layout, cls):
        client = _client(tmp_path, f"apps/todo/layout={layout}")
        html = _markup(client.get("/todo").text)
        assert (
            f'<a href="/" role="button" class="{cls}">Return to List of Apps</a>'
            in html
        )
        # Still after the list/board, as before.
        assert html.index(cls) > html.rindex('id="todo-')

    def test_home_link_is_prominent_only_in_default(self, tmp_path):
        """Navigation tasks need screenshot agents to find the button in the
        default layout; other layouts get the demoted link."""
        default = _markup(_client(tmp_path / "list").get("/todo").text)
        board = _markup(
            _client(tmp_path / "board", "apps/todo/layout=kanban_board").get("/todo").text
        )
        assert "todo-home-button" in default and "todo-home-link" not in default
        assert "todo-home-link" in board and "todo-home-button" not in board

    @pytest.mark.parametrize("theme", ["dark", "solarized", "mono", "challenging_font"])
    @pytest.mark.parametrize("layout", ["default", "kanban_board"])
    def test_renders_under_every_theme(self, tmp_path, theme, layout):
        client = _client(tmp_path, f"apps/theme={theme}", f"apps/todo/layout={layout}")
        response = client.get("/todo")
        assert response.status_code == 200
        assert "--color-primary:" in response.text

    def test_css_keeps_ui_question_answers_true(self):
        """tests/ui_questions asks about the default screenshot: the control
        left of each title is a "Checkbox" (with "Radio button" as a
        distractor) and checked items are not "struck through". Guard the two
        realism tweaks that would quietly flip those answers."""
        css = re.sub(r"\s+", "", to_xml(todo_main.styles))
        assert "line-through" not in css
        assert "border-radius:50%" not in css

    def test_component_css_uses_tokens_only(self):
        css = to_xml(todo_main.styles)
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "hex literal in todo CSS"
        assert not re.search(r"\b(?:rgb|hsl)a?\(", css), "raw colour in todo CSS"
        assert "http" not in css
