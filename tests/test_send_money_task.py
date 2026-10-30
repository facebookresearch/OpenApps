"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for `SendMoneyTask` -- the reward for paying someone from the bank.

Scored against states taken from the running bank and address book, as
`test_buy_with_card.py` does for purchases: snapshot, send through the app's
own code path, snapshot again. A target built by hand would agree with
itself and miss the drift that matters (a renamed field, a second row).

What the reward has to get right, beyond "a row appeared":

* the payee is matched *exactly*. The fuzzy diff strips punctuation, so
  without an exact check `sam.ortiz@example.com` would pay as
  `samortiz@example.com` -- someone else;
* a contact named in the task is paid at *any* of their handles;
* the memo is checked for what the task needs in it, not for its wording;
* nothing else moves: a second send, the wrong account, a stray contact.
"""

import contextlib
import io
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task
from open_apps.tasks.tasks import AddToDoTask, CompositeTask, SendMoneyTask

LAST4 = "4417"  # EVERYDAY CHECKING, Dana's account
DANA = 3


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("send-task")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


@pytest.fixture(autouse=True)
def reseed(client):
    from open_apps.apps.contacts_app import main as contacts_app
    from open_apps.apps.openbanking_app import main as bank

    # Todo is left alone: its seeding does not clear the table first, and every
    # test snapshots its own initial state anyway.
    yield
    bank.set_environment(app.config)
    contacts_app.set_environment(app.config)


def cross_state(client) -> dict:
    """The slices these tasks read, from the live apps. The others are
    stubbed empty: `preprocess` indexes them, nothing here changes them."""
    return {
        "todo": client.get("/todo_all").json(),
        "calendar": [],
        "map": [],
        "messenger": [],
        "openbanking": client.get("/openbanking_all").json(),
        "contacts": client.get("/contacts_all").json(),
    }


def send(**kwargs):
    from open_apps.apps.openbanking_app.send import send_payment

    fields = {"from_id": DANA, "to": "rent@northgateprop.example", "amount": "1850", "memo": "Unit 2"}
    fields.update(kwargs)
    result = send_payment(**fields)
    assert result.ok, result.errors
    return result


def rent(**overrides) -> SendMoneyTask:
    fields = {
        "goal": "Pay September's rent.",
        "amount": 1850,
        "from_account_last4": LAST4,
        "to": "rent@northgateprop.example",
        "memo_includes": ["Unit 2"],
    }
    fields.update(overrides)
    return SendMoneyTask(**fields)


def bob_lunch(**overrides) -> SendMoneyTask:
    fields = {"goal": "Send Bob $18 for lunch.", "amount": 18, "from_account_last4": LAST4,
              "to_contact": "Bob"}
    fields.update(overrides)
    return SendMoneyTask(**fields)


def sam(**overrides) -> SendMoneyTask:
    fields = {"goal": "Pay Sam and save them.", "amount": 60, "from_account_last4": LAST4,
              "to": "sam.ortiz@example.com",
              "save_contact": {"first_name": "Sam", "last_name": "Ortiz"}}
    fields.update(overrides)
    return SendMoneyTask(**fields)


def scores(task, initial, current) -> bool:
    with contextlib.redirect_stdout(io.StringIO()):
        return task.check_if_task_is_complete(initial, current)


class TestTheHappyPath:
    def test_rent_with_the_unit_in_the_memo(self, client):
        initial = cross_state(client)
        send(memo="Unit 2 - September")
        assert scores(rent(), initial, cross_state(client))

    @pytest.mark.parametrize("handle", ["bob@example.com", "+1 (555) 010-0002", "Bob"])
    def test_bob_at_any_of_his_handles(self, client, handle):
        initial = cross_state(client)
        send(to=handle, amount="18", memo="")
        assert scores(bob_lunch(), initial, cross_state(client))

    def test_a_new_payee_saved_to_contacts(self, client):
        initial = cross_state(client)
        send(to="sam.ortiz@example.com", amount="60", memo="",
             save_as={"first_name": "Sam", "last_name": "Ortiz"})
        assert scores(sam(), initial, cross_state(client))

    def test_memo_matching_ignores_case_and_punctuation(self, client):
        initial = cross_state(client)
        send(memo="unit #2 rent")
        assert scores(rent(), initial, cross_state(client))

    def test_a_refused_attempt_before_the_right_one_still_scores(self, client):
        from open_apps.apps.openbanking_app.send import send_payment

        initial = cross_state(client)
        assert not send_payment(from_id=DANA, to="rent@northgateprop.example", amount="99999").ok
        send()
        assert scores(rent(), initial, cross_state(client))


class TestWhatMustFail:
    def test_nothing_sent(self, client):
        initial = cross_state(client)
        assert not scores(rent(), initial, cross_state(client))

    def test_the_decoy_landlord(self, client):
        initial = cross_state(client)
        send(to="rent@northgate-pm.example")
        assert not scores(rent(), initial, cross_state(client))

    def test_a_payee_one_dot_off(self, client):
        """`samortiz@` and `sam.ortiz@` normalise to the same string in the
        fuzzy diff; the payee check must still tell them apart."""
        initial = cross_state(client)
        send(to="samortiz@example.com", amount="60", memo="",
             save_as={"first_name": "Sam", "last_name": "Ortiz"})
        assert not scores(sam(), initial, cross_state(client))

    def test_the_wrong_amount(self, client):
        initial = cross_state(client)
        send(amount="1580")
        assert not scores(rent(), initial, cross_state(client))

    def test_the_wrong_account(self, client):
        initial = cross_state(client)
        send(from_id=0)
        assert not scores(rent(), initial, cross_state(client))

    def test_the_memo_left_out(self, client):
        initial = cross_state(client)
        send(memo="September rent")
        assert not scores(rent(), initial, cross_state(client))

    def test_paying_twice(self, client):
        initial = cross_state(client)
        send()
        send()
        assert not scores(rent(), initial, cross_state(client))

    def test_a_contact_saved_that_was_not_asked_for(self, client):
        initial = cross_state(client)
        send(to="sam.ortiz@example.com", amount="60", memo="",
             save_as={"first_name": "Sam", "last_name": "Ortiz"})
        assert not scores(sam(save_contact=None), initial, cross_state(client))

    def test_a_contact_not_saved_when_asked(self, client):
        initial = cross_state(client)
        send(to="sam.ortiz@example.com", amount="60", memo="")
        assert not scores(sam(), initial, cross_state(client))

    def test_bob_paid_by_someone_elses_handle(self, client):
        initial = cross_state(client)
        send(to="charlie@example.com", amount="18", memo="")
        assert not scores(bob_lunch(), initial, cross_state(client))

    def test_a_target_the_bank_would_refuse_never_scores(self, client):
        initial = cross_state(client)
        assert not scores(rent(amount=99999), initial, cross_state(client))

    def test_an_unknown_account(self, client):
        initial = cross_state(client)
        send()
        assert not scores(rent(from_account_last4="0000"), initial, cross_state(client))


class TestOtherTasksSeeASend:
    def test_a_stray_payment_fails_an_unrelated_task(self, client):
        initial = cross_state(client)
        client.post("/todo", data={"title": "Buy milk"})
        task = AddToDoTask(goal="Add Buy milk", todo_name="Buy milk", is_done=False)
        assert scores(task, initial, cross_state(client))
        send(amount="18")
        assert not scores(task, initial, cross_state(client))


class TestComposite:
    def test_a_payment_plus_a_todo(self, client):
        initial = cross_state(client)
        send(to="Bob", amount="18", memo="")
        client.post("/todo", data={"title": "Lunch with Bob"})
        task = CompositeTask(
            goal="Pay Bob and note it",
            subtasks=[bob_lunch(), AddToDoTask(goal="note", todo_name="Lunch with Bob", is_done=False)],
        )
        assert scores(task, initial, cross_state(client))

    def test_two_payments_in_either_order(self, client):
        initial = cross_state(client)
        send(to="Bob", amount="18", memo="")
        send()
        task = CompositeTask(goal="Pay rent and Bob", subtasks=[rent(), bob_lunch()])
        assert scores(task, initial, cross_state(client))
