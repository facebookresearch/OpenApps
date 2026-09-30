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

from open_apps.apps.messenger_app import main as messenger

LAYOUTS = ["default", "split_inbox", "compact_list"]


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
        for user in ["Alice", "Bob", "Charlie", "Fantastic4GroupChat"]:
            assert f'href="/messages/{user}"' in main
        assert len(re.findall(r'class="msg-row(?: is-selected)?"', html)) == 4
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
        link = re.search(r'<a [^>]*class="msg-apps-link"[^>]*>.*?</a>', html, flags=re.S).group(0)
        assert 'href="/"' in link and 'role="button"' in link
        assert re.sub(r"<[^>]+>", "", link).strip() == "Return to List of Apps"
        # Still below the list (UI question: "Below the conversation list").
        assert html.index("msg-apps-link") > html.rindex("msg-row-preview")

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
        row = re.search(r'<a href="/messages/Alice".*?</a>', html, flags=re.S).group(0)
        assert row.index("msg-row-name") < row.index("msg-row-preview") < row.index("msg-row-time")


class TestThread:
    def test_selector_and_form_fields_unchanged(self, layout_client):
        _, client = layout_client
        html = strip_assets(client.get("/messages/Alice/").text)
        assert 'id="chatlist"' in html
        assert 'id="chat-container"' in html
        assert 'id="search-toggle"' in html
        assert 'name="msg"' in html and 'id="msg-input"' in html
        assert 'name="interlocutor"' in html
        assert 'hx-post="/messages/send"' in html

    def test_every_message_keeps_sender_and_time_in_dom(self, layout_client):
        _, client = layout_client
        html = strip_assets(client.get("/messages/Fantastic4GroupChat/").text)
        assert html.count('<div class="chat-header">') == 5
        assert html.count('<div class="chat-footer">') == 5
        assert "Mar 1, 1:00 PM" in html

    def test_group_run_classes(self, tmp_path):
        # Fantastic4GroupChat: Alice, you, Bob, Charlie, Alice -> all single
        # runs; append two more from "you" to get a run of three.
        client = build_client(tmp_path)
        html = strip_assets(client.get("/messages/Fantastic4GroupChat/").text)
        classes = re.findall(r'<div class="(chat chat-(?:start|end)[^"]*)"', html)
        assert classes[0] == "chat chat-start msg-first msg-last"
        assert classes[1] == "chat chat-end msg-first msg-last"

        messenger.add_new_message_to_history("Bob", "one", "Bob", "Jan 05, 9:50PM")
        messenger.add_new_message_to_history("Bob", "two", "Bob", "Jan 05, 9:51PM")
        html = strip_assets(client.get("/messages/Bob/").text)
        classes = re.findall(r'<div class="(chat chat-(?:start|end)[^"]*)"', html)
        assert classes[-3:] == [
            "chat chat-start msg-first",
            "chat chat-start",
            "chat chat-start msg-last",
        ]

    def test_thread_header_has_avatar_name_and_group_members(self, tmp_path):
        client = build_client(tmp_path)
        html = strip_assets(client.get("/messages/Fantastic4GroupChat/").text)
        assert "msg-avatar-header is-group" in html
        assert "<h1>Fantastic4GroupChat</h1>" in html
        assert '<p class="msg-thread-sub">Alice, Bob, Charlie</p>' in html
        assert 'class="msg-thread is-group"' in html
        html = strip_assets(client.get("/messages/Alice/").text)
        assert 'class="msg-thread is-direct"' in html

    def test_incoming_messages_carry_an_avatar_outgoing_do_not(self, tmp_path):
        html = strip_assets(build_client(tmp_path).get("/messages/Alice/").text)
        chats = re.findall(r'<div class="chat chat-(start|end)[^"]*">(.*?)<input', html, flags=re.S)
        assert chats
        for side, body in chats:
            assert ("chat-image" in body) == (side == "start")

    def test_composer_send_button_is_themed_and_labelled(self, tmp_path):
        html = strip_assets(build_client(tmp_path).get("/messages/Alice/").text)
        assert find_tag(html, "button", type="submit", aria_label="Send", class_="msg-send")
        assert "btn-primary" not in html

    def test_compact_puts_time_on_the_sender_line(self, tmp_path):
        html = strip_assets(build_client(tmp_path, layout="compact_list").get("/messages/Alice/").text)
        first = re.search(r'<div class="chat chat-start.*?<input', html, flags=re.S).group(0)
        assert first.index("chat-header") < first.index("chat-footer") < first.index("chat-bubble")

    def test_split_inbox_marks_only_the_open_chat(self, tmp_path):
        html = strip_assets(build_client(tmp_path, layout="split_inbox").get("/messages/Bob/").text)
        assert html.count('aria-current="page"') == 1
        current = re.search(r'<a [^>]*aria-current="page"[^>]*>', html).group(0)
        assert 'href="/messages/Bob"' in current
        assert find_tag(html, "nav", class_="msg-list-pane", aria_label="Conversations")

    def test_send_continues_a_run_of_your_own(self, tmp_path):
        client = build_client(tmp_path)
        messenger.add_new_message_to_history("Charlie", "earlier", "you", "Feb 25, 4:50PM")
        raw = client.post("/messages/send", data={"msg": "hi", "interlocutor": "Charlie"}).text
        html = strip_assets(raw)
        classes = re.findall(r'<div class="(chat chat-(?:start|end)[^"]*)"', html)
        assert classes == ["chat chat-end msg-last", "chat chat-start msg-first msg-last"]
        # The bubble that ended the run is on the page already; the response
        # carries the script that drops its `msg-last`.
        script = re.search(r'<script class="msg-close-run">(.*?)</script>', raw, flags=re.S)
        assert script and "classList.remove('msg-last')" in script.group(1)
        state = client.get("/messages_all").json()
        charlie = next(c for c in state if c["user"] == "Charlie")
        assert [m[1] for m in charlie["messages"][-2:]] == ["you", "Charlie"]


    def test_send_after_a_reply_needs_no_run_fix(self, tmp_path):
        client = build_client(tmp_path)
        html = client.post("/messages/send", data={"msg": "hi", "interlocutor": "Alice"}).text
        assert "msg-close-run" not in html
        classes = re.findall(r'<div class="(chat chat-(?:start|end)[^"]*)"', html)
        assert classes[0] == "chat chat-end msg-first msg-last"


class TestStyles:
    def test_no_hex_colours_in_messenger_styles(self):
        css = to_xml(messenger._LAYOUT_STYLES) + to_xml(messenger._COMPONENT_STYLES)
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
        assert "rgba(" not in css and "rgb(" not in css

    @pytest.mark.parametrize("theme", ["dark", "solarized", "mono", "challenging_font"])
    def test_renders_in_every_theme(self, tmp_path, theme):
        for layout in LAYOUTS:
            client = build_client(tmp_path / layout, layout=layout, theme=theme)
            for route in ("/messages", "/messages/Alice/"):
                response = client.get(route)
                assert response.status_code == 200
                assert "--color-primary" in response.text
            thread = strip_assets(client.get("/messages/Alice/").text)
            assert "#570df8" not in client.get("/messages/Alice/").text
            assert 'id="chatlist"' in thread
