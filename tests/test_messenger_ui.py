"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Markup tests for the messenger UI across its layouts and themes.

Chromium cannot always launch where these run, so they assert on the served
HTML instead of pixels: the selectors and hrefs tasks rely on, the
presentation helpers (initials, run grouping, timestamp display), and that
the stylesheet stays on theme tokens.
"""

import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from fasthtml.common import to_xml
from starlette.testclient import TestClient

from open_apps.apps.contacts_app import main as contacts_app
from open_apps.apps.messenger_app import main as messenger

LAYOUTS = ["default", "split_inbox", "compact_list"]

ALICE, BOB, CHARLIE = "+15550100001", "+15550100002", "+15550100003"
GROUP = "group:fantastic4"
UNKNOWN = "+15550142290"


def build_client(tmp_path: Path, layout: str = "default", theme: str = "default", content: str = "default"):
    overrides = [
        f"logs_dir={tmp_path}",
        f"apps/messenger/layout={layout}",
        f"apps/theme={theme}",
        f"apps/messenger/content={content}",
    ]
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=overrides)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    # Names are drawn from Contacts, so its tables are seeded alongside.
    contacts_app.set_environment(config.apps)
    messenger.set_environment(config.apps)
    return TestClient(messenger.app)


def strip_assets(html: str) -> str:
    """The markup without <style>/<script> bodies, so asserts see only the DOM."""
    html = re.sub(r"<style\b.*?</style>", "", html, flags=re.S)
    return re.sub(r"<script\b.*?</script>", "", html, flags=re.S)


def find_tag(html: str, name: str, **attrs) -> str:
    """The first `<name ...>` opening tag carrying every given attribute
    value, whatever order FastHTML emits them in; "" if none does."""
    for tag in re.findall(rf"<{name}\b[^>]*>", html):
        if all(f'{k.rstrip("_").replace("_", "-")}="{v}"' in tag for k, v in attrs.items()):
            return tag
    return ""


@pytest.fixture(params=LAYOUTS)
def layout_client(request, tmp_path):
    return request.param, build_client(tmp_path, layout=request.param)


class TestHelpers:
    @pytest.mark.parametrize(
        "raw, shown",
        [
            ("Sep 17, 09:32AM", "Sep 17, 9:32 AM"),
            ("Apr 16, 9:00 AM", "Apr 16, 9:00 AM"),
            ("Apr 16, 9:03AM", "Apr 16, 9:03 AM"),
            ("Sep 30, 03:15 PM", "Sep 30, 3:15 PM"),
            ("yesterday", "yesterday"),
            ("", ""),
        ],
    )
    def test_display_time_normalises_known_formats(self, raw, shown):
        assert messenger.display_time(raw) == shown

    def test_short_date_drops_the_time(self):
        assert messenger.short_date("Jan 05, 9:46PM") == "Jan 5"
        assert messenger.short_date("not a date") == "not a date"

    @pytest.mark.parametrize(
        "name, expected",
        [("Alice", "A"), ("bob", "B"), ("Anna Lena", "AL"), ("jean-luc picard", "JL"), ("", "?")],
    )
    def test_initials(self, name, expected):
        assert messenger.initials(name) == expected

    def test_avatar_tone_is_stable_and_in_range(self):
        # crc32, not hash(): must not change between processes.
        assert messenger.avatar_tone("Alice") == messenger.avatar_tone("Alice")
        tones = {messenger.avatar_tone(n) for n in ["Alice", "Bob", "Charlie"]}
        assert tones <= set(range(messenger.AVATAR_TONES))
        assert len(tones) == 3

    def test_message_runs(self):
        senders = ["Alice", "you", "Bob", "Charlie", "Charlie", "Charlie", "you"]
        assert messenger.message_runs(senders) == [
            (True, True),
            (True, True),
            (True, True),
            (True, False),
            (False, False),
            (False, True),
            (True, True),
        ]
        assert messenger.message_runs([]) == []

    def test_group_members_skip_you_and_keep_order(self):
        assert messenger.group_members(["Alice", "you", "Bob", "Alice", "Charlie"]) == [
            "Alice",
            "Bob",
            "Charlie",
        ]


class TestListPage:
    def test_rows_keep_their_hrefs_and_selector(self, layout_client):
        layout, client = layout_client
        html = strip_assets(client.get("/messages").text)
        main = html[html.index("<main"):]
        for key in [ALICE, BOB, CHARLIE, GROUP, UNKNOWN]:
            assert f'href="/messages/{key}"' in main
        assert len(re.findall(r'class="msg-row(?: is-selected)?"', html)) == 5
        assert f"layout-{layout}" in re.search(r"<body[^>]*>", html).group(0)

    def test_initials_avatars_replace_icon_images(self, layout_client):
        _, client = layout_client
        html = strip_assets(client.get("/messages").text)
        assert "<img" not in html.split("<main", 1)[1]
        assert find_tag(html, "div", class_="msg-avatar msg-avatar-list is-single", aria_hidden="true")
        assert re.search(r'msg-avatar-disc avatar-tone-\d">A</span>', html)
        # The group chat gets two members' discs.
        assert "msg-avatar-list is-group" in html

    def test_return_link_keeps_text_href_role(self, layout_client):
        _, client = layout_client
        html = strip_assets(client.get("/messages").text)
        link = re.search(r'<a [^>]*class="msg-apps-link[^"]*"[^>]*>.*?</a>', html, flags=re.S).group(0)
        assert 'href="/"' in link and 'role="button"' in link
        assert re.sub(r"<[^>]+>", "", link).strip() == "Return to List of Apps"
        # Still below the list (UI question: "Below the conversation list").
        assert html.index("msg-apps-link") > html.rindex("msg-row-preview")

    def test_return_button_is_prominent_only_in_default(self, layout_client):
        # Navigation tasks rely on screenshot agents spotting it in default.
        layout, client = layout_client
        html = strip_assets(client.get("/messages").text)
        link = find_tag(html, "a", href="/", role="button")
        assert "msg-apps-link" in link
        assert ("is-prominent" in link) == (layout == "default")

    def test_list_time_is_short_and_preview_is_capped(self, tmp_path):
        client = build_client(tmp_path)
        html = strip_assets(client.get("/messages").text)
        assert '<span class="msg-row-time">Apr 16</span>' in html
        assert "Alice: Absolutely! Together we can…" in html

    def test_default_list_has_no_chats_heading(self, tmp_path):
        # UI question: the page title is "OpenMessages", with "Chats" as a distractor.
        html = strip_assets(build_client(tmp_path).get("/messages").text)
        assert ">Chats<" not in html

    def test_split_inbox_list_page_is_two_panes(self, tmp_path):
        html = strip_assets(build_client(tmp_path, layout="split_inbox").get("/messages").text)
        main = html[html.index("<main"):]
        assert 'class="messenger-split"' in main
        assert find_tag(main, "nav", class_="msg-list-pane", aria_label="Conversations")
        assert "Select a conversation to start messaging" in main
        assert 'aria-current="page"' not in html

    def test_compact_rows_read_name_preview_time(self, tmp_path):
        html = strip_assets(build_client(tmp_path, layout="compact_list").get("/messages").text)
        row = re.search(r'<a href="/messages/\+15550100001".*?</a>', html, flags=re.S).group(0)
        assert row.index("msg-row-name") < row.index("msg-row-preview") < row.index("msg-row-time")


class TestThread:
    def test_selector_and_form_fields_unchanged(self, layout_client):
        _, client = layout_client
        html = strip_assets(client.get(f"/messages/{ALICE}/").text)
        assert 'id="chatlist"' in html
        assert 'id="chat-container"' in html
        assert 'id="search-toggle"' in html
        assert 'name="msg"' in html and 'id="msg-input"' in html
        assert 'name="interlocutor"' in html
        assert 'hx-post="/messages/send"' in html

    def test_every_message_keeps_sender_and_time_in_dom(self, layout_client):
        _, client = layout_client
        html = strip_assets(client.get(f"/messages/{GROUP}/").text)
        assert html.count('<div class="chat-header">') == 5
        assert html.count('<div class="chat-footer">') == 5
        assert "Mar 1, 1:00 PM" in html

    def test_group_run_classes(self, tmp_path):
        # The group chat: Alice, you, Bob, Charlie, Alice -> all single
        # runs; append two more from "you" to get a run of three.
        client = build_client(tmp_path)
        html = strip_assets(client.get(f"/messages/{GROUP}/").text)
        classes = re.findall(r'<div class="(chat chat-(?:start|end)[^"]*)"', html)
        assert classes[0] == "chat chat-start msg-first msg-last"
        assert classes[1] == "chat chat-end msg-first msg-last"

        messenger.add_new_message_to_history(BOB, "one", BOB, "Jan 05, 9:50PM")
        messenger.add_new_message_to_history(BOB, "two", BOB, "Jan 05, 9:51PM")
        html = strip_assets(client.get(f"/messages/{BOB}/").text)
        classes = re.findall(r'<div class="(chat chat-(?:start|end)[^"]*)"', html)
        assert classes[-3:] == [
            "chat chat-start msg-first",
            "chat chat-start",
            "chat chat-start msg-last",
        ]

    def test_thread_header_has_avatar_name_and_group_members(self, tmp_path):
        client = build_client(tmp_path)
        html = strip_assets(client.get(f"/messages/{GROUP}/").text)
        assert "msg-avatar-header is-group" in html
        assert "<h1>Fantastic4GroupChat</h1>" in html
        assert '<p class="msg-thread-sub">Alice, Bob, Charlie</p>' in html
        assert 'class="msg-thread is-group"' in html
        html = strip_assets(client.get(f"/messages/{ALICE}/").text)
        assert 'class="msg-thread is-direct"' in html

    def test_incoming_messages_carry_an_avatar_outgoing_do_not(self, tmp_path):
        html = strip_assets(build_client(tmp_path).get(f"/messages/{ALICE}/").text)
        chats = re.findall(r'<div class="chat chat-(start|end)[^"]*">(.*?)<input', html, flags=re.S)
        assert chats
        for side, body in chats:
            assert ("chat-image" in body) == (side == "start")

    def test_composer_send_button_is_themed_and_labelled(self, tmp_path):
        html = strip_assets(build_client(tmp_path).get(f"/messages/{ALICE}/").text)
        assert find_tag(html, "button", type="submit", aria_label="Send", class_="msg-send")
        assert "btn-primary" not in html

    def test_compact_puts_time_on_the_sender_line(self, tmp_path):
        html = strip_assets(build_client(tmp_path, layout="compact_list").get(f"/messages/{ALICE}/").text)
        first = re.search(r'<div class="chat chat-start.*?<input', html, flags=re.S).group(0)
        assert first.index("chat-header") < first.index("chat-footer") < first.index("chat-bubble")

    def test_split_inbox_marks_only_the_open_chat(self, tmp_path):
        html = strip_assets(build_client(tmp_path, layout="split_inbox").get(f"/messages/{BOB}/").text)
        assert html.count('aria-current="page"') == 1
        current = re.search(r'<a [^>]*aria-current="page"[^>]*>', html).group(0)
        assert f'href="/messages/{BOB}"' in current
        assert find_tag(html, "nav", class_="msg-list-pane", aria_label="Conversations")

    def test_send_continues_a_run_of_your_own(self, tmp_path):
        client = build_client(tmp_path)
        messenger.add_new_message_to_history(CHARLIE, "earlier", "you", "Feb 25, 4:50PM")
        raw = client.post("/messages/send", data={"msg": "hi", "interlocutor": CHARLIE}).text
        html = strip_assets(raw)
        classes = re.findall(r'<div class="(chat chat-(?:start|end)[^"]*)"', html)
        assert classes == ["chat chat-end msg-last", "chat chat-start msg-first msg-last"]
        # The bubble that ended the run is on the page already; the response
        # carries the script that drops its `msg-last`.
        script = re.search(r'<script class="msg-close-run">(.*?)</script>', raw, flags=re.S)
        assert script and "classList.remove('msg-last')" in script.group(1)
        state = client.get("/messages_all").json()
        charlie = next(c for c in state if c["handle"] == CHARLIE)
        assert [m[1] for m in charlie["messages"][-2:]] == ["you", CHARLIE]


    def test_send_after_a_reply_needs_no_run_fix(self, tmp_path):
        client = build_client(tmp_path)
        html = client.post("/messages/send", data={"msg": "hi", "interlocutor": ALICE}).text
        assert "msg-close-run" not in html
        classes = re.findall(r'<div class="(chat chat-(?:start|end)[^"]*)"', html)
        assert classes[0] == "chat chat-end msg-first msg-last"


def add_contact(first_name, *handles, last_name=""):
    """Save a contact straight into the Contacts tables (this branch has no
    create route yet) and return it."""
    person = contacts_app.contacts.insert(
        contacts_app.Contact(
            id=None, first_name=first_name, last_name=last_name, company="", note="", favorite=False
        )
    )
    for value in handles:
        kind = "email" if "@" in value else "phone"
        contacts_app.handles.insert(
            contacts_app.ContactHandle(id=None, contact_id=person.id, kind=kind, label="mobile", value=value)
        )
    return person


def rename_contact(handle, first_name):
    contact = contacts_app.contact_for_handle(handle)
    contact.first_name = first_name
    contacts_app.contacts.update(contact)


def row_names(html):
    return [n.strip() for n in re.findall(r'class="msg-row-name">(.*?)</', html, flags=re.S)]


def sender_labels(html):
    """Each message's sender label as text, links unwrapped."""
    return [
        re.sub(r"<[^>]+>", "", m).strip()
        for m in re.findall(r'<div class="chat-header">(.*?)</div>', html, flags=re.S)
    ]


