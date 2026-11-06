"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The calendar under every variation axis: dark and challenging_font themes
(root background and Pico bridge on tokens), German and Mandarin UI strings
with `<html lang>`, and every layout in every language. Markup only, through
the app's own TestClient, as in test_calendar_layouts.py.
"""

import re
from datetime import datetime
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.calendar_app import main as cal

LAYOUTS = ["default", "agenda_first", "sidebar_nav"]

GERMAN = {
    "lang": "de",
    "add": "Termin hinzufügen",
    "prev": "< Zurück",
    "next": "Weiter >",
    "today": "Heute",
    "views": ["Kalender", "Terminliste"],
    "weekdays": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    "return": "Zurück zur App-Übersicht",
}
MANDARIN = {
    "lang": "zh-Hans",
    "add": "添加活动",
    "prev": "< 上个月",
    "next": "下个月 >",
    "today": "今天",
    "views": ["日历", "日程"],
    "weekdays": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
    "return": "返回应用列表",
}
PACKS = {"german": GERMAN, "mandarin": MANDARIN}


@pytest.fixture(scope="module")
def render(tmp_path_factory):
    """`render(path, layout=, theme=, content=)` -> (raw html, soup without
    style/script). Restores the module globals other test modules set."""
    saved = {n: getattr(cal, n) for n in ("events", "logo_title_container") if hasattr(cal, n)}
    saved_config = getattr(cal.app, "config", None)
    configured = {}

    def _render(path="/calendar", layout="default", theme="default", content="default"):
        key = (layout, theme, content)
        if configured.get("key") != key:
            logs_dir = tmp_path_factory.mktemp(f"cal-{layout}-{theme}-{content}")
            with initialize(version_base=None, config_path="../config/"):
                config = compose(
                    config_name="config",
                    overrides=[
                        f"logs_dir={logs_dir}",
                        f"apps/calendar/layout={layout}",
                        f"apps/theme={theme}",
                        f"apps/calendar/content={content}",
                    ],
                )
            Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
            cal.set_environment(config.apps)
            configured["key"] = key
        response = TestClient(cal.app).get(path)
        assert response.status_code == 200, path
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["style", "script"]):
            tag.decompose()
        return response.text, soup

    yield _render

    for name, value in saved.items():
        setattr(cal, name, value)
    if saved_config is not None:
        cal.app.config = saved_config


def html_tag(raw):
    return re.search(r"<html[^>]*>", raw).group(0)


def texts(elements):
    return [el.get_text(strip=True) for el in elements]


# ---- Themes -------------------------------------------------------------


@pytest.mark.parametrize("theme,tone", [("dark", "dark"), ("challenging_font", "light")])
def test_theme_paints_the_root_and_bridges_pico(render, theme, tone):
    raw, soup = render(theme=theme)
    # <html> is painted from the token, not left to Pico's white :root.
    assert re.search(r"html, body \{\s*background-color: var\(--color-bg\);", raw)
    # Pico's own variables (headings included) are mapped onto the tokens.
    for var in ("--pico-h1-color: var(--color-fg)", "--pico-background-color: var(--color-bg)",
                "--pico-form-element-background-color: var(--color-bg)"):
        assert var in raw
    # Pico's palette is pinned to the theme's tone, not the OS preference.
    assert f'data-theme="{tone}"' in html_tag(raw)
    assert soup.select_one(".calendar-table")


def test_dark_tokens_reach_the_page(render):
    raw, _ = render(theme="dark")
    assert "--color-bg: #121212;" in raw
    assert "--color-fg: #ffffff;" in raw


def test_challenging_font_reaches_the_page(render):
    raw, _ = render(theme="challenging_font")
    assert "Brush Script MT" in raw
    assert "font-family: var(--font-family);" in raw


# ---- English stays the reference rendering --------------------------------


def test_english_default_is_unchanged(render):
    raw, soup = render()
    assert 'lang="en"' in html_tag(raw)
    assert soup.select_one("a.calendar-create").get_text(strip=True) == "Add Event"
    nav = texts(soup.select(".calendar-nav a[role=button]"))
    assert nav == ["Today", "< Prev", "Next >"]
    assert texts(soup.select(".view-toggle a")) == ["Calendar", "Agenda"]
    assert soup.select_one(".view-toggle")["aria-label"] == "View"
    assert texts(soup.select(".calendar-table th")) == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    now = datetime.now()
    assert soup.select_one("#current-month-year").get_text(strip=True) == now.strftime("%B %Y")
    assert soup.select_one("a.calendar-return").get_text(strip=True) == "Return to List of Apps"


# ---- German and Mandarin --------------------------------------------------


@pytest.mark.parametrize("content", PACKS)
def test_month_view_is_localized(render, content):
    want = PACKS[content]
    raw, soup = render(content=content)
    assert f'lang="{want["lang"]}"' in html_tag(raw)
    assert soup.select_one("a.calendar-create").get_text(strip=True) == want["add"]
    assert texts(soup.select(".calendar-nav a[role=button]")) == [want["today"], want["prev"], want["next"]]
    assert texts(soup.select(".view-toggle a")) == want["views"]
    assert texts(soup.select(".calendar-table th")) == want["weekdays"]
    assert soup.select_one("a.calendar-return").get_text(strip=True) == want["return"]
    visible = soup.get_text(" ")
    for english in ("Add Event", "< Prev", "Next >", "Return to List of Apps", "Today"):
        assert english not in visible
    # Routes and ids do not move with the language.
    assert soup.select_one("a.calendar-create")["href"] == "/calendar/create_event/"
    assert soup.select_one("#current-month-year")


def test_month_heading_and_today_tooltip_are_localized(render):
    now = datetime.now()
    _, soup = render(content="mandarin")
    assert soup.select_one("#current-month-year").get_text(strip=True) == f"{now.year}年{now.month}月"
    _, soup = render(content="german")
    german_months = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
                     "August", "September", "Oktober", "November", "Dezember"]
    german_days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    month = german_months[now.month - 1]
    assert soup.select_one("#current-month-year").get_text(strip=True) == f"{month} {now.year}"
    tooltip = soup.select_one("a.calendar-today")["title"]
    assert tooltip == f"{german_days[now.weekday()]}, {now.day}. {month}"


@pytest.mark.parametrize("content", PACKS)
def test_agenda_is_localized(render, content):
    _, soup = render("/calendar/calendar_content/2025/7?view=agenda", content=content)
    all_day = {"german": "Ganztägig", "mandarin": "全天"}[content]
    assert all_day in texts(soup.select(".agenda-time"))
    assert "All day" not in soup.get_text(" ")
    # Event titles are data and stay as the pack gives them: tasks remove
    # events by their English title.
    assert "WACV 2026 Abstract Deadline" in texts(soup.select("a.agenda-event-link"))


def test_empty_agenda_is_localized(render):
    render(content="german")  # configures the app with the German pack
    soup = BeautifulSoup(str(cal.agenda(2031, 2, [], datetime.now().date())), "html.parser")
    assert soup.select_one(".agenda-empty-title").get_text(strip=True) == "Keine Termine im Februar 2031"


@pytest.mark.parametrize("content", PACKS)
def test_create_form_is_localized(render, content):
    _, soup = render("/calendar/create_event", content=content)
    labels = texts(soup.select("form label"))
    english = ["Title", "Date", "Description", "URL", "Invitees", "Location", "Recurring"]
    assert len(labels) == len(english)
    assert not set(labels) & (set(english) - {"URL"})
    options = soup.select("select#recurring option")
    assert [o["value"] for o in options] == ["none", "weekly", "monthly", "yearly"]
    assert "Not Recurring" not in texts(options)
    assert soup.select_one("form button[type=submit]").get_text(strip=True) != "Submit"
    # Field names the save handler reads are unchanged.
    assert [i["name"] for i in soup.select("form input")] == ["title", "date", "url", "invitees", "location"]


def test_german_placeholders_are_fixed(render):
    _, soup = render("/calendar/create_event", content="german")
    assert soup.select_one("#title")["placeholder"] == "Titel der Veranstaltung"
    assert soup.select_one("#location")["placeholder"] != "None"


def test_event_page_is_localized(render):
    _, soup = render("/calendar/event/1", content="mandarin")
    text = soup.get_text(" ")
    assert "日期:" in text and "Date:" not in text
    assert soup.select_one("form button[type=submit]").get_text(strip=True) == "删除活动"


def test_error_banner_is_localized(render):
    _, soup = render("/calendar?error=Invalid+date+format", content="german")
    assert soup.select_one(".error-message").get_text(strip=True).startswith("Ungültiges Datumsformat")


# ---- Every layout in every language ---------------------------------------


@pytest.mark.parametrize("content", PACKS)
@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_layout_renders_in_every_language(render, layout, content):
    want = PACKS[content]
    raw, soup = render(layout=layout, theme="dark", content=content)
    assert f'lang="{want["lang"]}"' in html_tag(raw)
    assert soup.select_one("a.calendar-create").get_text(strip=True) == want["add"]
    assert set(texts(soup.select(".view-toggle a"))) == set(want["views"])
    assert soup.select(".calendar-table, .agenda-list")
    if layout == "sidebar_nav":
        initials = texts(soup.select(".mini-month th"))
        assert len(set(initials)) > 1  # not seven copies of 周
