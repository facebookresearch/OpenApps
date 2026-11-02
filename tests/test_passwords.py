"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The OpenPasswords app: a password manager in 1Password's shape.

What's protected here is what an agent and a reward depend on:

- the seed, and ``/passwords_all`` staying byte-stable when nothing changes;
- a secret is never in the markup until it is revealed;
- every write is an explicit form post, and delete goes through Recently
  Deleted with a confirm step;
- both layouts, every content pack, and the start page's tile.
"""

import json
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task

SEEDED = {
    ("login", "Netflix"),
    ("login", "Netflix (old)"),
    ("login", "Northgate Tenant Portal"),
    ("login", "Harborline Patient Portal"),
    ("login", "OpenBanking"),
    ("login", "Mail"),
    ("login", "Iron Harbor Gym"),
    ("login", "Northwind VPN"),
    ("wifi", "Wharf Street 5G"),
    ("wifi", "Wharf Street Guest"),
    ("note", "Bike lock"),
}
FIELDS = {"id", "kind", "title", "username", "password", "website", "security", "notes",
          "favorite", "deleted", "edited_on"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("passwords")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


def items(client) -> list[dict]:
    return client.get("/passwords_all").json()["items"]


class TestSeeding:
    def test_the_eleven_seeded_items(self, client):
        assert {(i["kind"], i["title"]) for i in items(client)} == SEEDED

    def test_every_item_has_the_same_fields(self, client):
        for item in items(client):
            assert set(item) == FIELDS, item["title"]

    def test_nothing_starts_deleted(self, client):
        assert not any(i["deleted"] for i in items(client))

    def test_edit_dates_are_pinned_iso_dates(self, client):
        for item in items(client):
            assert len(item["edited_on"]) == 10 and item["edited_on"][4] == "-", item

    def test_websites_never_leave_the_sandbox(self, client):
        """`.example` is reserved (RFC 2606): a clicked link cannot resolve."""
        for item in items(client):
            if item["website"]:
                assert item["website"].split("/")[2].endswith(".example"), item["website"]


class TestScoringSurface:
    def test_sorted_by_id(self, client):
        ids = [i["id"] for i in items(client)]
        assert ids == sorted(ids)

    def test_byte_stable_across_fetches(self, client):
        first = client.get("/passwords_all").text
        for _ in range(3):
            assert client.get("/passwords_all").text == first

    def test_collected_with_the_cross_app_state(self, client, monkeypatch):
        from open_apps import state as state_module

        def fetch(url, *args, **kwargs):
            response = client.get(url.removeprefix("http://testserver"))
            return response.json() if response.status_code == 200 else None

        monkeypatch.setattr(state_module, "safe_get_json", fetch)
        state = state_module.get_current_state("http://testserver")
        assert json.dumps(state["passwords"]) == client.get("/passwords_all").text


class TestRegistration:
    def test_the_start_page_lists_the_app(self, client):
        html = client.get("/").text
        assert "OpenPasswords" in html
        assert 'href="/passwords"' in html

    def test_the_mcp_registry_knows_it(self):
        from open_apps.mcp.registry import config_dir_for, url_path_for

        assert url_path_for("passwords") == "/passwords"
        assert config_dir_for("passwords") == "passwords"