def header_title(html):
    return re.sub(r"<[^>]+>", "", re.search(r"<h1>.*?</h1>", html, flags=re.S).group(0)).strip()


class TestNamesFromContacts:
    """The messenger stores handles; every name on screen is Contacts'."""

    def test_list_rows_are_named_by_contacts(self, layout_client):
        _, client = layout_client
        html = strip_assets(client.get("/messages").text)
        names = row_names(html)
        assert {"Alice", "Bob", "Charlie", "Fantastic4GroupChat", "+1 (555) 014-2290"} <= set(names)
        # No raw handle leaks into a row label for a saved contact.
        assert ALICE not in names

    def test_thread_header_links_a_saved_contact_to_their_card(self, tmp_path):
        client = build_client(tmp_path)
        alice = contacts_app.contact_for_handle(ALICE)
        html = strip_assets(client.get(f"/messages/{ALICE}/").text)
        link = find_tag(html, "a", href=f"/contacts/{alice.id}", data_testid="thread-contact-link")
        assert link and "msg-thread-contact" in link
        assert header_title(html) == "Alice"
        assert "unknown-sender-banner" not in html

    def test_renaming_a_contact_renames_their_thread(self, tmp_path):
        client = build_client(tmp_path)
        rename_contact(BOB, "Robert")
        html = strip_assets(client.get(f"/messages/{BOB}/").text)
        assert header_title(html) == "Robert"
        assert "Robert" in row_names(strip_assets(client.get("/messages").text))

    def test_group_names_its_members_and_senders(self, tmp_path):
        client = build_client(tmp_path)
        html = strip_assets(client.get(f"/messages/{GROUP}/").text)
        assert "<h1>Fantastic4GroupChat</h1>" in html
        assert '<p class="msg-thread-sub">Alice, Bob, Charlie</p>' in html
        assert {"Alice", "you", "Bob", "Charlie"} == set(sender_labels(html))
        rename_contact(BOB, "Robert")
        html = strip_assets(client.get(f"/messages/{GROUP}/").text)
        assert '<p class="msg-thread-sub">Alice, Robert, Charlie</p>' in html
        assert "Robert" in sender_labels(html)

    def test_group_reply_comes_from_a_member_handle(self, tmp_path):
        client = build_client(tmp_path)
        client.post("/messages/send", data={"msg": "hi all", "interlocutor": GROUP})
        group = next(c for c in client.get("/messages_all").json() if c["handle"] == GROUP)
        assert [m[1] for m in group["messages"][-2:]] == ["you", ALICE]

    def test_fixed_replies_are_keyed_by_handle(self, tmp_path):
        client = build_client(tmp_path)
        html = client.post("/messages/send", data={"msg": "Saturday?", "interlocutor": BOB}).text
        assert "Yes, let&#x27;s play on Saturday!" in html or "Yes, let's play on Saturday!" in html


