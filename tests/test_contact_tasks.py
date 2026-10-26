"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Contact tasks, scored against real episodes.

Each task in ``config/tasks/contacts.yaml`` is played through the actual
routes with a ``TestClient`` -- the same form posts the UI makes -- and the
resulting cross-app state is scored. A correct episode has to pass, and the
near-misses an agent would plausibly make (the wrong contact, one extra edit,
the unknown sender saved under the wrong name) have to fail.
"""

import contextlib
import io
from pathlib import Path

import pytest
from hydra import compose, initialize
from hydra.utils import instantiate
from omegaconf import OmegaConf
from starlette.testclient import TestClient

from open_apps import config_dir
from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task
from open_apps.tasks.tasks import AddContactTask, DeleteContactTask, EditContactTask

_TASKS = OmegaConf.load(config_dir() / "tasks" / "contacts.yaml")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("contact_tasks")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


@pytest.fixture
def fresh(client, monkeypatch):
    """Re-seed Contacts and the messenger, and return a state reader."""
    from open_apps import state as state_module
    from open_apps.apps.contacts_app import main as contacts_app
    from open_apps.apps.messenger_app import main as messenger_app

    contacts_app.set_environment(app.config)
    messenger_app.set_environment(app.config)

    def fetch(url, *args, **kwargs):
        response = client.get(url.removeprefix("http://testserver"))
        return response.json() if response.status_code == 200 else None

    monkeypatch.setattr(state_module, "safe_get_json", fetch)
    return lambda: state_module.get_current_state("http://testserver")


def _task(key):
    return instantiate(_TASKS[key])


def _passes(task, initial, current):
    with contextlib.redirect_stdout(io.StringIO()):
        return task.check_if_task_is_complete(initial, current)


def _id_of(client, first_name):
    people = client.get("/contacts_all").json()["contacts"]
    return next(c["id"] for c in people if c["first_name"] == first_name and not c["last_name"])


def _form(first="", last="", company="", phones=(), emails=(), note="", favorite=False):
    data = {"first_name": first, "last_name": last, "company": company, "note": note}
    for i, (label, value) in enumerate(phones):
        data[f"phone_label_{i}"], data[f"phone_value_{i}"] = label, value
    for i, (label, value) in enumerate(emails):
        data[f"email_label_{i}"], data[f"email_value_{i}"] = label, value
    if favorite:
        data["favorite"] = "1"
    return data


def _current_form(client, contact_id):
    person = next(c for c in client.get("/contacts_all").json()["contacts"] if c["id"] == contact_id)
    return _form(
        person["first_name"], person["last_name"], person["company"],
        [(h["label"], h["value"]) for h in person["phones"]],
        [(h["label"], h["value"]) for h in person["emails"]],
        person["note"], person["favorite"],
    )


def _send(client, handle, text):
    assert client.post("/messages/send", data={"msg": text, "interlocutor": handle}).status_code == 200


class TestTaskSet:
    def test_every_task_has_three_phrasings(self):
        bases = [k for k in _TASKS if "__" not in k]
        assert len(bases) == 6
        for base in bases:
            styles = {_TASKS[k].goal_style for k in _TASKS if k.startswith(base + "__")}
            assert styles == {"casual", "formal", "unrelated_context"}, base

    def test_phrasings_keep_the_scored_fields(self):
        for key in _TASKS:
            if "__" not in key:
                continue
            base = dict(_TASKS[key.split("__")[0]])
            variant = dict(_TASKS[key])
            for field in ("goal", "goal_style"):
                base.pop(field, None)
                variant.pop(field, None)
            assert variant == base, key

    def test_not_in_the_canonical_inventory(self):
        """Like openbanking.yaml: all_tasks is the paper's inventory."""
        assert "contacts" not in str(OmegaConf.load(config_dir() / "tasks" / "all_tasks.yaml"))


class TestAddContact:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("add_contact_dana_reyes"), fresh()
        client.post("/contacts", data=_form(
            "Dana", "Reyes", phones=[("mobile", "(555) 010-0077")],
            emails=[("home", "Dana.Reyes@example.com")],
        ))
        assert _passes(task, initial, fresh())

    def test_handle_spelling_and_label_do_not_matter(self, client, fresh):
        task, initial = _task("add_contact_dana_reyes"), fresh()
        client.post("/contacts", data=_form(
            "Dana", "Reyes", phones=[("work", "+1 555 010 0077")],
            emails=[("other", "dana.reyes@example.com")],
        ))
        assert _passes(task, initial, fresh())

    @pytest.mark.parametrize(
        "email", ["danareyes@example.com", "dana-reyes@example.com", "dana.reyes@examp.lecom"]
    )
    def test_a_different_email_fails(self, client, fresh, email):
        """Handles are addresses, not prose: the fuzzy string compare that
        forgives punctuation everywhere else must not reach them."""
        task, initial = _task("add_contact_dana_reyes"), fresh()
        client.post("/contacts", data=_form(
            "Dana", "Reyes", phones=[("mobile", "5550100077")], emails=[("home", email)],
        ))
        assert not _passes(task, initial, fresh())

    def test_missing_email_fails(self, client, fresh):
        task, initial = _task("add_contact_dana_reyes"), fresh()
        client.post("/contacts", data=_form("Dana", "Reyes", phones=[("mobile", "5550100077")]))
        assert not _passes(task, initial, fresh())

    def test_added_twice_fails(self, client, fresh):
        task, initial = _task("add_contact_dana_reyes"), fresh()
        client.post("/contacts", data=_form(
            "Dana", "Reyes", phones=[("mobile", "5550100077")], emails=[("home", "dana.reyes@example.com")],
        ))
        client.post("/contacts", data=_form("Dana", "Reyes"))
        assert not _passes(task, initial, fresh())


