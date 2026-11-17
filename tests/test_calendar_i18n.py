"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The calendar's events under the german / mandarin content packs, and the
calendar tasks that find them (mirrors tests/test_todo_i18n_layouts.py).

Three promises are checked here:

* the translated packs translate every event, in the default pack's order and
  with its dates, links, recurrence and invitees, and their
  ``event_translations`` maps follow that list; Hydra replaces the event list
  rather than merging it into the default one;
* removing an event through the app's own route satisfies ``RemoveEventTask``
  in every language, and removing the wrong one does not;
* every calendar task in ``config/tasks`` builds a target under de/zh, is not
  satisfied by doing nothing, and asks for the same change as in English
  (English goal titles resolve through the pack's ``event_translations``).
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path

import pytest
from hydra import compose, initialize
from hydra.utils import instantiate
from omegaconf import OmegaConf
from starlette.testclient import TestClient

from open_apps import config_dir
from open_apps.apps.calendar_app import main as cal
from open_apps.tasks.tasks import CompositeTask, RemoveEventTask

CONTENTS = ["default", "german", "mandarin"]
TRANSLATED = ["german", "mandarin"]
CONTENT_DIR = config_dir() / "apps" / "calendar" / "content"
STATES_DIR = Path(__file__).parent / "states"
# Every field but the translated text: what a task or the state depends on.
STRUCTURAL = ("date", "url", "recurring", "invitees")


def _pack(name: str):
    return OmegaConf.load(CONTENT_DIR / f"{name}.yaml")


DEFAULT_PACK = _pack("default")
DEFAULT_TITLES = [str(e.title) for e in DEFAULT_PACK.events]
_BASE_STATE = json.loads((STATES_DIR / "initial_state.json").read_text(encoding="utf-8"))


def _translations(content: str) -> dict[str, str]:
    return OmegaConf.to_container(_pack(content).event_translations)


def _rendered(title: str, content: str) -> str:
    """The title ``title`` (English, as tasks write it) renders as in ``content``."""
    return title if content == "default" else _translations(content)[title]


def _complete(task, initial: dict, current: dict) -> bool:
    with contextlib.redirect_stdout(io.StringIO()):  # the comparison prints diffs
        return bool(task.check_if_task_is_complete(initial, current))


# ---------------------------------------------------------------------------
# The packs
# ---------------------------------------------------------------------------
class TestTranslatedPacks:
    @pytest.mark.parametrize("content", TRANSLATED)
    def test_every_event_is_translated_in_default_order(self, content):
        default, pack = list(DEFAULT_PACK.events), list(_pack(content).events)
        assert len(pack) == len(default)
        english = set(DEFAULT_TITLES)
        for en, tr in zip(default, pack):
            assert str(tr.title) not in english, f"{content}: {tr.title!r} is left in English"
            assert str(tr.description) != str(en.description), f"{content}: {tr.title!r} description"
            for key in STRUCTURAL:
                assert tr.get(key) == en.get(key), f"{content}: {tr.title!r} changes {key}"
            assert set(tr.keys()) == set(en.keys()), f"{content}: {tr.title!r} fields differ"
        assert len({str(e.title) for e in pack}) == len(pack), f"{content}: duplicate titles"

    @pytest.mark.parametrize("content", TRANSLATED)
    def test_translation_map_follows_events(self, content):
        pack = _pack(content)
        expected = {en: str(tr.title) for en, tr in zip(DEFAULT_TITLES, pack.events)}
        assert _translations(content) == expected

    @pytest.mark.parametrize("content", TRANSLATED)
    def test_names_survive_the_translation(self, content):
        """People keep their names: a birthday's title still names its invitee."""
        for event in _pack(content).events:
            invitee = event.get("invitees")
            if invitee and invitee not in ("Dad", "Granni"):
                assert str(invitee) in str(event.title), event.title

    def test_alex_birthday(self):
        assert _rendered("Alex's Birthday", "german") == "Alex' Geburtstag"
        assert _rendered("Alex's Birthday", "mandarin") == "Alex的生日"

    @pytest.mark.parametrize(
        "content", ["long_descriptions", "misleading_descriptions", "adversarial_descriptions"]
    )
    def test_english_variation_packs_are_untouched(self, content):
        pack = _pack(content)
        assert "event_translations" not in pack
        if "events" in pack:
            assert [str(e.title) for e in pack.events] == DEFAULT_TITLES

    @pytest.mark.parametrize("content", CONTENTS)
    def test_hydra_replaces_the_event_list(self, tmp_path, content):
        with initialize(version_base=None, config_path="../config/"):
            config = compose(
                config_name="config",
                overrides=[f"logs_dir={tmp_path}", f"apps/calendar/content={content}"],
            )
        titles = [str(e.title) for e in config.apps.calendar.events]
        assert titles == [_rendered(t, content) for t in DEFAULT_TITLES]


# ---------------------------------------------------------------------------
# RemoveEventTask through the app, every language
# ---------------------------------------------------------------------------
@pytest.fixture
def calendar_env(tmp_path):
    """`calendar_env(content)` -> a TestClient on a freshly seeded calendar.
    Restores the module globals other test modules set."""
    saved = {n: getattr(cal, n) for n in ("events", "logo_title_container") if hasattr(cal, n)}
    saved_config = getattr(cal.app, "config", None)

    def _env(content: str) -> TestClient:
        with initialize(version_base=None, config_path="../config/"):
            config = compose(
                config_name="config",
                overrides=[f"logs_dir={tmp_path / content}", f"apps/calendar/content={content}"],
            )
        Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
        with contextlib.redirect_stdout(io.StringIO()):
            cal.set_environment(config.apps)
        return TestClient(cal.app)

    yield _env

    for name, value in saved.items():
        setattr(cal, name, value)
    if saved_config is not None:
        cal.app.config = saved_config


def _full(client: TestClient) -> dict:
    state = copy.deepcopy(_BASE_STATE)
    state["calendar"] = client.get("/calendar_all").json()
    return state


def _delete(client: TestClient, title: str) -> None:
    event_id = next(e["id"] for e in client.get("/calendar_all").json() if e["title"] == title)
    response = client.post(f"/calendar/event/{event_id}/delete", follow_redirects=False)
    assert response.status_code in (200, 303)


@pytest.mark.parametrize("content", CONTENTS)
@pytest.mark.parametrize(
    "title,date,wrong",
    [
        ("Otto's Birthday", "1990-04-02", "Karl's Birthday"),
        ("WACV 2026 Abstract Deadline", "2025-07-11", "WACV 2026 Paper Deadline"),
        ("ECCV 2026 Paper Deadline", "2026-03-06", "IJCNN 2026 Deadline"),
    ],
)
class TestRemoveEventFlows:
    def test_removing_the_translated_event_wins(self, calendar_env, content, title, date, wrong):
        client = calendar_env(content)
        initial = _full(client)
        task = RemoveEventTask(goal=f"Remove {title} from my calendar.", title=title, date=date)
        assert not _complete(task, initial, initial), "idling wins"
        _delete(client, _rendered(title, content))
        assert _complete(task, initial, _full(client))

    def test_removing_the_wrong_event_fails(self, calendar_env, content, title, date, wrong):
        client = calendar_env(content)
        initial = _full(client)
        task = RemoveEventTask(goal=f"Remove {title} from my calendar.", title=title, date=date)
        _delete(client, _rendered(wrong, content))
        assert not _complete(task, initial, _full(client))

    def test_the_date_still_has_to_match(self, calendar_env, content, title, date, wrong):
        client = calendar_env(content)
        initial = _full(client)
        task = RemoveEventTask(goal="x", title=title, date="1900-01-01")
        _delete(client, _rendered(title, content))
        assert not _complete(task, initial, _full(client))


# ---------------------------------------------------------------------------
# Every calendar task in config/tasks, under de/zh
# ---------------------------------------------------------------------------
def _leaves(task):
    """The ordinary tasks inside composite and abstention wrappers."""
    children = list(getattr(task, "subtasks", None) or [])
    child = getattr(task, "action_task", None)
    if child is not None:
        children.append(child)
    if not children:
        return [task]
    return [leaf for c in children for leaf in _leaves(c)]


def _calendar_tasks() -> list:
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
            if any(isinstance(leaf, RemoveEventTask) for leaf in _leaves(task)):
                found[f"{path.stem}:{key}"] = task
    return sorted(found.items())


CALENDAR_TASKS = _calendar_tasks()


def _seeded(content: str) -> list:
    """``/calendar_all`` as the app serves it right after seeding ``content``."""
    pack = DEFAULT_PACK if content == "default" else _pack(content)
    return [
        {
            "id": i + 1,
            "title": str(e.title),
            "date": str(e.date),
            "description": str(e.description),
            "url": e.get("url"),
            "location": e.get("location"),
            "invitees": e.get("invitees"),
            "recurring": e.get("recurring"),
        }
        for i, e in enumerate(pack.events)
    ]


def _session(content: str) -> dict:
    """A whole cross-app state: calendar (and todos) in ``content``."""
    state = copy.deepcopy(_BASE_STATE)
    state["calendar"] = _seeded(content)
    if content != "default":
        todo_pack = OmegaConf.load(config_dir() / "apps" / "todo" / "content" / f"{content}.yaml")
        state["todo"] = [
            {"id": i, "title": str(title), "done": int(bool(done))}
            for i, (title, done) in enumerate(todo_pack.init_todos)
        ]
    return state


def _calendar_as_english(events: list, content: str) -> list:
    back = {v: k for k, v in _translations(content).items()}
    return sorted((back.get(e["title"], e["title"]), e["date"]) for e in events)


def test_the_task_inventory_is_found():
    assert len(CALENDAR_TASKS) >= 40
    assert any(isinstance(t, CompositeTask) for _, t in CALENDAR_TASKS)


def test_every_task_title_has_a_translation():
    titles = {
        leaf.title
        for _, task in CALENDAR_TASKS
        for leaf in _leaves(task)
        if isinstance(leaf, RemoveEventTask)
    }
    for content in TRANSLATED:
        missing = titles - set(_translations(content))
        assert not missing, f"{content} has no translation for {sorted(missing)}"


@pytest.mark.parametrize("content", TRANSLATED)
@pytest.mark.parametrize("key", [k for k, _ in CALENDAR_TASKS])
def test_calendar_task_is_winnable_and_asks_the_same_as_english(key, content):
    task = dict(CALENDAR_TASKS)[key]
    english_initial, initial = _session("default"), _session(content)
    english_target = task.get_target_state(english_initial)
    target = task.get_target_state(initial)
    english = sorted((e["title"], e["date"]) for e in english_target["calendar"])
    assert _calendar_as_english(target["calendar"], content) == english, (
        f"{key} asks for a different calendar change under {content}"
    )
    # The English task removes a seeded event, so the comparison above is not
    # two no-ops agreeing.
    for leaf in _leaves(task):
        if isinstance(leaf, RemoveEventTask):
            assert (leaf.title, leaf.date) not in english, f"{key}: {leaf.title!r} not removed"
            assert _rendered(leaf.title, content) not in [e["title"] for e in target["calendar"]]
    assert not _complete(task, initial, initial), f"{key}: idling wins under {content}"
    assert _complete(task, initial, target), f"{key}: unwinnable under {content}"
