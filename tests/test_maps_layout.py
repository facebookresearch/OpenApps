"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Markup checks for the maps page across layouts and themes.

Chromium cannot run everywhere these tests do, so instead of screenshots this
renders ``/maps`` through ``TestClient`` and asserts on the markup with
``<style>``/``<script>`` stripped: the ids and labels agents and the
UI-understanding questions rely on must survive every layout, and each layout
must emit its panel in the order it is drawn.
"""

import re

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.map_app import main as map_main

LAYOUTS = ["default", "sidebar_left", "bottom_sheet"]
THEMES = ["default", "dark", "solarized", "mono", "challenging_font"]

# Every id the template's script looks up, plus the landmarks agents use.
REQUIRED_IDS = [
    "map",
    "sidebar",
    "returnButton",
    "searchInput",
    "searchBtn",
    "searchResults",
    "currentLocationInfo",
    "popupList",
    "markerCustomizationModal",
    "markerIcon",
    "markerColor",
]


def render(layout: str = "default", theme: str = "default") -> str:
    """Full HTML of ``/maps`` for one layout/theme composition.

    Only the page route is exercised, which reads config but never the
    landmark table, so no database is set up. The TestClient is not used as a
    context manager: that would fire the app's startup hook, which launches
    the OTP route planner.
    """
    with initialize(version_base=None, config_path="../config/"):
        config = compose(
            config_name="config",
            overrides=[f"apps/maps/layout={layout}", f"apps/theme={theme}"],
        )
    map_main.app.config = config.apps
    response = TestClient(map_main.app).get("/maps")
    assert response.status_code == 200
    return response.text


def markup(html: str) -> str:
    """The page body without CSS and JS, so assertions see only elements."""
    html = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"<script>.*?</script>", "", html, flags=re.S)


def script(html: str) -> str:
    return "\n".join(re.findall(r"<script>(.*?)</script>", html, flags=re.S))


def positions(body: str, *needles: str) -> list[int]:
    found = [body.find(n) for n in needles]
    assert -1 not in found, dict(zip(needles, found))
    return found


@pytest.fixture(scope="module")
def pages() -> dict[str, str]:
    return {layout: render(layout) for layout in LAYOUTS}


@pytest.mark.parametrize("layout", LAYOUTS)
class TestEveryLayout:

    def test_ids_are_unique_and_present(self, pages, layout):
        body = markup(pages[layout])
        for element_id in REQUIRED_IDS:
            assert body.count(f'id="{element_id}"') == 1, element_id

    def test_labels_and_headings_unchanged(self, pages, layout):
        body = markup(pages[layout])
        assert "Return to List of Apps" in body
        assert "onclick=\"window.location.href='/'\"" in body
        assert 'placeholder="Search location..."' in body
        assert re.search(r'<button id="searchBtn">\s*Search\s*</button>', body)
        assert "<h3>Current Location Info</h3>" in body
        assert "<h3>Saved Locations</h3>" in body

    def test_search_field_has_a_search_icon(self, pages, layout):
        body = markup(pages[layout])
        icon, field, button = positions(
            body, "fa-search search-icon", 'id="searchInput"', 'id="searchBtn"'
        )
        assert icon < field < button

    def test_location_info_starts_with_a_hint(self, pages, layout):
        body = markup(pages[layout])
        hint = re.search(
            r'<div id="currentLocationInfo" class="info-box">(.*?)</div>', body, re.S
        )
        assert hint and "Click anywhere on the map" in hint.group(1)

    def test_info_heading_sits_between_search_and_saved(self, pages, layout):
        search, info, saved = positions(
            markup(pages[layout]),
            'id="searchInput"',
            "Current Location Info",
            "Saved Locations",
        )
        assert search < info < saved

    def test_saved_rows_keep_the_remove_button(self, pages, layout):
        js = script(pages[layout])
        assert 'class="place-icon" aria-hidden="true"' in js
        assert (
            '<button class="delete-btn" onclick="deletePopup(\'${name}\')" '
            'aria-label="Delete ${name}">×</button>'
        ) in js

    def test_no_hex_colors_in_page_css(self, pages, layout):
        css = re.search(r"<style>(.*?)</style>", pages[layout], re.S).group(1)
        assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)


class TestLayoutsAreDistinct:

    def test_default_keeps_return_above_title_in_a_right_panel(self, pages):
        body = markup(pages["default"])
        map_div, sidebar, ret, title, search = positions(
            body, 'id="map"', 'id="sidebar"', 'id="returnButton"', "<h2>", 'id="searchInput"'
        )
        assert map_div < sidebar < ret < title < search
        assert "sheet-handle" not in body

    def test_sidebar_left_emits_the_panel_before_the_map(self, pages):
        body = markup(pages["sidebar_left"])
        sidebar, title, ret, search, map_div = positions(
            body, 'id="sidebar"', "<h2>", 'id="returnButton"', 'id="searchInput"', 'id="map"'
        )
        assert sidebar < title < ret < search < map_div
        assert 'class="panel-top"' in body

    def test_bottom_sheet_has_a_grab_handle_below_the_map(self, pages):
        body = markup(pages["bottom_sheet"])
        map_div, sidebar, handle, search = positions(
            body, 'id="map"', 'id="sidebar"', 'class="sheet-handle" aria-hidden="true"', 'id="searchInput"'
        )
        assert map_div < sidebar < handle < search

    def test_layouts_render_different_markup(self, pages):
        bodies = {markup(pages[layout]) for layout in LAYOUTS}
        assert len(bodies) == len(LAYOUTS)


class TestLayerControl:

    def test_default_keeps_the_basemap_list_open(self, pages):
        js = script(pages["default"])
        assert "position: 'topright'" in js
        assert "collapsed: false" in js

    @pytest.mark.parametrize("layout", ["sidebar_left", "bottom_sheet"])
    def test_other_layouts_fold_it_into_a_button(self, pages, layout):
        assert "collapsed: true" in script(pages[layout])

    def test_sidebar_left_follows_google_maps_placement(self, pages):
        js = script(pages["sidebar_left"])
        assert "position: 'bottomleft'" in js
        assert "map.zoomControl.setPosition('bottomright')" in js

    def test_unknown_layout_falls_back_to_default_controls(self):
        assert map_main.map_controls("nope") == map_main.map_controls("default")


@pytest.mark.parametrize("theme", THEMES)
def test_every_theme_renders_each_layout(theme):
    for layout in LAYOUTS:
        html = render(layout, theme)
        assert f'class="layout-{layout}"' in html
        assert "--color-surface" in html