class TestContactLinks:
    """Every saved person's name on a thread leads to their contact card."""

    def test_group_sender_labels_link_to_contacts(self, tmp_path):
        client = build_client(tmp_path)
        bob = contacts_app.contact_for_handle(BOB)
        html = strip_assets(client.get(f"/messages/{GROUP}/").text)
        link = find_tag(html, "a", href=f"/contacts/{bob.id}", data_testid="sender-contact-link")
        assert link and 'aria-label="View contact: Bob"' in link
        assert "msg-sender-link" in link
        # Your own messages are not links.
        assert not re.search(r'<div class="chat-header">\s*<a[^>]*>\s*you', html)

    def test_unknown_group_sender_stays_plain_text(self, tmp_path):
        client = build_client(tmp_path)
        messenger.add_new_message_to_history(GROUP, "hi, new here", UNKNOWN, "Mar 1, 1:30PM")
        html = strip_assets(client.get(f"/messages/{GROUP}/").text)
        assert '<div class="chat-header">+1 (555) 014-2290</div>' in html

    def test_hidden_labels_are_not_links(self, tmp_path):
        # Direct threads hide the sender line; a hidden link would still be a
        # Tab stop, so none is rendered there.
        html = strip_assets(build_client(tmp_path).get(f"/messages/{ALICE}/").text)
        assert "sender-contact-link" not in html

    def test_header_link_only_for_a_known_contact(self, tmp_path):
        client = build_client(tmp_path)
        alice = contacts_app.contact_for_handle(ALICE)
        html = strip_assets(client.get(f"/messages/{ALICE}/").text)
        link = find_tag(html, "a", href=f"/contacts/{alice.id}", data_testid="thread-contact-link")
        assert link and 'aria-label="View contact: Alice"' in link
        html = strip_assets(client.get(f"/messages/{UNKNOWN}/").text)
        assert "thread-contact-link" not in html
        assert "<h1>+1 (555) 014-2290</h1>" in html

    def test_list_rows_hold_no_nested_links(self, layout_client):
        _, client = layout_client
        html = strip_assets(client.get("/messages").text)
        for row in re.findall(r'<a href="/messages/[^"]*".*?</a>', html, flags=re.S):
            assert "<a " not in row[3:]
            assert "/contacts/" not in row


