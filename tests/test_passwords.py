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
from html import unescape
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


def item_id(client, title: str) -> int:
    (match,) = [i["id"] for i in items(client) if i["title"] == title]
    return match


def list_titles(html: str) -> list[str]:
    import re

    return re.findall(r'<span class="pw-row-title">([^<]*)</span>', html)


class TestBrowsing:
    @pytest.mark.parametrize("category,expected", [
        ("wifi", {"Wharf Street 5G", "Wharf Street Guest"}),
        ("note", {"Bike lock"}),
        ("favorites", {"Netflix", "OpenBanking"}),
        ("login", {"Netflix", "Netflix (old)", "Northgate Tenant Portal", "Harborline Patient Portal",
                   "OpenBanking", "Mail", "Iron Harbor Gym", "Northwind VPN"}),
    ])
    def test_each_category_lists_its_items(self, client, category, expected):
        html = client.get("/passwords", params={"category": category}).text
        assert set(list_titles(html)) == expected

    def test_all_items_is_every_item_not_deleted(self, client):
        assert set(list_titles(client.get("/passwords").text)) == {t for _, t in SEEDED}

    def test_the_list_is_alphabetical(self, client):
        titles = list_titles(client.get("/passwords").text)
        assert titles == sorted(titles, key=str.casefold)

    def test_the_sidebar_counts_each_category(self, client):
        html = client.get("/passwords").text
        assert 'href="/passwords?category=wifi"' in html
        assert '<span class="pw-count">2</span>' in html  # Wi-Fi
        assert 'href="/passwords?category=deleted"' in html

    def test_recently_deleted_starts_empty(self, client):
        html = client.get("/passwords", params={"category": "deleted"}).text
        assert list_titles(html) == []
        assert "Recently Deleted is empty." in html

    def test_rows_are_links_to_their_item(self, client):
        html = client.get("/passwords", params={"category": "wifi"}).text
        assert f'href="/passwords/{item_id(client, "Wharf Street 5G")}?category=wifi"' in html


class TestTheItem:
    def test_a_login_shows_its_fields_with_the_password_masked(self, client):
        html = client.get(f"/passwords/{item_id(client, 'Netflix')}").text
        assert 'id="pw-detail"' in html
        assert "dana.reyes@example.com" in html
        assert 'href="https://netflix.example"' in html
        assert "Family plan. Bob uses the second profile." in html
        assert "Popcorn-Wharf-2291" not in html
        assert "Last edited" in html

    def test_a_wifi_item_shows_network_and_security(self, client):
        html = client.get(f"/passwords/{item_id(client, 'Wharf Street 5G')}").text
        assert "WPA3" in html and "network name" in html
        assert "Harbor-Light-42!" not in html

    def test_a_note_shows_its_text(self, client):
        html = client.get(f"/passwords/{item_id(client, 'Bike lock')}").text
        assert "Combination 3-9-1-7" in html
        assert "password" not in html.split('id="pw-detail"')[1].split("</article>")[0]

    def test_reveal_shows_the_password_and_writes_nothing(self, client):
        before = client.get("/passwords_all").text
        netflix = item_id(client, "Netflix")
        shown = client.get(f"/passwords/{netflix}/reveal", params={"show": 1},
                           headers={"HX-Request": "true"}).text
        assert "Popcorn-Wharf-2291" in shown
        hidden = client.get(f"/passwords/{netflix}/reveal", params={"show": 0},
                            headers={"HX-Request": "true"}).text
        assert "Popcorn-Wharf-2291" not in hidden
        assert client.get("/passwords_all").text == before

    @pytest.mark.parametrize("field,value", [("password", "Popcorn-Wharf-2291"),
                                             ("username", "dana.reyes@example.com")])
    def test_copy_returns_plain_text(self, client, field, value):
        response = client.get(f"/passwords/{item_id(client, 'Netflix')}/copy/{field}")
        assert response.status_code == 200
        assert response.text == value
        assert response.headers["content-type"].startswith("text/plain")

    @pytest.mark.parametrize("path", ["/passwords/9999/copy/password", "/passwords/{note}/copy/password",
                                      "/passwords/{netflix}/copy/notes"])
    def test_copy_refuses_what_is_not_there(self, client, path):
        path = path.format(note=item_id(client, "Bike lock"), netflix=item_id(client, "Netflix"))
        assert client.get(path).status_code == 404

    def test_an_unknown_item_is_a_404_page(self, client):
        response = client.get("/passwords/9999")
        assert response.status_code == 404
        assert "doesn't exist" in unescape(response.text)


class TestSearch:
    @pytest.mark.parametrize("query,expected", [
        ("wharf", {"Wharf Street 5G", "Wharf Street Guest"}),  # title
        ("northgateprop", {"Northgate Tenant Portal"}),  # website
        ("dreyes", {"OpenBanking", "Northwind VPN"}),  # username
    ])
    def test_search_matches_title_website_and_username(self, client, query, expected):
        html = client.get("/passwords/search", params={"q": query}, headers={"HX-Request": "true"}).text
        assert set(list_titles(html)) == expected

    def test_search_never_matches_a_password(self, client):
        html = client.get("/passwords/search", params={"q": "Popcorn"}, headers={"HX-Request": "true"}).text
        assert list_titles(html) == []

    def test_a_search_with_no_match_says_so(self, client):
        html = client.get("/passwords/search", params={"q": "zzz"}, headers={"HX-Request": "true"}).text
        assert 'No items match "zzz".' in unescape(html)


class TestReadOnly:
    def test_reading_writes_nothing(self, client):
        before = client.get("/passwords_all").text
        netflix = item_id(client, "Netflix")
        for route in ["/passwords", "/passwords?category=wifi", "/passwords?category=deleted",
                      f"/passwords/{netflix}", f"/passwords/{netflix}/copy/password",
                      "/passwords/search?q=mail"]:
            assert client.get(route).status_code == 200
        assert client.get("/passwords_all").text == before

    def test_styles_use_only_the_shared_token_contract(self):
        import re

        from open_apps.apps.passwords_app.main import styles
        from open_apps.theme import load_theme

        used = set(re.findall(r"var\(--([a-z0-9-]+)", str(styles)))
        used = {t for t in used if not t.startswith(("pico-", "oa-"))}
        assert used <= set(load_theme("default")["tokens"])
