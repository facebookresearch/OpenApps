"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

UI language for the content-language variation axis.

A content pack declares its language with ``content_language: german`` (or
``mandarin``); a pack without the flag is English. That one flag drives three
things:

* the app's own UI strings, which live in its content packs as flat
  ``*_label`` keys (English in ``content/default.yaml``, overridden by
  ``german.yaml`` / ``mandarin.yaml``) -- the pattern openbanking set;
* the strings every app shares (the back-to-apps link, window controls, the
  dock, toasts), which live in :data:`SHARED` below, so eight apps do not
  carry eight copies of "Close";
* ``<html lang>``, so the browser picks CJK glyph variants and screen readers
  (and agents reading the accessibility tree) know what they are looking at.

Shared shell surfaces (chrome, dock, desktop) follow the start page's pack:
the benchmark switches every app's content at once, so the start page speaks
for the session.
"""
from __future__ import annotations

from typing import Any

ENGLISH = "english"
LANGUAGES: tuple[str, ...] = (ENGLISH, "german", "mandarin")

# BCP 47 tags for <html lang>.
_HTML_LANG: dict[str, str] = {"english": "en", "german": "de", "mandarin": "zh-Hans"}

# Strings rendered by shared code. Key -> language -> text. Every key has all
# three languages (tests/test_i18n.py checks it).
SHARED: dict[str, dict[str, str]] = {
    "return_to_apps": {
        "english": "Return to List of Apps",
        "german": "Zurück zur App-Übersicht",
        "mandarin": "返回应用列表",
    },
    "close": {"english": "Close", "german": "Schließen", "mandarin": "关闭"},
    "cancel": {"english": "Cancel", "german": "Abbrechen", "mandarin": "取消"},
    "confirm": {"english": "Confirm", "german": "Bestätigen", "mandarin": "确认"},
    "save": {"english": "Save", "german": "Speichern", "mandarin": "保存"},
    "delete": {"english": "Delete", "german": "Löschen", "mandarin": "删除"},
    "edit": {"english": "Edit", "german": "Bearbeiten", "mandarin": "编辑"},
    "search": {"english": "Search", "german": "Suchen", "mandarin": "搜索"},
    "notice": {"english": "Notice", "german": "Hinweis", "mandarin": "提示"},
    "minimize": {"english": "Minimize", "german": "Minimieren", "mandarin": "最小化"},
    "maximize": {"english": "Maximize", "german": "Maximieren", "mandarin": "最大化"},
    "restore": {"english": "Restore", "german": "Wiederherstellen", "mandarin": "还原"},
    "window_controls": {
        "english": "Window controls",
        "german": "Fenstersteuerung",
        "mandarin": "窗口控制",
    },
    "desktop": {"english": "Desktop", "german": "Schreibtisch", "mandarin": "桌面"},
    "dock": {"english": "Dock", "german": "Dock", "mandarin": "程序坞"},
    "all_apps": {"english": "All apps", "german": "Alle Apps", "mandarin": "所有应用"},
    "apps": {"english": "Apps", "german": "Apps", "mandarin": "应用"},
    "applications": {
        "english": "Applications",
        "german": "Anwendungen",
        "mandarin": "应用程序",
    },
    "dismiss": {"english": "Dismiss", "german": "Schließen", "mandarin": "忽略"},
    "dismiss_notification": {
        "english": "Dismiss notification: {title}",
        "german": "Benachrichtigung schließen: {title}",
        "mandarin": "忽略通知：{title}",
    },
}


def language_of(cfg: Any) -> str:
    """The language an app's (or pack's) config declares; English when unset
    or unknown, so a typo degrades to the reference rendering."""
    lang = cfg.get("content_language") if cfg is not None and hasattr(cfg, "get") else None
    return lang if lang in LANGUAGES else ENGLISH


def shell_language(apps: Any) -> str:
    """The session language for shared shell surfaces: the start page's."""
    start = apps.get("start_page") if apps is not None and hasattr(apps, "get") else None
    return language_of(start) if start is not None else ENGLISH


def html_lang(language: str) -> str:
    """BCP 47 tag for ``<html lang>``."""
    return _HTML_LANG.get(language, "en")


def t(key: str, language: str = ENGLISH, **fmt: str) -> str:
    """A shared UI string in ``language``, falling back to English."""
    texts = SHARED[key]
    text = texts.get(language) or texts[ENGLISH]
    return text.format(**fmt) if fmt else text


def label(cfg: Any, key: str, default: str) -> str:
    """An app's own UI string: ``cfg[key]`` from its content pack, else
    ``default`` (the English text, so a pack predating the key still renders)."""
    value = cfg.get(key) if cfg is not None and hasattr(cfg, "get") else None
    return str(value) if value else default