class TestUnknownSender:
    def test_unknown_number_shows_formatted_with_add_banner(self, layout_client):
        _, client = layout_client
        html = strip_assets(client.get(f"/messages/{UNKNOWN}/").text)
        assert header_title(html) == "+1 (555) 014-2290"
        assert find_tag(html, "div", class_="msg-unknown-banner", role="note")
        assert "Not in your contacts" in html
        link = find_tag(html, "a", data_testid="add-to-contacts")
        # `+` must be escaped, or the query string would read it as a space.
        assert 'href="/contacts/new?phone=%2B15550142290"' in link
        # A stranger gets a person glyph, not "+(" initials.
        assert "msg-avatar-glyph" in html

    def test_email_handle_prefills_the_email_field(self, tmp_path):
        client = build_client(tmp_path)
        messenger.message_history_db.insert(
            messenger.Thread(handle="sam@example.com", messages=["hey"], senders=["sam@example.com"], timestamps=["Apr 19, 9:00AM"])
        )
        html = strip_assets(client.get("/messages/sam@example.com/").text)
        assert 'href="/contacts/new?email=sam%40example.com"' in html

    def test_saving_the_number_names_the_thread(self, tmp_path):
        client = build_client(tmp_path)
        before = client.get("/messages_all").text
        jordan = add_contact("Jordan", UNKNOWN)
        html = strip_assets(client.get(f"/messages/{UNKNOWN}/").text)
        assert header_title(html) == "Jordan"
        assert find_tag(html, "a", href=f"/contacts/{jordan.id}")
        assert "unknown-sender-banner" not in html
        assert "Jordan" in row_names(strip_assets(client.get("/messages").text))
        # ...and the scoring surface did not move.
        assert client.get("/messages_all").text == before

    def test_groups_never_get_the_banner(self, tmp_path):
        html = strip_assets(build_client(tmp_path).get(f"/messages/{GROUP}/").text)
        assert "unknown-sender-banner" not in html