class TestSaveUnknownSender:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("save_unknown_sender_as_jordan_lee"), fresh()
        client.post("/contacts", data=_form("Jordan", "Lee", phones=[("mobile", "+15550142290")]))
        assert _passes(task, initial, fresh())

    def test_wrong_name_fails(self, client, fresh):
        task, initial = _task("save_unknown_sender_as_jordan_lee"), fresh()
        client.post("/contacts", data=_form("Jordan", "Climbing Gym", phones=[("mobile", "+15550142290")]))
        assert not _passes(task, initial, fresh())

    def test_wrong_number_fails(self, client, fresh):
        task, initial = _task("save_unknown_sender_as_jordan_lee"), fresh()
        client.post("/contacts", data=_form("Jordan", "Lee", phones=[("mobile", "+15550142299")]))
        assert not _passes(task, initial, fresh())


class TestEditContact:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("edit_bob_mobile_number"), fresh()
        bob = _id_of(client, "Bob")
        form = _current_form(client, bob)
        form["phone_value_0"] = "555-010-0022"
        client.post(f"/contacts/{bob}", data=form)
        assert _passes(task, initial, fresh())

    def test_editing_the_wrong_contact_fails(self, client, fresh):
        task, initial = _task("edit_bob_mobile_number"), fresh()
        alice = _id_of(client, "Alice")
        form = _current_form(client, alice)
        form["phone_value_0"] = "555-010-0022"
        client.post(f"/contacts/{alice}", data=form)
        assert not _passes(task, initial, fresh())

    def test_a_spurious_extra_edit_fails(self, client, fresh):
        task, initial = _task("edit_bob_mobile_number"), fresh()
        bob = _id_of(client, "Bob")
        form = _current_form(client, bob)
        form["phone_value_0"] = "555-010-0022"
        form["note"] = "new number"
        client.post(f"/contacts/{bob}", data=form)
        assert not _passes(task, initial, fresh())

    def test_nothing_done_fails(self, client, fresh):
        initial = fresh()
        assert not _passes(_task("edit_bob_mobile_number"), initial, fresh())


class TestDeleteContact:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("delete_charlie"), fresh()
        client.post(f"/contacts/{_id_of(client, 'Charlie')}/delete")
        assert _passes(task, initial, fresh())

    def test_deleting_the_wrong_contact_fails(self, client, fresh):
        task, initial = _task("delete_charlie"), fresh()
        client.post(f"/contacts/{_id_of(client, 'Bob')}/delete")
        assert not _passes(task, initial, fresh())

    def test_deleting_keeps_the_thread(self, client, fresh):
        """Messenger state is keyed by handle, so it doesn't move."""
        task, initial = _task("delete_charlie"), fresh()
        client.post(f"/contacts/{_id_of(client, 'Charlie')}/delete")
        after = fresh()
        assert after["messenger"] == initial["messenger"]
        assert _passes(task, initial, after)


class TestCrossApp:
    def test_reply_to_the_unknown_number(self, client, fresh):
        task, initial = _task("reply_to_unknown_number"), fresh()
        _send(client, "+15550142290", "Got it, saving your number now.")
        assert _passes(task, initial, fresh())

    def test_reply_to_someone_else_fails(self, client, fresh):
        task, initial = _task("reply_to_unknown_number"), fresh()
        _send(client, "+15550100002", "Got it, saving your number now.")
        assert not _passes(task, initial, fresh())

    def test_save_then_message_by_name(self, client, fresh):
        task, initial = _task("save_unknown_sender_then_message"), fresh()
        client.post("/contacts", data=_form("Jordan", "Lee", phones=[("mobile", "+15550142290")]))
        _send(client, "+15550142290", "Hi Jordan, saved you. See you at the gym Thursday!")
        assert _passes(task, initial, fresh())

    def test_save_without_message_fails(self, client, fresh):
        task, initial = _task("save_unknown_sender_then_message"), fresh()
        client.post("/contacts", data=_form("Jordan", "Lee", phones=[("mobile", "+15550142290")]))
        assert not _passes(task, initial, fresh())

    def test_message_without_saving_fails(self, client, fresh):
        task, initial = _task("save_unknown_sender_then_message"), fresh()
        _send(client, "+15550142290", "Hi Jordan, saved you. See you at the gym Thursday!")
        assert not _passes(task, initial, fresh())


class TestTargets:
    """Target construction errors surface at build time, not mid-scoring."""

    def test_edit_of_an_unknown_contact_raises(self, client, fresh):
        task = EditContactTask(goal="g", match="Nobody", set={"note": "x"})
        with pytest.raises(ValueError, match="Nobody"):
            task.get_target_state(fresh())

    def test_edit_of_an_unknown_field_raises(self, client, fresh):
        task = EditContactTask(goal="g", match="Bob", set={"nickname": "x"})
        with pytest.raises(ValueError, match="nickname"):
            task.get_target_state(fresh())

    def test_delete_of_an_ambiguous_name_raises(self, client, fresh):
        state = fresh()
        state["contacts"]["contacts"].append(dict(state["contacts"]["contacts"][1], id=99, last_name="Smith"))
        state["contacts"]["contacts"].append(dict(state["contacts"]["contacts"][1], id=98, last_name="Jones"))
        state["contacts"]["contacts"] = [c for c in state["contacts"]["contacts"] if c["id"] != 2]
        with pytest.raises(ValueError, match="Bob"):
            DeleteContactTask(goal="g", match="Bob").get_target_state(state)

    def test_add_with_an_invalid_handle_raises(self, client, fresh):
        task = AddContactTask(goal="g", first_name="X", phones=["not a phone"])
        with pytest.raises(ValueError, match="not a phone"):
            task.get_target_state(fresh())
