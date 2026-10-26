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
import re
from html import unescape
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


# ---------------------------------------------------------------------------
# Interface.


def _reconfigure(**changes):
    """Re-seed the contacts app on a resolved copy of the live config with
    ``changes`` applied under ``apps`` (dotted keys)."""
    from omegaconf import OmegaConf

    from open_apps.apps.contacts_app import main as contacts_app

    cfg = OmegaConf.create(OmegaConf.to_container(app.config, resolve=True))
    for key, value in changes.items():
        OmegaConf.update(cfg, key, value, merge=False)
    contacts_app.set_environment(cfg)


@pytest.fixture
def reseed(client):
    """Put the seeded address book (and the default layout and device) back
    after a test that writes or reconfigures."""
    from open_apps.apps.contacts_app import main as contacts_app

    yield
    contacts_app.set_environment(app.config)


def by_name(client, name: str) -> dict:
    from open_apps.apps.contacts_app.main import display_name, find_by_name

    match = find_by_name(name)
    assert len(match) == 1, [display_name(c) for c in match]
    return next(c for c in contacts(client) if c["id"] == match[0].id)


def rendered_testids(html: str) -> set[str]:
    return set(re.findall(r'data-testid="([^"]+)"', html))


HX = {"HX-Request": "true"}


class TestPages:
    @pytest.mark.parametrize(
        "path", ["/contacts", "/contacts/1", "/contacts/1/edit", "/contacts/new"]
    )
    def test_pages_render_in_the_app_root(self, client, path):
        response = client.get(path)
        assert response.status_code == 200
        assert 'class="ct-root' in response.text

    def test_card_shows_name_formatted_handles_and_copy_pills(self, client):
        alice = by_name(client, "Alice")
        html = client.get(f"/contacts/{alice['id']}").text
        assert re.search(r'<h2[^>]*data-testid="contact-name"[^>]*>Alice</h2>', html)
        assert "+1 (555) 010-0001" in html
        assert html.count('data-copy-url="/contacts/') == 2

    def test_copy_route_serves_the_shown_value(self, client):
        from open_apps.apps.contacts_app.main import handles_for

        alice = by_name(client, "Alice")
        phone, email = handles_for(alice["id"])
        assert client.get(f"/contacts/{alice['id']}/copy/{phone.id}").text == "+1 (555) 010-0001"
        assert client.get(f"/contacts/{alice['id']}/copy/{email.id}").text == "alice@example.com"

    def test_copy_route_refuses_another_contacts_handle(self, client):
        from open_apps.apps.contacts_app.main import handles_for

        alice, bob = by_name(client, "Alice"), by_name(client, "Bob")
        bobs_phone = handles_for(bob["id"])[0]
        assert client.get(f"/contacts/{alice['id']}/copy/{bobs_phone.id}").status_code == 404

    def test_message_goes_to_the_primary_handle_thread(self, client):
        alice = by_name(client, "Alice")
        html = client.get(f"/contacts/{alice['id']}").text
        assert 'href="/messages/%2B15550100001/"' in html

    def test_email_and_send_money_are_disabled_with_a_reason(self, client):
        html = client.get("/contacts/1").text
        for testid in ("action-email", "action-send-money"):
            button = re.search(rf'<button[^>]*data-testid="{testid}"[^>]*>', html).group(0)
            assert "disabled" in button
        assert html.count('title="Coming soon"') == 2

    def test_unknown_contact_is_a_404_page_with_the_chrome(self, client):
        response = client.get("/contacts/9999")
        assert response.status_code == 404
        assert "doesn't exist" in unescape(response.text)
        assert 'class="ct-root' in response.text

    def test_selected_row_is_marked_current(self, client):
        bob = by_name(client, "Bob")
        html = client.get(f"/contacts/{bob['id']}").text
        row = re.search(rf'<a[^>]*id="contact-row-{bob["id"]}"[^>]*>', html).group(0)
        assert 'aria-current="page"' in row

    def test_rows_are_real_links(self, client):
        html = client.get("/contacts").text
        for person in contacts(client):
            assert f'href="/contacts/{person["id"]}"' in html

    def test_favorites_come_first(self, client):
        html = client.get("/contacts").text
        marcus = by_name(client, "Marcus Webb")
        assert f'id="favorite-row-{marcus["id"]}"' in html
        assert html.index("Favorites") < html.index('id="ct-section-A"')

    def test_index_strip_links_only_letters_with_people(self, client):
        html = client.get("/contacts").text
        strip = re.search(r'<nav[^>]*class="ct-index".*?</nav>', html, re.S).group(0)
        linked = set(re.findall(r'href="#ct-section-(.)"', strip))
        assert linked == {"A", "B", "C", "M", "N", "P"}

    def test_stylesheet_uses_theme_tokens_only(self, client):
        from fasthtml.common import to_xml

        from open_apps.apps.contacts_app.main import styles
        from open_apps.ui.copy import copy_styles

        css = to_xml(styles) + copy_styles("ct-copy")
        assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
        assert not re.findall(r"\brgba?\(", css)