class TestScoringSurface:
    def test_payload_is_handles_only(self, tmp_path):
        state = build_client(tmp_path).get("/messages_all").json()
        assert [set(c) for c in state] == [{"handle", "messages"}] * len(state)
        assert {c["handle"] for c in state} == {ALICE, BOB, CHARLIE, GROUP, UNKNOWN}
        senders = {m[1] for c in state for m in c["messages"]}
        assert senders == {"you", ALICE, BOB, CHARLIE, UNKNOWN}
        assert all(len(m) == 3 for c in state for m in c["messages"])

    def test_renaming_or_deleting_a_contact_leaves_it_byte_identical(self, tmp_path):
        client = build_client(tmp_path)
        before = client.get("/messages_all").text
        rename_contact(ALICE, "Alicia")
        assert client.get("/messages_all").text == before
        charlie = contacts_app.contact_for_handle(CHARLIE)
        contacts_app.handles.delete_where("contact_id = ?", [charlie.id])
        contacts_app.contacts.delete(charlie.id)
        assert client.get("/messages_all").text == before
        # The deleted contact's thread falls back to the number.
        assert header_title(strip_assets(client.get(f"/messages/{CHARLIE}/").text)) == "+1 (555) 010-0003"


class TestLegacyUrls:
    @pytest.mark.parametrize(
        "old, new",
        [
            ("Alice", ALICE),
            ("alice", ALICE),
            ("Fantastic4GroupChat", GROUP),
            ("(555) 010-0002", BOB),
            ("5550100003", CHARLIE),
        ],
    )
    def test_unambiguous_names_and_spellings_redirect(self, tmp_path, old, new):
        client = build_client(tmp_path)
        response = client.get(f"/messages/{old}/", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == f"/messages/{new}/"
        assert client.get(f"/messages/{old}/").status_code == 200

    def test_ambiguous_or_unknown_names_are_404(self, tmp_path):
        client = build_client(tmp_path)
        assert client.get("/messages/Nobody/", follow_redirects=False).status_code == 404
        # A contact with no thread is not a conversation to redirect to.
        assert client.get("/messages/Priya Shah/", follow_redirects=False).status_code == 404
        add_contact("Alice", "+15550109999", last_name="Other")
        assert client.get("/messages/Alice/", follow_redirects=False).status_code == 404

    def test_encoded_handle_resolves(self, tmp_path):
        client = build_client(tmp_path)
        assert client.get("/messages/%2B15550100001/").status_code == 200
        assert client.get("/messages/group%3Afantastic4/").status_code == 200

    def test_send_to_an_unknown_thread_is_404(self, tmp_path):
        client = build_client(tmp_path)
        assert client.post("/messages/send", data={"msg": "x", "interlocutor": "Alice"}).status_code == 404


class TestLongDescriptions:
    def test_long_pack_threads_keep_their_names(self, tmp_path):
        from omegaconf import OmegaConf

        from open_apps.utils import merge_plus_keys

        overrides = [
            f"logs_dir={tmp_path}",
            "apps/messenger/content=long_descriptions",
            "apps/contacts/content=long_descriptions",
        ]
        with initialize(version_base=None, config_path="../config/"):
            config = compose(config_name="config", overrides=overrides)
        # The launcher's merge of `+threads` into `threads`.
        OmegaConf.resolve(config)
        config = merge_plus_keys(config)
        Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
        contacts_app.set_environment(config.apps)
        messenger.set_environment(config.apps)
        client = TestClient(messenger.app)

        html = strip_assets(client.get("/messages").text)
        names = set(row_names(html))
        assert {"Alice", "Sieglinde", "Mom", "Dad", "Priya", "Self", "Fantastic4GroupChat"} <= names
        assert not any(n.startswith("+1 (555) 010-01") for n in names)
        assert header_title(strip_assets(client.get("/messages/+15550100114/").text)) == "Priya"


class TestStyles:
    def test_no_hex_colours_in_messenger_styles(self):
        css = to_xml(messenger._LAYOUT_STYLES) + to_xml(messenger._COMPONENT_STYLES)
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
        assert "rgba(" not in css and "rgb(" not in css

    @pytest.mark.parametrize("theme", ["dark", "solarized", "mono", "challenging_font"])
    def test_renders_in_every_theme(self, tmp_path, theme):
        for layout in LAYOUTS:
            client = build_client(tmp_path / layout, layout=layout, theme=theme)
            for route in ("/messages", f"/messages/{ALICE}/"):
                response = client.get(route)
                assert response.status_code == 200
                assert "--color-primary" in response.text
            thread = strip_assets(client.get(f"/messages/{ALICE}/").text)
            assert "#570df8" not in client.get(f"/messages/{ALICE}/").text
            assert 'id="chatlist"' in thread
