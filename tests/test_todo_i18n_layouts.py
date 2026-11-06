"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The todo app's third layout (``grouped_sections``), its localized UI, and the
todo tasks under the german / mandarin content packs.

Three promises are checked here:

* every layout renders under every theme and language, with the pack's own
  labels, the shared back-to-apps string and ``<html lang>``;
* the task flows (add, mark done, delete, edit) work through HTTP in every
  layout and language, and the resulting ``/todo_all`` satisfies the task --
  the routes, ids and reward endpoint are the same in every layout;
* every todo task in ``config/tasks`` builds a target under de/zh, is not
  satisfied by doing nothing, and asks for the same change as in English (the
  translated packs keep the default pack's done flags; English goal names
  resolve through the pack's ``todo_translations``).
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from hydra.utils import instantiate
from omegaconf import OmegaConf
from starlette.testclient import TestClient

from open_apps import config_dir, i18n
from open_apps.apps.todo_app import main as todo_main
from open_apps.tasks.tasks import (
    AddToDoTask,
    DeleteToDoTask,
    MarkToDoDoneTask,
)

LAYOUTS = ["default", "kanban_board", "grouped_sections"]
CONTENTS = ["default", "german", "mandarin"]
LANG_TAG = {"default": "en", "german": "de", "mandarin": "zh-Hans"}
CONTENT_DIR = config_dir() / "apps" / "todo" / "content"
STATES_DIR = Path(__file__).parent / "states"


def _pack(name: str):
    return OmegaConf.load(CONTENT_DIR / f"{name}.yaml")


DEFAULT_PACK = _pack("default")


def _client(tmp_path: Path, *overrides: str) -> TestClient:
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"logs_dir={tmp_path}", *overrides])
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()):
        todo_main.set_environment(config.apps)
    return TestClient(todo_main.app)


def _env(tmp_path: Path, layout: str, content: str) -> TestClient:
    return _client(tmp_path, f"apps/todo/layout={layout}", f"apps/todo/content={content}")


def _markup(html: str) -> str:
    html = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    return re.sub(r"<script.*?</script>", "", html, flags=re.S)


def _labels(content: str) -> dict:
    """The pack's effective ``*_label`` / placeholder strings (default + pack)."""
    merged = OmegaConf.merge(DEFAULT_PACK, _pack(content)) if content != "default" else DEFAULT_PACK
    return {
        k: str(v)
        for k, v in OmegaConf.to_container(merged, resolve=False).items()
        if k.endswith("_label") or k.endswith("_placeholder")
    }


_BASE_STATE = json.loads((STATES_DIR / "initial_state.json").read_text(encoding="utf-8"))


def _full(todos: list) -> dict:
    """A whole cross-app state with ``todos`` as its todo slice."""
    state = copy.deepcopy(_BASE_STATE)
    state["todo"] = copy.deepcopy(todos)
    return state


def _complete(task, initial: dict, current: dict) -> bool:
    with contextlib.redirect_stdout(io.StringIO()):  # the comparison prints diffs
        return bool(task.check_if_task_is_complete(initial, current))


def _rendered(name: str, content: str) -> str:
    """The title ``name`` (English, as tasks write it) renders as in ``content``."""
    if content == "default":
        return name
    return str(_pack(content).todo_translations[name])


# ---------------------------------------------------------------------------
# The packs
# ---------------------------------------------------------------------------
class TestTranslatedPacks:
    @pytest.mark.parametrize("content", ["german", "mandarin"])
    def test_every_todo_is_translated_with_the_default_done_flags(self, content):
        default = [tuple(t) for t in DEFAULT_PACK.init_todos]
        pack = [tuple(t) for t in _pack(content).init_todos]
        assert len(pack) == len(default)
        english = {title for title, _ in default}
        for (en_title, en_done), (title, done) in zip(default, pack):
            assert done == en_done, f"{content}: {title!r} flips the done flag of {en_title!r}"
            assert title not in english, f"{content}: {title!r} is left in English"

    @pytest.mark.parametrize("content", ["german", "mandarin"])
    def test_translation_map_follows_init_todos(self, content):
        pack = _pack(content)
        expected = {
            str(en[0]): str(tr[0]) for en, tr in zip(DEFAULT_PACK.init_todos, pack.init_todos)
        }
        assert OmegaConf.to_container(pack.todo_translations) == expected

    @pytest.mark.parametrize("content", ["german", "mandarin"])
    def test_every_label_is_translated(self, content):
        english = _labels("default")
        translated = _labels(content)
        assert set(translated) == set(english)
        own = OmegaConf.to_container(_pack(content), resolve=False)
        missing = [k for k in english if k not in own]
        assert not missing, f"{content} falls back to English for {missing}"

    def test_typos_are_fixed(self):
        titles = [t[0] for t in _pack("german").init_todos] + [
            t[0] for t in _pack("mandarin").init_todos
        ]
        assert "Arzttermin bessorgen" not in titles
        assert "整理医疗论文" not in titles


