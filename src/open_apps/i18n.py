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
  dock), which live in :data:`SHARED` below, so eight apps do not
  carry eight copies of "Close";
* ``<html lang>``, so the browser picks CJK glyph variants and screen readers
  (and agents reading the accessibility tree) know what they are looking at.

Shared shell surfaces (chrome, dock, desktop) follow the start page's pack:
a language variation switches every app's content at once, so the start page
speaks for the session.
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
    # --- window chrome: title-bar controls, named "<verb> <app>" -----------------
    # Whole phrases rather than verb + title: German puts the verb last.
    "close_window": {
        "english": "Close {title}",
        "german": "{title} schließen",
        "mandarin": "关闭{title}",
    },
    "minimize_window": {
        "english": "Minimize {title}",
        "german": "{title} minimieren",
        "mandarin": "最小化{title}",
    },
    "maximize_window": {
        "english": "Maximize {title}",
        "german": "{title} maximieren",
        "mandarin": "最大化{title}",
    },
    "restore_window": {
        "english": "Restore {title}",
        "german": "{title} wiederherstellen",
        "mandarin": "还原{title}",
    },
    # --- desktop shell: toolbar, launcher, pins ---------------------------------
    "desktop_toolbar": {
        "english": "Desktop toolbar",
        "german": "Schreibtisch-Werkzeugleiste",
        "mandarin": "桌面工具栏",
    },
    "switch_to_dark": {
        "english": "Switch to dark mode",
        "german": "Zum dunklen Modus wechseln",
        "mandarin": "切换到深色模式",
    },
    "switch_to_light": {
        "english": "Switch to light mode",
        "german": "Zum hellen Modus wechseln",
        "mandarin": "切换到浅色模式",
    },
    "celsius": {"english": "Celsius", "german": "Celsius", "mandarin": "摄氏度"},
    "fahrenheit": {"english": "Fahrenheit", "german": "Fahrenheit", "mandarin": "华氏度"},
    "temperature_switch": {
        "english": "Temperature {temperature}. Switch to {other}.",
        "german": "Temperatur {temperature}. Zu {other} wechseln.",
        "mandarin": "温度 {temperature}。切换到{other}。",
    },
    "weather_title": {
        "english": "{condition} — click to show {other}",
        "german": "{condition} — klicken für {other}",
        "mandarin": "{condition} — 点击显示{other}",
    },
    # Weather conditions the toolbar knows (open_apps.ui.molecules.WEATHER_ICONS).
    # English renders the config string verbatim, as it always has.
    "weather_clear": {"english": "clear", "german": "Klar", "mandarin": "晴"},
    "weather_sunny": {"english": "sunny", "german": "Sonnig", "mandarin": "晴朗"},
    "weather_cloudy": {"english": "cloudy", "german": "Bewölkt", "mandarin": "多云"},
    "weather_overcast": {"english": "overcast", "german": "Bedeckt", "mandarin": "阴"},
    "weather_rain": {"english": "rain", "german": "Regen", "mandarin": "雨"},
    "weather_rainy": {"english": "rainy", "german": "Regnerisch", "mandarin": "有雨"},
    "open_launcher": {
        "english": "Open app launcher",
        "german": "App-Starter öffnen",
        "mandarin": "打开应用启动器",
    },
    "close_launcher": {
        "english": "Close app launcher",
        "german": "App-Starter schließen",
        "mandarin": "关闭应用启动器",
    },
    "pin_app": {
        "english": "Pin {title} to desktop",
        "german": "{title} an den Schreibtisch anheften",
        "mandarin": "将{title}固定到桌面",
    },
    "unpin_app": {
        "english": "Unpin {title} from desktop",
        "german": "{title} vom Schreibtisch lösen",
        "mandarin": "取消将{title}固定到桌面",
    },
    "main_page": {"english": "main-page", "german": "Startseite", "mandarin": "主页"},
    # --- start page: the gallery's fallback tiles (no `apps:` inventory) --------
    "fallback_todo": {
        "english": "Manage your tasks and to-dos efficiently",
        "german": "Aufgaben und To-dos effizient verwalten",
        "mandarin": "高效管理您的任务和待办事项",
    },
    "fallback_calendar": {
        "english": "Keep track of your appointments and events",
        "german": "Termine und Veranstaltungen im Blick behalten",
        "mandarin": "跟踪您的约会和活动",
    },
    "fallback_messages": {
        "english": "Chat with friends and colleagues",
        "german": "Mit Freunden und Kollegen chatten",
        "mandarin": "与朋友和同事聊天",
    },
    "fallback_maps": {
        "english": "Navigate and explore locations",
        "german": "Orte finden und erkunden",
        "mandarin": "导航并探索地点",
    },
    "fallback_codeeditor": {
        "english": "Write and edit code seamlessly",
        "german": "Code nahtlos schreiben und bearbeiten",
        "mandarin": "流畅地编写和编辑代码",
    },
    "fallback_onlineshop": {
        "english": "Browse and purchase items online",
        "german": "Artikel online durchsuchen und kaufen",
        "mandarin": "在线浏览和购买商品",
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
