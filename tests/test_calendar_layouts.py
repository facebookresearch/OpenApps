"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Rendered-markup checks for the calendar's layout variants.

Chromium cannot always launch where these run, so each layout (and a few
themes) is rendered through the calendar app's own `TestClient` and asserted
on its markup. `<style>`/`<script>` are dropped before any assertion: the
inline stylesheet names every variant's classes, so a naive "not in the
page" check would pass or fail on CSS text rather than on elements.
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
THEMES = ["default", "dark", "solarized", "mono", "challenging_font"]


@pytest.fixture(scope="module")
def render(tmp_path_factory):
    """Render a calendar route under a given layout and theme.

    `set_environment` rebinds module globals, so whatever another test module
    configured is restored afterwards.
    """
    saved = {
        name: getattr(cal, name)
        for name in ("events", "logo_title_container")
        if hasattr(cal, name)
    }
    saved_config = getattr(cal.app, "config", None)

    def _render(layout="default", theme="default", path="/calendar"):
        logs_dir = tmp_path_factory.mktemp(f"cal-{layout}-{theme}")
        with initialize(version_base=None, config_path="../config/"):
            config = compose(
                config_name="config",
                overrides=[
                    f"logs_dir={logs_dir}",
                    f"apps/calendar/layout={layout}",
                    f"apps/theme={theme}",
                ],
            )
        Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
        cal.set_environment(config.apps)
        response = TestClient(cal.app).get(path)
        assert response.status_code == 200
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["style", "script"]):
            tag.decompose()
        return soup

    yield _render

    for name, value in saved.items():
        setattr(cal, name, value)
    if saved_config is not None:
        cal.app.config = saved_config


def texts(elements):
    return [el.get_text(strip=True) for el in elements]


@pytest.mark.parametrize("layout", LAYOUTS)
class TestEveryLayout:

    def test_eval_selectors_and_labels_survive(self, render, layout):
        soup = render(layout, path="/calendar?view=calendar")
        for selector in ("#calendar-container", "#current-month-year", ".calendar-table"):
            assert soup.select_one(selector) is not None, selector
        labels = texts(soup.select("a"))
        for label in ("< Prev", "Next >", "Calendar", "Agenda", "Return to List of Apps"):
            assert label in labels, label
        assert labels.count("Add Event") == 1

    def test_no_none_leaks_into_the_page(self, render, layout):
        for view in ("calendar", "agenda"):
            text = render(layout, path=f"/calendar?view={view}").get_text()
            assert "(None)" not in text
            assert "None" not in text.split()

    def test_events_are_title_only_chips(self, render, layout):
        soup = render(layout, path="/calendar?view=calendar")
        chips = soup.select(".calendar-table a.event-chip")
        assert chips, "the default content has events every month"
        titles = {e.title for e in cal.events()}
        for chip in chips:
            assert chip.get_text() in titles
            assert chip["title"].startswith(chip.get_text())

    def test_today_is_marked_once(self, render, layout):
        today = datetime.now()
        soup = render(layout, path="/calendar?view=calendar")
        marked = soup.select('.calendar-table [aria-current="date"]')
        assert texts(marked) == [str(today.day)]
        assert "today" in marked[0]["class"]

    def test_other_months_have_no_today_marker(self, render, layout):
        soup = render(layout, path="/calendar/calendar_content/2030/2?view=calendar")
        assert soup.select('[aria-current="date"]') == []

    def test_out_of_month_cells_are_blank_and_distinguished(self, render, layout):
        # March 2027 ends on a Wednesday: four trailing out-of-month cells.
        soup = render(layout, path="/calendar/calendar_content/2027/3?view=calendar")
        empty = soup.select(".calendar-table td.calendar-cell-empty")
        assert empty and all(td.get_text(strip=True) == "" for td in empty)

    def test_view_toggle_is_a_segmented_group_with_one_selection(self, render, layout):
        soup = render(layout, path="/calendar?view=agenda")
        toggle = soup.select_one(".view-toggle")
        assert toggle["role"] == "group"
        assert texts(toggle.select('[aria-current="page"]')) == ["Agenda"]

    def test_today_links_to_the_existing_calendar_route(self, render, layout):
        soup = render(layout, path="/calendar/calendar_content/2030/2?view=agenda")
        today = soup.find("a", string="Today")
        assert today["href"] == "/calendar?view=agenda"

    def test_add_event_is_primary_and_return_is_demoted(self, render, layout):
        soup = render(layout)
        add = soup.find("a", string="Add Event")
        back = soup.find("a", string="Return to List of Apps")
        assert "outline" not in add.get("class", [])
        assert "calendar-return" in back["class"]
        assert add["href"] == "/calendar/create_event/"
        assert back["href"] == "/"