# ---------------------------------------------------------------------------
# grouped_sections markup
# ---------------------------------------------------------------------------
@pytest.fixture
def sections_client(tmp_path):
    return _client(tmp_path, "apps/todo/layout=grouped_sections")


def _section(html: str, key: str) -> str:
    start = html.index(f"todo-section--{key}")
    end = html.find("todo-section--", start + 1)
    return html[start : end if end != -1 else len(html)]


class TestGroupedSections:
    def test_rows_are_split_by_done_state(self, sections_client):
        html = _markup(sections_client.get("/todo").text)
        open_html, done_html = _section(html, "open"), _section(html, "done")
        for todo in todo_main.todos():
            home, away = (done_html, open_html) if todo.done else (open_html, done_html)
            assert f'id="todo-{todo.id}"' in home
            assert f'id="todo-{todo.id}"' not in away

    def test_counts_match_rows(self, sections_client):
        html = _markup(sections_client.get("/todo").text)
        for key in ("open", "done"):
            section = _section(html, key)
            count = int(re.search(r'todo-section-count">(\d+)<', section).group(1))
            assert count == section.count("<li ")

    def test_eval_facing_hooks(self, sections_client):
        html = _markup(sections_client.get("/todo").text)
        assert 'hx-post="/todo"' in html
        assert 'id="new-title"' in html and 'id="submit-button"' in html
        for todo in todo_main.todos():
            assert f'hx-put="/todo/toggle/{todo.id}"' in html
            assert f'hx-get="/todo/edit/{todo.id}"' in html
            assert f'hx-delete="/todo/todos/{todo.id}"' in html
            assert f'aria-label="{todo.title}"' in html
        # Every action re-renders the one container.
        assert 'id="todo-sections"' in html
        assert html.count('hx-target="#todo-sections"') == 1 + 3 * len(todo_main.todos())

    def test_no_list_or_board_markup(self, sections_client):
        html = _markup(sections_client.get("/todo").text)
        assert 'id="todo-list"' not in html
        assert "kanban" not in html
        assert "todo-page--sections" in html

    def test_home_link_is_the_demoted_one(self, sections_client):
        html = _markup(sections_client.get("/todo").text)
        assert '<a href="/" role="button" class="todo-home-link">Return to List of Apps</a>' in html

    def test_toggle_moves_the_row_to_the_other_section(self, sections_client):
        target = next(t for t in todo_main.todos() if not t.done)
        html = _markup(sections_client.put(f"/todo/toggle/{target.id}").text)
        assert f'id="todo-{target.id}"' in _section(html, "done")
        html = _markup(sections_client.put(f"/todo/toggle/{target.id}").text)
        assert f'id="todo-{target.id}"' in _section(html, "open")

    def test_empty_section_says_so(self, sections_client):
        for todo in todo_main.todos():
            if todo.done:
                sections_client.delete(f"/todo/todos/{todo.id}")
        html = _markup(sections_client.get("/todo").text)
        assert "Nothing here." in _section(html, "done")

    def test_blank_add_is_ignored(self, sections_client):
        before = sections_client.get("/todo_all").json()
        response = sections_client.post("/todo", data={"title": "   "})
        assert response.status_code == 200 and 'id="todo-sections"' in response.text
        assert sections_client.get("/todo_all").json() == before


# ---------------------------------------------------------------------------
# Task flows through HTTP, every layout x language
# ---------------------------------------------------------------------------
def _id_of(client: TestClient, title: str) -> int:
    return next(t["id"] for t in client.get("/todo_all").json() if t["title"] == title)


def _add(client: TestClient, layout: str, title: str):
    if layout == "kanban_board":
        return client.post("/todo/kanban/add/todo", data={"title": title})
    return client.post("/todo", data={"title": title})


