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
        assert 'id="pw-password-row"' not in html

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


@pytest.fixture
def reseed(client):
    """Put the seeded vault back after a test that writes."""
    from open_apps.apps.passwords_app import main as passwords_app

    yield
    passwords_app.set_environment(app.config)


def login_form(**overrides) -> dict:
    data = {"kind": "login", "title": "Harbor Climbing Gym", "username": "dana.reyes@example.com",
            "password": "Chalk-Bag-77", "website": "https://harborclimb.example", "notes": ""}
    data.update(overrides)
    return data


def by_title(client, title: str) -> dict:
    (match,) = [i for i in items(client) if i["title"] == title]
    return match


class TestCreating:
    def test_the_type_picker_offers_each_kind(self, client):
        html = client.get("/passwords/new").text
        for kind in ("login", "wifi", "note"):
            assert f'href="/passwords/new?kind={kind}"' in html

    def test_each_kind_gets_its_own_fields(self, client):
        assert 'name="website"' in client.get("/passwords/new", params={"kind": "login"}).text
        wifi = client.get("/passwords/new", params={"kind": "wifi"}).text
        assert 'name="security"' in wifi and 'name="website"' not in wifi
        note = client.get("/passwords/new", params={"kind": "note"}).text
        assert 'name="password"' not in note

    def test_a_login_is_saved_with_the_pinned_date(self, client, reseed):
        response = client.post("/passwords", data=login_form(), follow_redirects=False)
        assert response.status_code == 303
        gym = by_title(client, "Harbor Climbing Gym")
        assert response.headers["location"] == f"/passwords/{gym['id']}"
        assert gym["password"] == "Chalk-Bag-77"
        assert gym["edited_on"] == app.config.passwords.today
        assert not gym["deleted"]

    def test_a_password_is_stored_exactly_as_typed(self, client, reseed):
        client.post("/passwords", data=login_form(password="  spaced pass  "), follow_redirects=False)
        assert by_title(client, "Harbor Climbing Gym")["password"] == "  spaced pass  "

    def test_a_note_and_a_wifi_network(self, client, reseed):
        client.post("/passwords", data={"kind": "note", "title": "Storage unit", "notes": "Gate code 4471#"},
                    follow_redirects=False)
        client.post("/passwords", data={"kind": "wifi", "title": "Cabin", "password": "pine-cone-8",
                                        "security": "WPA2"}, follow_redirects=False)
        assert by_title(client, "Storage unit")["kind"] == "note"
        assert by_title(client, "Cabin")["security"] == "WPA2"

    @pytest.mark.parametrize("data,message", [
        (login_form(title="  "), "Add a title."),
        (login_form(username="", password=""), "A login needs a username or a password."),
        ({"kind": "wifi", "title": "Cabin", "password": ""}, "A Wi-Fi network needs a password."),
    ])
    def test_refusals_write_nothing_and_say_why(self, client, reseed, data, message):
        before = client.get("/passwords_all").text
        response = client.post("/passwords", data=data, follow_redirects=False)
        assert response.status_code == 422
        assert message in unescape(response.text)
        assert client.get("/passwords_all").text == before

    def test_a_refusal_through_htmx_swaps_the_form_in_place(self, client, reseed):
        response = client.post("/passwords", data=login_form(title=""), headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert 'id="pw-detail"' in response.text and "Add a title." in unescape(response.text)

    def test_a_save_through_htmx_navigates(self, client, reseed):
        response = client.post("/passwords", data=login_form(), headers={"HX-Request": "true"})
        assert response.headers["HX-Redirect"].startswith("/passwords/")


class TestEditing:
    def test_the_form_is_prefilled(self, client):
        html = client.get(f"/passwords/{item_id(client, 'Netflix')}/edit").text
        assert 'value="dana.reyes@example.com"' in html
        assert 'value="Popcorn-Wharf-2291"' in html  # editing shows the secret, as the app does

    def test_saving_changes_the_fields_and_the_date(self, client, reseed):
        netflix = item_id(client, "Netflix")
        data = login_form(title="Netflix", username="dana.reyes@example.com", password="New-Popcorn-1",
                          website="https://netflix.example", notes="Family plan. Bob uses the second profile.",
                          favorite="1")
        response = client.post(f"/passwords/{netflix}", data=data, follow_redirects=False)
        assert response.status_code == 303
        after = by_title(client, "Netflix")
        assert after["password"] == "New-Popcorn-1"
        assert after["favorite"] is True
        assert after["edited_on"] == app.config.passwords.today

    def test_the_kind_cannot_change(self, client, reseed):
        netflix = item_id(client, "Netflix")
        client.post(f"/passwords/{netflix}", data=login_form(kind="note", title="Netflix"), follow_redirects=False)
        assert by_title(client, "Netflix")["kind"] == "login"

    def test_a_refused_edit_writes_nothing(self, client, reseed):
        before = client.get("/passwords_all").text
        netflix = item_id(client, "Netflix")
        assert client.post(f"/passwords/{netflix}", data=login_form(title=""), follow_redirects=False).status_code == 422
        assert client.get("/passwords_all").text == before

    def test_editing_an_unknown_item_is_a_404(self, client):
        assert client.post("/passwords/9999", data=login_form(), follow_redirects=False).status_code == 404


class TestDeleting:
    def test_the_item_page_offers_delete_behind_a_confirm(self, client):
        html = client.get(f"/passwords/{item_id(client, 'Netflix (old)')}").text
        assert 'id="pw-delete-dialog"' in html
        assert f'action="/passwords/{item_id(client, "Netflix (old)")}/delete"' in html

    def test_delete_moves_it_to_recently_deleted(self, client, reseed):
        old = item_id(client, "Netflix (old)")
        response = client.post(f"/passwords/{old}/delete", follow_redirects=False)
        assert response.status_code == 303
        assert by_title(client, "Netflix (old)")["deleted"] is True
        assert "Netflix (old)" not in list_titles(client.get("/passwords").text)
        assert list_titles(client.get("/passwords", params={"category": "deleted"}).text) == ["Netflix (old)"]

    def test_a_deleted_item_offers_only_permanent_deletion(self, client, reseed):
        old = item_id(client, "Netflix (old)")
        client.post(f"/passwords/{old}/delete")
        html = client.get(f"/passwords/{old}", params={"category": "deleted"}).text
        assert f'action="/passwords/{old}/purge"' in html
        assert f'href="/passwords/{old}/edit"' not in html

    def test_purge_removes_it(self, client, reseed):
        old = item_id(client, "Netflix (old)")
        client.post(f"/passwords/{old}/delete")
        response = client.post(f"/passwords/{old}/purge", follow_redirects=False)
        assert response.status_code == 303
        assert "Netflix (old)" not in {i["title"] for i in items(client)}

    def test_purge_refuses_an_item_not_in_recently_deleted(self, client, reseed):
        before = client.get("/passwords_all").text
        assert client.post(f"/passwords/{item_id(client, 'Netflix')}/purge").status_code == 404
        assert client.get("/passwords_all").text == before

    def test_a_deleted_item_cannot_be_edited(self, client, reseed):
        old = item_id(client, "Netflix (old)")
        client.post(f"/passwords/{old}/delete")
        assert client.get(f"/passwords/{old}/edit").status_code == 404


def _reconfigure(**changes):
    """Re-seed the passwords app on a resolved copy of the live config with
    ``changes`` applied under ``apps`` (dotted keys)."""
    from omegaconf import OmegaConf

    from open_apps.apps.passwords_app import main as passwords_app

    cfg = OmegaConf.create(OmegaConf.to_container(app.config, resolve=True))
    for key, value in changes.items():
        OmegaConf.update(cfg, key, value, merge=False)
    passwords_app.set_environment(cfg)


def rendered_testids(html: str) -> set[str]:
    import re

    return set(re.findall(r'data-testid="([^"]+)"', html))


class TestLayouts:
    def test_default_is_three_panes(self, client):
        html = client.get(f"/passwords/{item_id(client, 'Netflix')}").text
        assert 'class="pw-sidebar"' in html and 'class="pw-listpane"' in html and 'id="pw-detail"' in html
        assert 'data-testid="back-to-list"' not in html

    def test_list_detail_is_one_pane_at_a_time(self, client, reseed):
        _reconfigure(**{"passwords.layout": "list_detail"})
        netflix = item_id(client, "Netflix")
        index, card = client.get("/passwords").text, client.get(f"/passwords/{netflix}").text
        assert 'class="pw-listpane"' in index and 'id="pw-detail"' not in index
        assert 'id="pw-detail"' in card and 'class="pw-listpane"' not in card
        assert 'data-testid="back-to-list"' in card

    def test_a_phone_is_single_pane(self, client, reseed):
        _reconfigure(**{"device.form_factor": "phone"})
        card = client.get(f"/passwords/{item_id(client, 'Netflix')}").text
        assert 'class="pw-listpane"' not in card and "pw-root is-phone" in card

    @pytest.mark.parametrize("path", ["/passwords/{netflix}", "/passwords/{netflix}/edit", "/passwords/new"])
    def test_both_layouts_render_the_same_controls(self, client, reseed, path):
        path = path.format(netflix=item_id(client, "Netflix"))
        three = {t for t in rendered_testids(client.get(path).text)
                 if not t.startswith(("item-row-", "category-"))}
        _reconfigure(**{"passwords.layout": "list_detail"})
        one = rendered_testids(client.get(path).text)
        assert one - {"back-to-list"} == three


class TestContentPacks:
    def _load(self, variant, tmp_path):
        from omegaconf import OmegaConf

        from open_apps.apps.passwords_app import main as passwords_app

        with initialize(version_base=None, config_path="../config/"):
            cfg = compose(config_name="config",
                          overrides=[f"logs_dir={tmp_path}", f"apps/passwords/content={variant}"])
        apps_cfg = OmegaConf.create(OmegaConf.to_container(app.config, resolve=True))
        apps_cfg.passwords = OmegaConf.to_container(cfg.apps.passwords, resolve=True)
        passwords_app.set_environment(apps_cfg)

    @pytest.mark.parametrize("variant,placeholder", [("german", "Persönlich durchsuchen"),
                                                     ("mandarin", "搜索个人")])
    def test_translated_packs_keep_the_items(self, client, reseed, variant, placeholder, tmp_path):
        before = client.get("/passwords_all").text
        self._load(variant, tmp_path)
        assert f'placeholder="{placeholder}"' in client.get("/passwords").text
        assert client.get("/passwords_all").text == before

    @pytest.mark.parametrize("variant", ["long_descriptions", "misleading_descriptions",
                                         "adversarial_descriptions"])
    def test_noise_packs_add_items_and_keep_the_seed(self, client, reseed, variant, tmp_path):
        seeded = items(client)
        self._load(variant, tmp_path)
        after = items(client)
        assert after[: len(seeded)] == seeded
        assert len(after) > len(seeded)

    def test_the_misleading_pack_has_lookalikes(self, client, reseed, tmp_path):
        self._load("misleading_descriptions", tmp_path)
        titles = [i["title"] for i in items(client)]
        assert titles.count("Mail") == 2
        assert "Wharf Street 5G (old router)" in titles

    def test_the_adversarial_pack_carries_the_shared_message(self, client, reseed, tmp_path):
        self._load("adversarial_descriptions", tmp_path)
        message = str(app.config.adversarial_message).strip()
        assert any(message in (i["title"] + i["notes"]) for i in items(client))

    @pytest.mark.parametrize("variant", ["default", "german", "mandarin", "long_descriptions",
                                         "misleading_descriptions", "adversarial_descriptions"])
    def test_every_pack_renders(self, client, reseed, variant, tmp_path):
        self._load(variant, tmp_path)
        assert client.get("/passwords").status_code == 200
        assert client.get(f"/passwords/{items(client)[-1]['id']}").status_code == 200
