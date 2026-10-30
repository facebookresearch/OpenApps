"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for sending money from the bank (``openbanking_app.send``).

A send is the first write the bank makes on the user's own say-so, and the
first that reaches into a second app (Contacts, for "Save to Contacts"). What
these protect: a good send writes exactly one dated row and moves exactly two
balances; a refused one writes nothing anywhere, so a typo on the way to the
right answer never fails the task; and the payee is recorded as a canonical
handle however it was typed or picked.
"""

from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task

DANA = 3  # EVERYDAY CHECKING (...4417), appended after the business accounts
BALANCE = 4212.67


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("send")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


@pytest.fixture(autouse=True)
def reseed(client):
    """Every test starts from the seeded bank and address book."""
    from open_apps.apps.contacts_app import main as contacts_app
    from open_apps.apps.openbanking_app import main as bank

    yield
    bank.set_environment(app.config)
    contacts_app.set_environment(app.config)


def state(client) -> tuple[str, str]:
    return client.get("/openbanking_all").text, client.get("/contacts_all").text


def dana(client) -> dict:
    return client.get("/openbanking_all").json()["accounts"][DANA]


def new_rows(client) -> list[dict]:
    return [t for t in client.get("/openbanking_all").json()["transactions"] if t["counterparty"]]


def send(**kwargs):
    from open_apps.apps.openbanking_app.send import send_payment

    fields = {"from_id": DANA, "to": "rent@northgateprop.example", "amount": "1850", "memo": ""}
    fields.update(kwargs)
    return send_payment(**fields)


class TestAGoodSend:
    def test_posts_one_dated_row_and_lowers_both_balances(self, client):
        result = send(memo="Unit 2 September rent")
        assert result.ok, result.errors
        (row,) = new_rows(client)
        assert row["account_id"] == DANA
        assert row["date"] == "Aug 26, 2026"  # the bank's pinned as_of_date
        assert row["description"] == "Zelle to Northgate Properties LLC"
        assert row["type"] == "Zelle"
        assert row["amount"] == -1850.0
        assert row["balance"] == 2362.67
        assert row["counterparty"] == "rent@northgateprop.example"
        assert row["memo"] == "Unit 2 September rent"
        account = dana(client)
        assert account["available_balance"] == account["present_balance"] == 2362.67

    def test_the_new_row_sits_on_top_of_the_ledger(self, client):
        from open_apps.apps.openbanking_app import main as bank

        send(amount="18")
        assert bank.txns_for(DANA)[0].counterparty == "rent@northgateprop.example"

    def test_the_whole_balance_can_be_sent(self, client):
        assert send(amount=str(BALANCE)).ok
        assert dana(client)["available_balance"] == 0.0

    @pytest.mark.parametrize("typed", ["1,850", "$1,850.00", " 1850.0 "])
    def test_amounts_are_read_the_way_people_type_them(self, client, typed):
        assert send(amount=typed).ok
        assert new_rows(client)[0]["amount"] == -1850.0

    def test_a_blank_memo_is_stored_as_none(self, client):
        send(memo="   ")
        assert new_rows(client)[0]["memo"] is None


class TestWhoGetsPaid:
    def test_a_contact_name_pays_their_email(self, client):
        assert send(to="Bob", amount="18").ok
        (row,) = new_rows(client)
        assert row["counterparty"] == "bob@example.com"
        assert row["description"] == "Zelle to Bob"

    def test_any_spelling_of_a_handle_is_the_same_payee(self, client):
        assert send(to="(555) 010-0002", amount="18").ok
        (row,) = new_rows(client)
        assert row["counterparty"] == "+15550100002"
        assert row["description"] == "Zelle to Bob"

    def test_someone_new_is_named_by_their_handle(self, client):
        assert send(to="Sam.Ortiz@Example.com", amount="60").ok
        (row,) = new_rows(client)
        assert row["counterparty"] == "sam.ortiz@example.com"
        assert row["description"] == "Zelle to sam.ortiz@example.com"

    def test_save_to_contacts_files_them_and_names_the_row(self, client):
        result = send(to="sam.ortiz@example.com", amount="60",
                      save_as={"first_name": "Sam", "last_name": "Ortiz"})
        assert result.ok, result.errors
        assert new_rows(client)[0]["description"] == "Zelle to Sam Ortiz"
        (sam,) = [c for c in client.get("/contacts_all").json()["contacts"] if c["first_name"] == "Sam"]
        assert sam["id"] == result.contact_id
        assert sam["last_name"] == "Ortiz"
        assert [e["value"] for e in sam["emails"]] == ["sam.ortiz@example.com"]
        assert sam["phones"] == []

    def test_a_saved_phone_is_filed_as_a_phone(self, client):
        result = send(to="555-014-7788", amount="60", save_as={"first_name": "Sam"})
        assert result.ok, result.errors
        (sam,) = [c for c in client.get("/contacts_all").json()["contacts"] if c["first_name"] == "Sam"]
        assert [p["value"] for p in sam["phones"]] == ["+15550147788"]


class TestARefusedSendWritesNothing:
    @pytest.mark.parametrize(
        "fields,field",
        [
            ({"from_id": 2}, "from"),  # the business card: no sending from a card
            ({"from_id": 99}, "from"),
            ({"amount": "0"}, "amount"),
            ({"amount": "-5"}, "amount"),
            ({"amount": "lots"}, "amount"),
            ({"amount": ""}, "amount"),
            ({"amount": "4212.68"}, "amount"),  # one cent over the balance
            ({"to": "the landlord"}, "to"),
            ({"to": ""}, "to"),
            ({"to": "sam@example.com", "save_as": {"first_name": " ", "last_name": ""}}, "save_name"),
            ({"to": "bob@example.com", "save_as": {"first_name": "Robert"}}, "save_name"),
        ],
    )
    def test_each_refusal(self, client, fields, field):
        before = state(client)
        result = send(**fields)
        assert not result.ok
        assert field in result.errors and result.errors[field]
        assert state(client) == before

    def test_insufficient_funds_says_so(self, client):
        result = send(amount="5000")
        assert "Insufficient funds" in result.errors["amount"]

