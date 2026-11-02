"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Password tasks, scored against real episodes.

Each task is played through the actual routes with a ``TestClient`` -- the
same form posts the OpenPasswords UI makes -- and the resulting cross-app
state is scored. A correct episode has to pass, and the near-misses an agent
would plausibly make (the decoy item, a password one character off, an extra
edit, deleted and then purged) have to fail. Passwords and usernames are
secrets and addresses, so they compare exactly, never fuzzily.
"""

import contextlib
import io
from pathlib import Path

import pytest
from hydra import compose, initialize
from omegaconf import OmegaConf
from starlette.testclient import TestClient

from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task
from open_apps.tasks.tasks import AddPasswordTask, DeletePasswordTask, EditPasswordTask


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("password_tasks")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


def _seed(pack: str = "default"):
    from open_apps.apps.passwords_app import main as passwords_app

    with initialize(version_base=None, config_path="../config/"):
        packed = compose(config_name="config", overrides=[f"apps/passwords/content={pack}"])
    OmegaConf.update(packed, "apps.passwords.database_path", str(app.config.passwords.database_path))
    cfg = OmegaConf.create(OmegaConf.to_container(app.config, resolve=True))
    cfg.passwords = OmegaConf.to_container(packed.apps.passwords, resolve=True)
    passwords_app.set_environment(cfg)


@pytest.fixture
def fresh(client, monkeypatch):
    """Re-seed the vault, and return a reader for the cross-app state."""
    from open_apps import state as state_module
    from open_apps.apps.passwords_app import main as passwords_app

    passwords_app.set_environment(app.config)

    def fetch(url, *args, **kwargs):
        response = client.get(url.removeprefix("http://testserver"))
        return response.json() if response.status_code == 200 else None

    monkeypatch.setattr(state_module, "safe_get_json", fetch)
    yield lambda: state_module.get_current_state("http://testserver")
    passwords_app.set_environment(app.config)


def _passes(task, initial, current):
    with contextlib.redirect_stdout(io.StringIO()):
        return task.check_if_task_is_complete(initial, current)


def _id(client, title, username=None):
    found = [i["id"] for i in client.get("/passwords_all").json()["items"]
             if i["title"] == title and (username is None or i["username"] == username) and not i["deleted"]]
    (only,) = found
    return only


def _current(client, item_id) -> dict:
    item = next(i for i in client.get("/passwords_all").json()["items"] if i["id"] == item_id)
    return {k: ("1" if v is True else "" if v is False else v) for k, v in item.items()
            if k in ("kind", "title", "username", "password", "website", "security", "notes", "favorite")}


def _edit(client, item_id, **changes):
    data = {**_current(client, item_id), **changes}
    response = client.post(f"/passwords/{item_id}", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text[:300]


GYM = {"kind": "login", "title": "Harbor Climbing Gym", "username": "dana.reyes@example.com",
       "password": "Chalk-Bag-77", "website": "https://harborclimb.example"}


def gym_task(**overrides) -> AddPasswordTask:
    return AddPasswordTask(goal="Add the climbing gym login.", **{**GYM, **overrides})


class TestAdd:
    def test_correct_episode(self, client, fresh):
        task, initial = gym_task(), fresh()
        client.post("/passwords", data=GYM)
        assert _passes(task, initial, fresh())

    def test_a_password_one_character_off_fails(self, client, fresh):
        task, initial = gym_task(), fresh()
        client.post("/passwords", data={**GYM, "password": "Chalk-Bag-7"})
        assert not _passes(task, initial, fresh())

    def test_punctuation_in_a_password_matters(self, client, fresh):
        """The fuzzy diff would read these as equal; a password is not prose."""
        task, initial = gym_task(), fresh()
        client.post("/passwords", data={**GYM, "password": "ChalkBag77"})
        assert not _passes(task, initial, fresh())

    def test_a_username_in_another_case_fails(self, client, fresh):
        task, initial = gym_task(), fresh()
        client.post("/passwords", data={**GYM, "username": "Dana.Reyes@example.com"})
        assert not _passes(task, initial, fresh())

    def test_the_title_is_matched_loosely(self, client, fresh):
        task, initial = gym_task(), fresh()
        client.post("/passwords", data={**GYM, "title": "harbor climbing gym"})
        assert _passes(task, initial, fresh())

    def test_adding_it_twice_fails(self, client, fresh):
        task, initial = gym_task(), fresh()
        client.post("/passwords", data=GYM)
        client.post("/passwords", data=GYM)
        assert not _passes(task, initial, fresh())

    def test_an_extra_edit_elsewhere_fails(self, client, fresh):
        task, initial = gym_task(), fresh()
        client.post("/passwords", data=GYM)
        _edit(client, _id(client, "Mail"), notes="changed")
        assert not _passes(task, initial, fresh())

    def test_a_secure_note(self, client, fresh):
        task = AddPasswordTask(goal="Add the storage unit note.", kind="note", title="Storage unit",
                               notes="Harbor Self Storage, unit B-114. Gate code 4471#.")
        initial = fresh()
        client.post("/passwords", data={"kind": "note", "title": "Storage unit",
                                        "notes": "Harbor Self Storage, unit B-114. Gate code 4471#."})
        assert _passes(task, initial, fresh())


class TestEdit:
    def test_correct_episode(self, client, fresh):
        task = EditPasswordTask(goal="Change the Netflix password.", match="Netflix",
                                set={"password": "Popcorn-Pier-3302"})
        initial = fresh()
        _edit(client, _id(client, "Netflix"), password="Popcorn-Pier-3302")
        assert _passes(task, initial, fresh())

    def test_the_decoy_fails(self, client, fresh):
        task = EditPasswordTask(goal="Change the Netflix password.", match="Netflix",
                                set={"password": "Popcorn-Pier-3302"})
        initial = fresh()
        _edit(client, _id(client, "Netflix (old)"), password="Popcorn-Pier-3302")
        assert not _passes(task, initial, fresh())

    def test_changing_something_else_too_fails(self, client, fresh):
        task = EditPasswordTask(goal="Change the Netflix password.", match="Netflix",
                                set={"password": "Popcorn-Pier-3302"})
        initial = fresh()
        _edit(client, _id(client, "Netflix"), password="Popcorn-Pier-3302", favorite="")
        assert not _passes(task, initial, fresh())

    def test_the_edit_date_is_not_scored(self, client, fresh):
        """A save stamps the pinned date; a target built without it still
        matches, and nothing else about the item may differ."""
        task = EditPasswordTask(goal="Fix the mail username.", match="Mail",
                                set={"username": "dana@reyes.example"})
        initial = fresh()
        _edit(client, _id(client, "Mail"), username="dana@reyes.example")
        assert _passes(task, initial, fresh())

    def test_an_ambiguous_match_never_scores(self, client, fresh):
        _seed("misleading_descriptions")
        task = EditPasswordTask(goal="Fix the mail username.", match="Mail",
                                set={"username": "dana@reyes.example"})
        from open_apps import state as state_module

        initial = state_module.get_current_state("http://testserver")
        assert not _passes(task, initial, initial)

    def test_matching_by_title_and_username_picks_the_right_one(self, client, fresh):
        from open_apps import state as state_module

        _seed("misleading_descriptions")
        task = EditPasswordTask(goal="Fix the mail username.",
                                match={"title": "Mail", "username": "dana.reyes@example.com"},
                                set={"username": "dana@reyes.example"})
        initial = state_module.get_current_state("http://testserver")
        _edit(client, _id(client, "Mail", "dana.reyes@example.com"), username="dana@reyes.example")
        assert _passes(task, initial, state_module.get_current_state("http://testserver"))

    def test_an_unknown_field_is_refused(self):
        with pytest.raises(ValueError):
            EditPasswordTask(goal="x", match="Mail", set={"colour": "blue"})


class TestDelete:
    def task(self):
        return DeletePasswordTask(goal="Delete the old Netflix login.", match="Netflix (old)")

    def test_correct_episode(self, client, fresh):
        initial = fresh()
        client.post(f"/passwords/{_id(client, 'Netflix (old)')}/delete")
        assert _passes(self.task(), initial, fresh())

    def test_deleting_and_purging_fails(self, client, fresh):
        initial = fresh()
        old = _id(client, "Netflix (old)")
        client.post(f"/passwords/{old}/delete")
        client.post(f"/passwords/{old}/purge")
        assert not _passes(self.task(), initial, fresh())

    def test_deleting_the_wrong_one_fails(self, client, fresh):
        initial = fresh()
        client.post(f"/passwords/{_id(client, 'Netflix')}/delete")
        assert not _passes(self.task(), initial, fresh())

    def test_nothing_done_fails(self, client, fresh):
        initial = fresh()
        assert not _passes(self.task(), initial, fresh())


class TestOtherTasksSeeTheVault:
    def test_an_unrelated_task_fails_on_a_stray_edit(self, client, fresh):
        from open_apps.tasks.tasks import AddToDoTask

        initial = fresh()
        client.post("/todo", data={"title": "Buy chalk"})
        task = AddToDoTask(goal="Add Buy chalk", todo_name="Buy chalk", is_done=False)
        assert _passes(task, initial, fresh())
        _edit(client, _id(client, "Mail"), password="Gull-Postbox-39")
        assert not _passes(task, initial, fresh())


# ---------------------------------------------------------------------------
# The shipped task set, config/tasks/passwords.yaml.

from hydra.utils import instantiate  # noqa: E402

from open_apps import config_dir  # noqa: E402

_TASKS = OmegaConf.load(config_dir() / "tasks" / "passwords.yaml")
BASES = ["add_gym_login", "add_storage_unit_note", "change_netflix_password", "fix_mail_username",
         "update_home_wifi_password", "delete_old_netflix_login"]


def _task(key):
    return instantiate(_TASKS[key])


def _play(client, key):
    """A correct episode for each base task, through the routes."""
    if key == "add_gym_login":
        client.post("/passwords", data=GYM)
    elif key == "add_storage_unit_note":
        client.post("/passwords", data={"kind": "note", "title": "Storage unit",
                                        "notes": "Harbor Self Storage, unit B-114. Gate code 4471#."})
    elif key == "change_netflix_password":
        _edit(client, _id(client, "Netflix"), password="Popcorn-Pier-3302")
    elif key == "fix_mail_username":
        _edit(client, _id(client, "Mail", "dana.reyes@example.com"), username="dreyes@mail.example")
    elif key == "update_home_wifi_password":
        _edit(client, _id(client, "Wharf Street 5G"), password="Harbor-Tide-57!")
    elif key == "delete_old_netflix_login":
        client.post(f"/passwords/{_id(client, 'Netflix (old)')}/delete")


class TestTaskSet:
    def test_six_tasks_with_three_phrasings_each(self):
        assert sorted(k for k in _TASKS if "__" not in k) == sorted(BASES)
        for base in BASES:
            styles = {_TASKS[k].goal_style for k in _TASKS if k.startswith(base + "__")}
            assert styles == {"casual", "formal", "unrelated_context"}, base

    def test_phrasings_keep_the_scored_fields(self):
        for key in _TASKS:
            if "__" not in key:
                continue
            base, variant = dict(_TASKS[key.split("__")[0]]), dict(_TASKS[key])
            for name in ("goal", "goal_style"):
                base.pop(name, None)
                variant.pop(name, None)
            assert variant == base, key

    def test_not_in_the_canonical_inventory(self):
        assert "passwords" not in str(OmegaConf.load(config_dir() / "tasks" / "all_tasks.yaml"))

    def test_composes_as_a_task_group(self, tmp_path):
        with initialize(version_base=None, config_path="../config/"):
            cfg = compose(config_name="config", overrides=["tasks=passwords", f"logs_dir={tmp_path}"])
        assert set(cfg.tasks) == set(_TASKS)

    def test_every_task_builds_a_target_on_the_seed(self, fresh):
        initial = fresh()
        for key in _TASKS:
            _task(key).get_target_state(initial)

    @pytest.mark.parametrize("pack", ["default", "misleading_descriptions"])
    @pytest.mark.parametrize("key", BASES)
    def test_a_correct_episode_scores(self, client, fresh, key, pack):
        from open_apps import state as state_module

        _seed(pack)
        initial = state_module.get_current_state("http://testserver")
        _play(client, key)
        assert _passes(_task(key), initial, state_module.get_current_state("http://testserver"))

    def test_the_decoy_router_fails_the_wifi_task(self, client, fresh):
        from open_apps import state as state_module

        _seed("misleading_descriptions")
        initial = state_module.get_current_state("http://testserver")
        _edit(client, _id(client, "Wharf Street 5G (old router)"), password="Harbor-Tide-57!")
        assert not _passes(_task("update_home_wifi_password"), initial,
                           state_module.get_current_state("http://testserver"))

    def test_the_decoy_mail_fails_the_username_task(self, client, fresh):
        from open_apps import state as state_module

        _seed("misleading_descriptions")
        initial = state_module.get_current_state("http://testserver")
        _edit(client, _id(client, "Mail", "d.reyes.mail@example.com"), username="dreyes@mail.example")
        assert not _passes(_task("fix_mail_username"), initial,
                           state_module.get_current_state("http://testserver"))