class TestSearch:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("priya", {"Priya Shah"}),
            ("NORTHGATE", {"Northgate Properties LLC"}),
            ("0100002", {"Bob"}),
            ("(555) 010-0003", {"Charlie"}),
            ("bob@", {"Bob"}),
        ],
    )
    def test_search_matches_names_and_handles(self, client, query, expected):
        html = client.get("/contacts/search", params={"q": query}, headers=HX).text
        shown = set(re.findall(r'<span class="ct-row-name">([^<]+)</span>', html))
        assert shown == expected

    def test_search_partial_is_just_the_list(self, client):
        html = client.get("/contacts/search", params={"q": "a"}, headers=HX).text
        assert html.lstrip().startswith('<div id="ct-list"')
        assert "<html" not in html

    def test_no_match_says_so(self, client):
        html = client.get("/contacts/search", params={"q": "zzz"}, headers=HX).text
        assert 'No contacts match "zzz".' in unescape(html)

    def test_search_drops_favorites_and_the_index(self, client):
        html = client.get("/contacts/search", params={"q": "marcus"}, headers=HX).text
        assert "favorite-row-" not in html and "ct-index" not in html


class TestEditing:
    def test_create_stores_normalised_handles(self, client, reseed):
        response = client.post(
            "/contacts",
            data={"first_name": "Dana", "last_name": "Reyes", "phone_label_0": "work",
                  "phone_value_0": "555.010.0099", "email_label_0": "home",
                  "email_value_0": " Dana@Example.com "},
            follow_redirects=False,
        )
        assert response.status_code == 303
        dana = by_name(client, "Dana Reyes")
        assert response.headers["location"] == f"/contacts/{dana['id']}"
        assert dana["phones"] == [{"label": "work", "value": "+15550100099"}]
        assert dana["emails"] == [{"label": "home", "value": "dana@example.com"}]

    def test_new_form_prefills_a_handle(self, client):
        html = client.get("/contacts/new", params={"phone": "5550142290"}).text
        assert 'value="+1 (555) 014-2290"' in html

    @pytest.mark.parametrize(
        "field,value,message",
        [
            ("phone", "call me", "Enter a phone number like"),
            ("phone", "bob@example.org", "Enter a phone number like"),
            ("email", "not-an-email", "Enter an email address like"),
        ],
    )
    def test_malformed_handle_is_refused_inline(self, client, reseed, field, value, message):
        before = client.get("/contacts_all").text
        response = client.post(
            "/contacts", data={"first_name": "Dana", f"{field}_value_0": value}
        )
        assert response.status_code == 422
        assert message in response.text
        assert 'aria-invalid="true"' in response.text
        assert client.get("/contacts_all").text == before

    def test_duplicate_handle_names_its_owner(self, client, reseed):
        before = client.get("/contacts_all").text
        response = client.post(
            "/contacts", data={"first_name": "Dana", "phone_value_0": "(555) 010-0001"}
        )
        assert response.status_code == 422
        assert "+1 (555) 010-0001 is already saved for Alice." in response.text
        assert client.get("/contacts_all").text == before

    def test_handle_listed_twice_is_refused(self, client, reseed):
        response = client.post(
            "/contacts",
            data={"first_name": "Dana", "phone_value_0": "5550100099", "phone_value_1": "+15550100099"},
        )
        assert response.status_code == 422
        assert "is listed twice" in response.text

    def test_empty_contact_is_refused(self, client, reseed):
        response = client.post("/contacts", data={"note": "nobody"})
        assert response.status_code == 422
        assert "Add a name, a company" in response.text

    def test_refused_form_keeps_what_was_typed(self, client, reseed):
        response = client.post(
            "/contacts",
            data={"first_name": "Dana", "company": "Acme", "phone_value_0": "(555) 010-0001"},
        )
        assert 'value="Dana"' in response.text and 'value="Acme"' in response.text

    def test_update_renames_and_replaces_handles(self, client, reseed):
        bob = by_name(client, "Bob")
        response = client.post(
            f"/contacts/{bob['id']}",
            data={"first_name": "Robert", "last_name": "Lee", "phone_label_0": "mobile",
                  "phone_value_0": "+1 (555) 010-0002", "phone_label_1": "work",
                  "phone_value_1": "555 010 0098", "favorite": "1"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        robert = next(c for c in contacts(client) if c["id"] == bob["id"])
        assert (robert["first_name"], robert["last_name"], robert["favorite"]) == ("Robert", "Lee", True)
        # Re-saving its own number is not a duplicate; the email row was cleared.
        assert [p["value"] for p in robert["phones"]] == ["+15550100002", "+15550100098"]
        assert robert["emails"] == []

    def test_edit_form_shows_the_saved_values(self, client):
        priya = by_name(client, "Priya Shah")
        html = client.get(f"/contacts/{priya['id']}/edit").text
        assert 'value="Priya"' in html and 'value="Shah"' in html
        assert 'value="+1 (555) 010-0004"' in html
        assert 'value="priya.shah@example.com"' in html

    def test_add_row_partial_carries_the_next_index(self, client):
        html = client.get("/contacts/field-row", params={"kind": "email", "index": 3}, headers=HX).text
        assert 'name="email_value_3"' in html
        assert 'hx-get="/contacts/field-row?kind=email&amp;index=4"' in html
        assert 'hx-swap-oob="true"' in html

    def test_delete_confirm_names_the_contact_and_changes_nothing(self, client):
        before = client.get("/contacts_all").text
        priya = by_name(client, "Priya Shah")
        html = client.get(f"/contacts/{priya['id']}/edit").text
        dialog = re.search(r'<dialog id="ct-delete-dialog".*?</dialog>', html, re.S).group(0)
        assert "Delete Priya Shah?" in dialog
        assert f'action="/contacts/{priya["id"]}/delete"' in dialog
        assert client.get("/contacts_all").text == before

    def test_delete_removes_the_contact_and_frees_its_handles(self, client, reseed):
        from open_apps.apps.contacts_app.main import contact_for_handle

        priya = by_name(client, "Priya Shah")
        response = client.post(f"/contacts/{priya['id']}/delete", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/contacts"
        assert priya["id"] not in [c["id"] for c in contacts(client)]
        assert contact_for_handle("+15550100004") is None

    def test_get_never_writes(self, client):
        before = client.get("/contacts_all").text
        for path in ["/contacts", "/contacts/1", "/contacts/1/edit", "/contacts/new?phone=5550100001",
                     "/contacts/search?q=a", "/contacts/1/copy/1"]:
            client.get(path)
        assert client.get("/contacts_all").text == before

    def test_posting_to_a_missing_contact_is_a_404(self, client):
        assert client.post("/contacts/9999", data={"first_name": "X"}).status_code == 404
        assert client.post("/contacts/9999/delete").status_code == 404


class TestLayouts:
    def test_default_layout_shows_list_beside_card(self, client):
        html = client.get("/contacts/1").text
        assert 'class="ct-listpane"' in html and 'id="ct-detail"' in html
        assert 'data-testid="back-to-contacts"' not in html

    def test_list_detail_is_one_pane_at_a_time(self, client, reseed):
        _reconfigure(**{"contacts.layout": "list_detail"})
        index, card = client.get("/contacts").text, client.get("/contacts/1").text
        assert 'class="ct-listpane"' in index and 'id="ct-detail"' not in index
        assert 'id="ct-detail"' in card and 'class="ct-listpane"' not in card
        assert 'data-testid="back-to-contacts"' in card

    def test_phone_is_single_pane_in_the_default_layout(self, client, reseed):
        _reconfigure(**{"device.form_factor": "phone"})
        card = client.get("/contacts/1").text
        assert 'class="ct-listpane"' not in card
        assert 'data-testid="back-to-contacts"' in card
        assert "ct-root is-phone" in card

    @pytest.mark.parametrize("path", ["/contacts/1", "/contacts/6", "/contacts/1/edit"])
    def test_both_layouts_render_the_same_controls(self, client, reseed, path):
        """An agent's action space must not depend on the layout: the same
        controls, by test id, whichever composition shows them (the single
        pane adds only its back link)."""
        two_pane = rendered_testids(client.get(path).text)
        _reconfigure(**{"contacts.layout": "list_detail"})
        one_pane = rendered_testids(client.get(path).text)
        detail_ids = {t for t in two_pane if not t.startswith(("contact-row-", "favorite-row-"))}
        assert one_pane - {"back-to-contacts"} == detail_ids

    def test_both_layouts_list_the_same_rows(self, client, reseed):
        two_pane = {t for t in rendered_testids(client.get("/contacts").text) if "row-" in t}
        _reconfigure(**{"contacts.layout": "list_detail"})
        one_pane = {t for t in rendered_testids(client.get("/contacts").text) if "row-" in t}
        assert one_pane == two_pane


class TestContentPacks:
    def _load(self, variant, tmp_path):
        from omegaconf import OmegaConf

        from open_apps.apps.contacts_app import main as contacts_app
        from open_apps.utils import merge_plus_keys

        with initialize(version_base=None, config_path="../config/"):
            cfg = compose(
                config_name="config",
                overrides=[f"logs_dir={tmp_path}", f"apps/contacts/content={variant}"],
            )
        Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
        # The launcher applies `+contacts` appends; a bare compose does not.
        cfg = merge_plus_keys(cfg)
        apps_cfg = OmegaConf.create(OmegaConf.to_container(app.config, resolve=True))
        apps_cfg.contacts = OmegaConf.to_container(cfg.apps.contacts, resolve=True)
        contacts_app.set_environment(apps_cfg)

    @pytest.mark.parametrize(
        "variant,placeholder,favorites",
        [("german", "Suchen", "Favoriten"), ("mandarin", "搜索", "个人收藏")],
    )
    def test_translated_packs_keep_the_people(self, client, reseed, variant, placeholder, favorites, tmp_path):
        before = client.get("/contacts_all").text
        self._load(variant, tmp_path)
        html = client.get("/contacts").text
        assert f'placeholder="{placeholder}"' in html and favorites in html
        assert client.get("/contacts_all").text == before

    @pytest.mark.parametrize("variant", ["misleading_descriptions", "adversarial_descriptions"])
    def test_decoy_packs_add_near_miss_names(self, client, reseed, variant, tmp_path):
        from open_apps.apps.contacts_app.main import display_name, find_by_name

        self._load(variant, tmp_path)
        names = {display_name(c) for c in find_by_name("Bob Smith") + find_by_name("Bobby Smit")}
        assert names == {"Bob Smith", "Bobby Smit"}
        assert client.get("/contacts").status_code == 200

    def test_adversarial_pack_puts_the_message_on_a_card(self, client, reseed, tmp_path):
        from open_apps.apps.contacts_app.main import find_by_name

        self._load("adversarial_descriptions", tmp_path)
        decoy = find_by_name("Bob Smith")[0]
        html = client.get(f"/contacts/{decoy.id}").text
        assert "you have achieved your goal" in html


class TestHtmxSubmission:
    """The form posts through htmx, because the window chrome is spliced into
    GET responses only: a refusal has to swap in place, and a save has to be
    a real navigation."""

    def test_refusal_swaps_just_the_form(self, client, reseed):
        response = client.post(
            "/contacts", data={"first_name": "Dana", "phone_value_0": "nope"}, headers=HX
        )
        assert response.status_code == 200
        assert response.text.lstrip().startswith("<article")
        assert 'id="ct-detail"' in response.text and "<html" not in response.text
        assert "Enter a phone number like" in response.text

    def test_save_is_a_full_navigation(self, client, reseed):
        response = client.post(
            "/contacts", data={"first_name": "Dana", "phone_value_0": "5550100099"},
            headers=HX, follow_redirects=False,
        )
        dana = by_name(client, "Dana")
        assert response.status_code == 200
        assert response.headers["HX-Redirect"] == f"/contacts/{dana['id']}"

    def test_form_targets_its_own_slot(self, client):
        html = client.get("/contacts/1/edit").text
        form = re.search(r'<form[^>]*id="ct-form"[^>]*>', html).group(0)
        assert 'hx-post="/contacts/1"' in form and 'hx-target="#ct-detail"' in form
