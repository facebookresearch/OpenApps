"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The Contacts app: the address book every other app resolves names through.

The messenger stores handles, never names, and payments and password sharing
pick recipients from here. So the things worth protecting are the lookups
(handle -> contact, name -> contacts) and the scoring surface, which has to be
byte-stable across reads like every other app's ``/<app>_all``.
"""

import json
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("contacts")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


def contacts(client) -> list[dict]:
    return client.get("/contacts_all").json()["contacts"]


class TestSeeding:
    def test_seeded_people_are_present(self, client):
        names = {(c["first_name"], c["last_name"], c["company"]) for c in contacts(client)}
        assert {
            ("Alice", "", ""),
            ("Bob", "", ""),
            ("Charlie", "", ""),
            ("Priya", "Shah", ""),
            ("Marcus", "Webb", ""),
            ("", "", "Northgate Properties LLC"),
        } <= names

    def test_seeded_handles_are_stored_normalised(self, client):
        alice = next(c for c in contacts(client) if c["first_name"] == "Alice")
        assert alice["phones"] == [{"label": "mobile", "value": "+15550100001"}]
        assert alice["emails"] == [{"label": "home", "value": "alice@example.com"}]

    def test_the_unknown_sender_is_not_a_contact(self, client):
        from open_apps.apps.contacts_app.main import contact_for_handle

        assert contact_for_handle("+15550142290") is None

    def test_reseeding_is_idempotent(self, client, tmp_path):
        from open_apps.apps.contacts_app import main as contacts_app

        before = client.get("/contacts_all").text
        contacts_app.set_environment(app.config)
        assert client.get("/contacts_all").text == before


class TestScoringSurface:
    def test_payload_is_byte_stable(self, client):
        assert client.get("/contacts_all").text == client.get("/contacts_all").text

    def test_payload_is_sorted_by_id(self, client):
        ids = [c["id"] for c in contacts(client)]
        assert ids == sorted(ids)

    def test_payload_shape(self, client):
        payload = client.get("/contacts_all").json()
        assert set(payload) == {"contacts"}
        assert set(payload["contacts"][0]) == {
            "id", "first_name", "last_name", "company", "note", "favorite", "phones", "emails",
        }
        json.dumps(payload)

    def test_cross_app_state_carries_contacts(self, client, monkeypatch):
        """Tasks resolve message recipients through the contacts slice, so
        get_current_state has to fetch it alongside the other apps."""
        from open_apps import state as state_module

        def fetch(url, *args, **kwargs):
            path = url.removeprefix("http://testserver")
            response = client.get(path)
            return response.json() if response.status_code == 200 else None

        monkeypatch.setattr(state_module, "safe_get_json", fetch)
        state = state_module.get_current_state("http://testserver")
        assert state["contacts"] == client.get("/contacts_all").json()


class TestLookups:
    def test_contact_for_handle_accepts_any_spelling(self, client):
        from open_apps.apps.contacts_app.main import contact_for_handle

        bob = contact_for_handle("(555) 010-0002")
        assert bob is not None and bob.first_name == "Bob"
        assert contact_for_handle("BOB@example.com").id == bob.id

    @pytest.mark.parametrize(
        "handle,expected",
        [
            ("+15550100001", "Alice"),
            ("+15550100004", "Priya Shah"),
            ("rent@northgateprop.example", "Northgate Properties LLC"),
            ("+15550142290", "+1 (555) 014-2290"),
            ("72524", "72524"),
        ],
    )
    def test_contact_name_falls_back_to_the_formatted_handle(self, client, handle, expected):
        from open_apps.apps.contacts_app.main import contact_name

        assert contact_name(handle) == expected

    @pytest.mark.parametrize(
        "query,expected",
        [
            ("Bob", ["Bob"]),
            ("bob", ["Bob"]),
            ("Priya", ["Priya Shah"]),
            ("priya shah", ["Priya Shah"]),
            ("Northgate Properties LLC", ["Northgate Properties LLC"]),
            ("Nobody", []),
        ],
    )
    def test_find_by_name_matches_full_or_first_name(self, client, query, expected):
        from open_apps.apps.contacts_app.main import display_name, find_by_name

        assert [display_name(c) for c in find_by_name(query)] == expected


class TestSeedValidation:
    """A bad seed fails the launch loudly. Which contact owns a handle decides
    the name a thread shows and who a payment reaches, so two people sharing
    one, or a handle nobody can match, must never load silently."""

    def _reseed(self, entries):
        from omegaconf import OmegaConf

        from open_apps.apps.contacts_app import main as contacts_app

        cfg = OmegaConf.create(OmegaConf.to_container(app.config, resolve=True))
        cfg.contacts.contacts = entries
        contacts_app.set_environment(cfg)

    def test_duplicate_handle_across_contacts_is_refused(self, client):
        from open_apps.apps.contacts_app import main as contacts_app

        try:
            with pytest.raises(ValueError, match=r"\+15550100001 belongs to both 'Alice' and 'Al'"):
                self._reseed([
                    {"first_name": "Alice", "phones": [{"value": "+1 (555) 010-0001"}]},
                    {"first_name": "Al", "phones": [{"value": "555.010.0001"}]},
                ])
        finally:
            contacts_app.set_environment(app.config)

    def test_malformed_handle_is_refused(self, client):
        from open_apps.apps.contacts_app import main as contacts_app

        try:
            with pytest.raises(ValueError, match="not a valid email"):
                self._reseed([{"first_name": "X", "emails": [{"value": "not-an-email"}]}])
        finally:
            contacts_app.set_environment(app.config)
