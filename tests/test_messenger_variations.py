"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The messenger under every variation axis: dark and challenging_font themes
(root background, daisyUI and Pico bridged onto tokens), German and Mandarin
UI strings with `<html lang>`, and every layout in every language. Markup only,
through the app's own TestClient, as in test_messenger_ui.py.
"""

import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.contacts_app import main as contacts_app
from open_apps.apps.messenger_app import main as messenger

LAYOUTS = ["default", "split_inbox", "compact_list"]
ALICE, BOB, GROUP = "+15550100001", "+15550100002", "group:fantastic4"

PACKS = {
    "german": {
        "lang": "de",
        "return": "Zurück zur App-Übersicht",
        "type": "Nachricht schreiben",
        "search": "Nachrichten durchsuchen …",
        "send": "Senden",
        "chats": "Chats",
        "you_prefix": "Du: ",
    },
    "mandarin": {
        "lang": "zh-Hans",
        "return": "返回应用列表",
        "type": "输入消息",
        "search": "搜索消息…",
        "send": "发送",
        "chats": "聊天",
        "you_prefix": "我：",
    },
}


def build_client(tmp_path, layout="default", theme="default", content="default"):
    overrides = [
        f"logs_dir={tmp_path}",
        f"apps/messenger/layout={layout}",
        f"apps/theme={theme}",
        f"apps/messenger/content={content}",
    ]
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=overrides)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    contacts_app.set_environment(config.apps)
    messenger.set_environment(config.apps)
    return TestClient(messenger.app)


def soup_of(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["style", "script"]):
        tag.decompose()
    return soup


def html_tag(raw):
    return re.search(r"<html[^>]*>", raw).group(0)


# ---- Themes -------------------------------------------------------------


@pytest.mark.parametrize("theme,tone", [("dark", "dark"), ("challenging_font", "light")])
def test_theme_paints_the_root_and_bridges_daisyui_and_pico(tmp_path, theme, tone):
    raw = build_client(tmp_path, theme=theme).get("/messages").text
    # The root element is painted from the token, out-ranking Pico's
    # `:where(:root)` and daisyUI's `:root, [data-theme]`.
    assert re.search(r"html:root \{\s*background-color: var\(--color-bg\);", raw)
    # daisyUI's base colours resolve through their fallbacks to the tokens.
    for var in ("--fallback-b1: var(--color-bg)", "--fallback-bc: var(--color-fg)",
                "--fallback-p: var(--color-primary)"):
        assert var in raw
    # Pico's too.
    assert "--pico-h1-color: var(--color-fg)" in raw
    assert f'data-theme="{tone}"' in html_tag(raw)


def test_dark_tokens_and_challenging_font_reach_the_page(tmp_path):
    assert "--color-bg: #121212;" in build_client(tmp_path / "d", theme="dark").get("/messages").text
    assert "Brush Script MT" in build_client(tmp_path / "f", theme="challenging_font").get("/messages").text


# ---- English stays the reference rendering --------------------------------


def test_english_default_is_unchanged(tmp_path):
    client = build_client(tmp_path)
    raw = client.get("/messages").text
    assert 'lang="en"' in html_tag(raw)
    soup = soup_of(raw)
    assert soup.select_one("a.msg-apps-link").get_text(strip=True) == "Return to List of Apps"
    assert "Sep 17" not in raw  # list shows the last message's day only
    thread = soup_of(client.get(f"/messages/{ALICE}/").text)
    assert thread.select_one("#msg-input")["placeholder"] == "Type a message"
    assert thread.select_one("#search-input")["placeholder"] == "Search messages..."
    assert thread.select_one("button.msg-send")["aria-label"] == "Send"
    assert "Sep 17, 9:32 AM" in [d.get_text(strip=True) for d in thread.select(".chat-footer")]


# ---- German and Mandarin --------------------------------------------------


@pytest.mark.parametrize("content", PACKS)
def test_list_and_thread_are_localized(tmp_path, content):
    want = PACKS[content]
    client = build_client(tmp_path, content=content)
    raw = client.get("/messages").text
    assert f'lang="{want["lang"]}"' in html_tag(raw)
    soup = soup_of(raw)
    assert soup.select_one("a.msg-apps-link").get_text(strip=True) == want["return"]
    assert soup.select_one("a.msg-apps-link")["href"] == "/"

    raw = client.get(f"/messages/{ALICE}/").text
    thread = soup_of(raw)
    assert thread.select_one("#msg-input")["placeholder"] == want["type"]
    assert thread.select_one("#search-input")["placeholder"] == want["search"]
    assert thread.select_one("button.msg-send")["aria-label"] == want["send"]
    assert thread.select_one("#search-toggle")["aria-label"] != "Search"
    assert thread.select_one("a.thread-back")["aria-label"] != "Back"
    # Timestamps are displayed in the pack's format ...
    footers = [d.get_text(strip=True) for d in thread.select(".chat-footer")]
    assert "Sep 17, 9:32 AM" not in footers
    # ... while the stored state the rewards read is untouched.
    alice = next(c for c in client.get("/messages_all").json() if c["handle"] == ALICE)
    assert alice["messages"][1][2] == "Sep 17, 09:32AM"
    # The search script gets its strings from the page.
    assert "window.msgI18n" in raw


def test_timestamp_formats(tmp_path):
    client = build_client(tmp_path / "de", content="german")
    footers = [d.get_text(strip=True) for d in soup_of(client.get(f"/messages/{ALICE}/").text).select(".chat-footer")]
    assert "17. Sept., 09:32" in footers and "16. Sept., 22:00" in footers
    client = build_client(tmp_path / "zh", content="mandarin")
    footers = [d.get_text(strip=True) for d in soup_of(client.get(f"/messages/{ALICE}/").text).select(".chat-footer")]
    assert "9月17日 上午9:32" in footers and "9月16日 下午10:00" in footers


@pytest.mark.parametrize("content", PACKS)
def test_preview_of_your_own_message_is_localized(tmp_path, content):
    client = build_client(tmp_path, content=content)
    # Seeded chats all end on the other party; make the group's last ours.
    messenger.add_new_message_to_history(GROUP, "ok", "you", "Mar 01, 1:30PM")
    previews = [p.get_text(strip=True) for p in soup_of(client.get("/messages").text).select(".msg-row-preview")]
    assert any(p.startswith(PACKS[content]["you_prefix"]) for p in previews)
    assert not any(p.startswith("You: ") for p in previews)


@pytest.mark.parametrize("content", PACKS)
def test_auto_reply_speaks_the_pack_language(tmp_path, content):
    client = build_client(tmp_path, content=content)
    client.post("/messages/send", data={"msg": "Samstag?", "interlocutor": BOB})
    bob = next(c for c in client.get("/messages_all").json() if c["handle"] == BOB)
    reply = bob["messages"][-1][0]
    assert reply != "Yes, let's play on Saturday!"
    assert reply == {"german": "Ja, lass uns am Samstag spielen!", "mandarin": "好啊，我们周六一起打吧！"}[content]


def test_mistranslations_are_fixed(tmp_path):
    de = build_client(tmp_path / "de", content="german").get("/messages_all").json()
    alice = next(c for c in de if c["handle"] == ALICE)
    assert "nicht gelabelter Daten" in alice["messages"][3][0]
    zh = build_client(tmp_path / "zh", content="mandarin").get("/messages_all").json()
    alice = next(c for c in zh if c["handle"] == ALICE)
    assert "未标记数据" in alice["messages"][3][0]


# ---- Every layout in every language ---------------------------------------


@pytest.mark.parametrize("content", PACKS)
@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_layout_renders_in_every_language(tmp_path, layout, content):
    want = PACKS[content]
    client = build_client(tmp_path, layout=layout, theme="dark", content=content)
    for path, selector in (("/messages", "a.msg-link"), (f"/messages/{GROUP}/", "#chatlist .chat")):
        response = client.get(path)
        assert response.status_code == 200, path
        assert f'lang="{want["lang"]}"' in html_tag(response.text)
        soup = soup_of(response.text)
        assert "Return to List of Apps" not in soup.get_text(" ")
        assert soup.select(selector), path
    if layout == "split_inbox":
        soup = soup_of(client.get("/messages").text)
        assert soup.select_one(".msg-list-heading").get_text(strip=True) == want["chats"]
        assert soup.select_one("nav.msg-list-pane")["aria-label"] != "Conversations"
        assert "Select a conversation" not in soup.get_text(" ")
