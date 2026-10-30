"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Payment tasks, scored against real episodes.

Each task in ``config/tasks/payments.yaml`` is played through the bank's own
routes with a ``TestClient`` -- the review GET and the confirm POST the Send
money pages make -- and the full cross-app state is scored. A correct episode
has to pass; the near-misses an agent would plausibly make (the decoy
landlord, the renewal offer's new rent, no memo, Sam left out of Contacts)
have to fail.
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
from open_apps.tasks.tasks import SendMoneyTask

_TASKS = OmegaConf.load(config_dir() / "tasks" / "payments.yaml")
DANA = 3


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("payment_tasks")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


def _contacts_pack(pack: str):
    """Re-seed Contacts from ``pack`` on a resolved copy of the live config."""
    from open_apps.apps.contacts_app import main as contacts_app

    with initialize(version_base=None, config_path="../config/"):
        packed = compose(config_name="config", overrides=[f"apps/contacts/content={pack}"])
    cfg = OmegaConf.create(OmegaConf.to_container(app.config, resolve=True))
    cfg.contacts.contacts = OmegaConf.to_container(packed.apps.contacts.contacts, resolve=True)
    contacts_app.set_environment(cfg)


@pytest.fixture(params=["default", "misleading_descriptions"])
def fresh(request, client, monkeypatch):
    """Re-seed the bank and Contacts (in each pack), and return a state reader."""
    from open_apps import state as state_module
    from open_apps.apps.contacts_app import main as contacts_app
    from open_apps.apps.openbanking_app import main as bank

    bank.set_environment(app.config)
    _contacts_pack(request.param)

    def fetch(url, *args, **kwargs):
        response = client.get(url.removeprefix("http://testserver"))
        return response.json() if response.status_code == 200 else None

    monkeypatch.setattr(state_module, "safe_get_json", fetch)
    yield lambda: state_module.get_current_state("http://testserver")
    bank.set_environment(app.config)
    contacts_app.set_environment(app.config)


def _task(key) -> SendMoneyTask:
    return instantiate(_TASKS[key])


def _passes(task, initial, current):
    with contextlib.redirect_stdout(io.StringIO()):
        return task.check_if_task_is_complete(initial, current)


def _pay(client, to, amount, memo="", **save):
    """Review, then confirm -- the two requests the Send money pages make."""
    params = {"from": DANA, "to": to, "amount": amount, "memo": memo}
    review = client.get("/openbanking/send/review", params=params)
    assert 'id="ob-send-review"' in review.text, "the review step refused the payment"
    data = dict(params)
    if save:
        data.update(save=1, **save)
    response = client.post("/openbanking/send/confirm", data=data, follow_redirects=False)
    assert "?sent=" in response.headers["location"], "the confirm step refused the payment"


class TestTaskSet:
    def test_every_task_has_three_phrasings(self):
        bases = [k for k in _TASKS if "__" not in k]
        assert sorted(bases) == ["pay_rent_to_northgate", "pay_sam_and_save_contact", "send_bob_lunch_money"]
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

    def test_every_task_builds_a_target_on_the_seeded_state(self, fresh):
        initial = fresh()
        for key in _TASKS:
            _task(key).get_target_state(initial)

    def test_not_in_the_canonical_inventory(self):
        assert "payments" not in str(OmegaConf.load(config_dir() / "tasks" / "all_tasks.yaml"))

    def test_composes_through_hydra(self, tmp_path):
        with initialize(version_base=None, config_path="../config/"):
            cfg = compose(config_name="config", overrides=["tasks=payments", f"logs_dir={tmp_path}"])
        assert set(cfg.tasks) == set(_TASKS)

    def test_the_rent_figures_are_the_leases(self):
        """$1,850 to the address the lease names, with the unit in the memo --
        the renewal offer's $1,925 starts in October, after the rent asked for."""
        task = _task("pay_rent_to_northgate")
        assert task.amount == 1850
        assert task.to == "rent@northgateprop.example"
        assert task.memo_includes == ["Unit 2"]
        assert "September" in task.goal


class TestPayRent:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("pay_rent_to_northgate"), fresh()
        _pay(client, "rent@northgateprop.example", "1,850.00", memo="Unit 2 - September rent")
        assert _passes(task, initial, fresh())

    def test_the_decoy_landlord_fails(self, client, fresh):
        task, initial = _task("pay_rent_to_northgate"), fresh()
        _pay(client, "rent@northgate-pm.example", "1850", memo="Unit 2")
        assert not _passes(task, initial, fresh())

    def test_the_renewal_rent_fails(self, client, fresh):
        task, initial = _task("pay_rent_to_northgate"), fresh()
        _pay(client, "rent@northgateprop.example", "1925", memo="Unit 2")
        assert not _passes(task, initial, fresh())

    def test_no_unit_in_the_memo_fails(self, client, fresh):
        task, initial = _task("pay_rent_to_northgate"), fresh()
        _pay(client, "rent@northgateprop.example", "1850", memo="September rent")
        assert not _passes(task, initial, fresh())

    def test_picking_the_landlord_by_name_works(self, client, fresh):
        task, initial = _task("pay_rent_to_northgate"), fresh()
        _pay(client, "Northgate Properties LLC", "1850", memo="Unit 2")
        assert _passes(task, initial, fresh())


class TestSendBob:
    @pytest.mark.parametrize("to", ["Bob", "bob@example.com", "555-010-0002"])
    def test_correct_episode(self, client, fresh, to):
        task, initial = _task("send_bob_lunch_money"), fresh()
        _pay(client, to, "18", memo="lunch")
        assert _passes(task, initial, fresh())

    def test_the_wrong_bob_fails(self, client, fresh):
        """The misleading pack's "Bob Smith" is not Bob; in the default pack the
        handle belongs to nobody."""
        task, initial = _task("send_bob_lunch_money"), fresh()
        _pay(client, "bob.smith@example.com", "18")
        assert not _passes(task, initial, fresh())


class TestPaySam:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("pay_sam_and_save_contact"), fresh()
        _pay(client, "sam.ortiz@example.com", "60", first_name="Sam", last_name="Ortiz")
        assert _passes(task, initial, fresh())

    def test_paying_without_saving_fails(self, client, fresh):
        task, initial = _task("pay_sam_and_save_contact"), fresh()
        _pay(client, "sam.ortiz@example.com", "60")
        assert not _passes(task, initial, fresh())

    def test_saving_through_contacts_afterwards_also_counts(self, client, fresh):
        """The reward is the end state: Sam paid and in the book. Saving him
        with the New Contact form after paying is the same outcome."""
        task, initial = _task("pay_sam_and_save_contact"), fresh()
        _pay(client, "sam.ortiz@example.com", "60")
        client.post("/contacts", data={"first_name": "Sam", "last_name": "Ortiz",
                                       "email_label_0": "home", "email_value_0": "sam.ortiz@example.com"})
        assert _passes(task, initial, fresh())