@pytest.mark.parametrize("content", CONTENTS)
@pytest.mark.parametrize("layout", LAYOUTS)
class TestTaskFlows:
    def test_add(self, tmp_path, layout, content):
        client = _env(tmp_path, layout, content)
        initial = _full(client.get("/todo_all").json())
        task = AddToDoTask(goal="Add 'Call Mom' to my todo list.", todo_name="Call Mom", is_done=False)
        assert not _complete(task, initial, initial)
        response = _add(client, layout, "Call Mom")
        assert response.status_code == 200 and "Call Mom" in response.text
        assert _complete(task, initial, _full(client.get("/todo_all").json()))

    def test_mark_done(self, tmp_path, layout, content):
        client = _env(tmp_path, layout, content)
        initial = _full(client.get("/todo_all").json())
        task = MarkToDoDoneTask(goal="Mark 'Visit grandma' as done.", todo_name="Visit grandma")
        assert not _complete(task, initial, initial)
        todo_id = _id_of(client, _rendered("Visit grandma", content))
        assert client.put(f"/todo/toggle/{todo_id}").status_code == 200
        assert _complete(task, initial, _full(client.get("/todo_all").json()))

    def test_delete(self, tmp_path, layout, content):
        client = _env(tmp_path, layout, content)
        initial = _full(client.get("/todo_all").json())
        task = DeleteToDoTask(goal="Delete 'Call Aunt Lisa'.", todo_name="Call Aunt Lisa")
        assert not _complete(task, initial, initial)
        todo_id = _id_of(client, _rendered("Call Aunt Lisa", content))
        assert client.delete(f"/todo/todos/{todo_id}").status_code == 200
        assert _complete(task, initial, _full(client.get("/todo_all").json()))

    def test_edit(self, tmp_path, layout, content):
        client = _env(tmp_path, layout, content)
        todo_id = _id_of(client, _rendered("Check budget", content))
        form = client.get(f"/todo/edit/{todo_id}")
        assert form.status_code == 200
        labels = _labels(content)
        assert labels["save_label"] in form.text and labels["done_label"] in form.text
        response = client.put(
            "/todo", data={"id": todo_id, "title": "Check budget twice", "done": "1"}
        )
        assert response.status_code == 200
        row = next(t for t in client.get("/todo_all").json() if t["id"] == todo_id)
        assert row["title"] == "Check budget twice" and row["done"]


# ---------------------------------------------------------------------------
# Localized UI, and every layout x theme x language renders
# ---------------------------------------------------------------------------
class TestLocalizedUI:
    @pytest.mark.parametrize("content", CONTENTS)
    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_labels_and_html_lang(self, tmp_path, layout, content):
        client = _env(tmp_path, layout, content)
        response = client.get("/todo")
        assert response.status_code == 200
        assert f'<html lang="{LANG_TAG[content]}"' in response.text
        html = _markup(response.text)
        labels = _labels(content)
        language = "english" if content == "default" else content
        assert f">{i18n.t('return_to_apps', language)}</a>" in html
        assert f">{labels['edit_label']}</button>" in html
        assert f">{labels['remove_label']}</button>" in html
        if layout == "kanban_board":
            for col in todo_main.kanban_columns:
                assert f">{labels[f'kanban_{col}_label']}</h3>" in html
            assert f'placeholder="{labels["add_task_placeholder"]}"' in html
        else:
            assert f'placeholder="{labels["new_todo_placeholder"]}"' in html
        if layout == "grouped_sections":
            assert f">{labels['open_section_label']}</h2>" in html
            assert f">{labels['done_section_label']}</h2>" in html
        if content != "default":
            for english in (">Edit<", ">Remove<", "Return to List of Apps", "New Todo"):
                assert english not in html, f"{english!r} left in English"
            for todo in todo_main.todos():
                assert todo.title in html

    def test_english_strings_are_unchanged(self, tmp_path):
        """The default pack renders exactly the strings the app always had."""
        html = _markup(_client(tmp_path / "l").get("/todo").text)
        assert 'name="title" placeholder="New Todo" id="new-title"' in html
        assert "No tasks yet. Add your first one above." in html
        assert 'class="todo-btn edit-btn">Edit</button>' in html
        board = _markup(_client(tmp_path / "b", "apps/todo/layout=kanban_board").get("/todo").text)
        for title in ("To Do", "In Progress", "Review", "Done"):
            assert f">{title}</h3>" in board
        assert ">Mark Done<" in board and ">Reopen<" in board

    def test_kanban_renames_do_not_survive_a_reset(self, tmp_path):
        client = _client(tmp_path / "a", "apps/todo/layout=kanban_board")
        client.put("/todo/kanban/header/review", data={"title": "QA"})
        assert todo_main.kanban_titles["review"] == "QA"
        _client(tmp_path / "b", "apps/todo/layout=kanban_board", "apps/todo/content=german")
        assert todo_main.kanban_titles["review"] == "Prüfung"

    @pytest.mark.parametrize("content", CONTENTS)
    @pytest.mark.parametrize("theme", ["dark", "challenging_font"])
    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_renders_under_theme_and_language(self, tmp_path, layout, theme, content):
        client = _client(
            tmp_path,
            f"apps/theme={theme}",
            f"apps/todo/layout={layout}",
            f"apps/todo/content={content}",
        )
        response = client.get("/todo")
        assert response.status_code == 200
        assert "--color-primary:" in response.text
        assert f'<html lang="{LANG_TAG[content]}"' in response.text