@pytest.mark.parametrize(
    "layout, expected_view, selector",
    [
        ("default", "Calendar", ".calendar-table"),
        ("sidebar_nav", "Calendar", ".calendar-table"),
        ("agenda_first", "Agenda", ".agenda-list"),
    ],
)
@pytest.mark.parametrize(
    "path", ["/calendar?view=bogus", "/calendar/calendar_content/2030/2?view=bogus"]
)
def test_unknown_view_falls_back_to_the_layout_default(
    render, layout, expected_view, selector, path
):
    """`resolve_view`: a typo'd `?view=` lands on the layout's default view
    with the matching toggle segment selected."""
    soup = render(layout, path=path)
    assert soup.select_one(selector) is not None
    assert texts(soup.select('.view-toggle [aria-current="page"]')) == [expected_view]
    assert texts(soup.select(".view-toggle a.active")) == [expected_view]


class TestAgenda:

    def test_grouped_by_day_with_all_day_rows(self, render):
        soup = render("default", path="/calendar?view=agenda")
        days = soup.select(".agenda-list > li.agenda-day")
        assert days
        dates = [day.select_one("h4.agenda-date time")["datetime"] for day in days]
        assert dates == sorted(set(dates)), "one header per day, in order"
        for day in days:
            for row in day.select(".agenda-event"):
                assert row.select_one(".agenda-time").get_text() == "All day"
                assert row.select_one("a.agenda-event-link")["href"].startswith("/calendar/event/")

    def test_empty_month_says_what_to_do(self):
        soup = BeautifulSoup(str(cal.agenda(2031, 4, [], datetime.now().date())), "html.parser")
        empty = soup.select_one("ul.agenda-list > li.agenda-empty")
        assert "No events in April 2031" in empty.get_text()
        assert "Add Event" in empty.get_text()
        assert soup.find("a") is None, "no second 'Add Event' control"


class TestLayoutsStayDistinct:

    def test_default_is_one_toolbar_with_actions_in_the_footer(self, render):
        soup = render("default", path="/calendar?view=calendar")
        toolbar = soup.select_one(".calendar-header.calendar-toolbar")
        nav = texts(toolbar.select(".calendar-nav > *"))
        # Month title sits between prev and next (UI questions rely on it).
        assert nav == ["Today", "< Prev", nav[2], "Next >"]
        assert nav[2] == soup.select_one("#current-month-year").get_text()
        footer = texts(soup.select(".footer-container a"))
        assert footer == ["Add Event", "Return to List of Apps"]

    def test_agenda_first_is_centred_and_leads_with_the_agenda(self, render):
        soup = render("agenda_first")
        assert soup.select_one(".calendar-header.calendar-header-centered") is not None
        assert soup.select_one(".agenda-list") is not None
        assert soup.select_one(".calendar-table") is None
        assert texts(soup.select(".view-toggle a")) == ["Agenda", "Calendar"]

    def test_sidebar_nav_rail_order_and_mini_month(self, render):
        soup = render("sidebar_nav", path="/calendar?view=calendar")
        rail = soup.select_one(".calendar-body-sidebar > .calendar-rail")
        order = [
            el.get_text(strip=True)
            for el in rail.select("a.calendar-create, #current-month-year, .view-toggle")
        ]
        assert order[0] == "Add Event" and order[-1] == "CalendarAgenda"
        mini = rail.select_one("table.mini-month")
        assert mini["aria-hidden"] == "true"
        assert "calendar-table" not in mini["class"]
        assert "Add Event" not in texts(soup.select(".footer-container a"))


@pytest.mark.parametrize("theme", THEMES)
def test_renders_under_every_theme(render, theme):
    for layout in LAYOUTS:
        soup = render(layout, theme)
        assert soup.select_one("#calendar-container") is not None


def test_calendar_css_uses_theme_tokens_only():
    """Hex or rgb() literals would ignore the active theme."""
    for css in (cal._COMPONENT_CSS, cal._VIEW_CSS):
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
        assert "rgb(" not in css and "rgba(" not in css