# ---------------------------------------------------------------------------
# Every todo task in config/tasks, under de/zh
# ---------------------------------------------------------------------------
TODO_CLASSES = (AddToDoTask, MarkToDoDoneTask, DeleteToDoTask)


def _leaves(task):
    """The ordinary tasks inside composites / ambiguity / abstention wrappers."""
    children = []
    for attr in ("subtasks",):
        children.extend(getattr(task, attr, None) or [])
    for attr in ("clarified", "action_task"):
        child = getattr(task, attr, None)
        if child is not None:
            children.append(child)
    if not children:
        return [task]
    return [leaf for child in children for leaf in _leaves(child)]


def _todo_tasks() -> list:
    found = {}
    for path in sorted((config_dir() / "tasks").glob("*.yaml")):
        if path.stem == "all_tasks":
            continue
        cfg = OmegaConf.load(path)
        for key, task_cfg in cfg.items():
            if not hasattr(task_cfg, "get") or "_target_" not in task_cfg:
                continue
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    task = instantiate(task_cfg, _convert_="all")
            except Exception:  # not instantiable standalone; not ours to test
                continue
            leaves = [leaf for leaf in _leaves(task) if isinstance(leaf, TODO_CLASSES)]
            if leaves:
                found[f"{path.stem}:{key}"] = (task, leaves)
    return sorted(found.items())


TODO_TASKS = _todo_tasks()


def _seeded(content: str) -> list:
    pack = DEFAULT_PACK if content == "default" else _pack(content)
    return [
        {"id": i, "title": str(title), "done": int(bool(done))}
        for i, (title, done) in enumerate(pack.init_todos)
    ]


def _as_english(todos: list, content: str) -> list:
    back = {v: k for k, v in OmegaConf.to_container(_pack(content).todo_translations).items()}
    return sorted((back.get(t["title"], t["title"]), bool(t["done"])) for t in todos)


def test_the_task_inventory_is_found():
    assert len(TODO_TASKS) > 50


@pytest.mark.parametrize("content", ["german", "mandarin"])
@pytest.mark.parametrize("key", [k for k, _ in TODO_TASKS])
def test_todo_task_is_winnable_and_asks_the_same_as_english(key, content):
    _, leaves = dict(TODO_TASKS)[key]
    english_initial = _full(_seeded("default"))
    initial = _full(_seeded(content))
    for leaf in leaves:
        try:
            english_target = leaf.get_target_state(english_initial)
        except ValueError:
            # Refers to a todo an earlier sub-task adds: not a seeded one, so
            # nothing to translate. Same outcome in every language.
            with pytest.raises(ValueError):
                leaf.get_target_state(initial)
            continue
        target = leaf.get_target_state(initial)
        english = sorted((t["title"], bool(t["done"])) for t in english_target["todo"])
        assert _as_english(target["todo"], content) == english, f"{key}: {type(leaf).__name__}({leaf.todo_name!r}) asks for a different change"
        if not _complete(leaf, english_initial, english_initial):
            assert not _complete(leaf, initial, initial), f"{key}: idling wins under {content}"
        assert _complete(leaf, initial, target), f"{key}: unwinnable under {content}"
