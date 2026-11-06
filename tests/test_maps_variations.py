"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The maps page across the theme and content-language axes.

Rendered through ``TestClient`` (see tests/test_maps_layout.py for why not
screenshots): the dark and mono tones must reach the tiles through a CSS
filter scoped to the tile layers, the chrome Leaflet draws must be on theme
tokens, and the German and Mandarin packs must translate every string the
page renders -- markup and script alike -- and set ``<html lang>``.
"""

import json
import re

import pytest

from open_apps.apps.map_app import main as map_main

from tests.test_maps_layout import LAYOUTS, markup, render, script

LANGUAGES = {"german": "de", "mandarin": "zh-Hans"}


def page_text(html: str) -> dict:
    """The JSON label dict the page hands its script."""
    match = re.search(r"const TEXT = (\{.*?\});\n", html, re.S)
    assert match, "label dict missing from the page script"
    return json.loads(match.group(1))


class TestToneFilter:

    def test_dark_filters_the_street_tiles(self):
        html = render(theme="dark")
        rule = re.search(
            r"\.leaflet-tile-pane \.leaflet-layer:not\(\.no-tone-filter\) \{\s*filter: ([^;]+);",
            html,
        )
        assert rule, "dark theme emitted no tile filter"
        assert "invert(1)" in rule.group(1) and "hue-rotate(180deg)" in rule.group(1)

    def test_mono_greys_the_street_tiles(self):
        assert "filter: grayscale(1);" in render(theme="mono")

    @pytest.mark.parametrize("theme", ["default", "solarized", "challenging_font"])
    def test_light_themes_have_no_filter(self, theme):
        assert "tile-tone-filter" not in render(theme=theme)

    def test_filter_never_reaches_markers_or_controls(self):
        """Markers live in the marker pane, controls in .leaflet-control-container;
        the rule names the tile pane only, so neither is ever inverted."""
        html = render(theme="dark")
        css = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
        for selector in re.findall(r"([^{}]+)\{[^}]*\bfilter:", css):
            assert "leaflet-tile-pane" in selector, selector
            assert "marker" not in selector

    def test_imagery_layers_opt_out(self):
        js = script(render(theme="dark"))
        for name in ("ESRI Satellite", "ESRI Terrain", "ESRI Dark Gray", "ESRI Light Gray"):
            block = re.search(rf"'{name}': L\.tileLayer\([^)]*\{{(.*?)\}}\)", js, re.S)
            assert block and "className: 'no-tone-filter'" in block.group(1), name
        street = re.search(r"'OpenStreetMap': L\.tileLayer\([^)]*\{(.*?)\}\)", js, re.S)
        assert "no-tone-filter" not in street.group(1)

    def test_dark_keeps_the_labelled_street_map(self):
        js = script(render(theme="dark"))
        assert "(layers['OpenStreetMap']" in js

    def test_unsafe_filter_values_are_dropped(self):
        html = render(theme="dark", extra=("apps.maps.filter_by_tone.dark='invert(1)} body {display:none'",))
        assert "display:none" not in html
        assert "tile-tone-filter" not in html


class TestThemedChrome:
    """Leaflet's own controls and the JS-built buttons draw from the tokens."""

    @pytest.fixture(scope="class")
    def html(self):
        return render(theme="dark")

    def test_attribution_is_tokenised(self, html):
        rule = re.search(r"\.leaflet-container \.leaflet-control-attribution \{([^}]*)\}", html)
        assert rule and "var(--color-bg)" in rule.group(1) and "var(--color-muted)" in rule.group(1)

    def test_zoom_bar_is_tokenised(self, html):
        rule = re.search(r"\.leaflet-container \.leaflet-bar a \{([^}]*)\}", html)
        assert rule and "var(--color-bg)" in rule.group(1) and "var(--color-fg)" in rule.group(1)

    def test_no_fixed_colours_in_the_script(self, html):
        js = script(html)
        for fixed in ("#2ecc71", "'#808080'", "'#FFFFFF'", "saveBtn.style.color"):
            assert fixed not in js, fixed
        assert "saveBtn.className = 'save-result-btn'" in js

    def test_marker_dialog_uses_tokens(self, html):
        assert "rgba(0,0,0,0.3)" not in html
        rule = re.search(r"#markerCustomizationModal \{([^}]*)\}", html)
        assert rule and "var(--color-bg)" in rule.group(1) and "var(--color-fg)" in rule.group(1)


class TestEnglishUnchanged:

    def test_html_lang_is_en(self):
        assert '<html lang="en">' in render()

    def test_visible_strings(self):
        html = render(extra=("apps.maps.allow_planning=true", "apps.maps.popup_display_rule=diy"))
        body = markup(html)
        for text in (
            "<title>World Map</title>",
            "Return to List of Apps",
            'placeholder="Search location..."',
            "<h3>Current Location Info</h3>",
            "<h3>Route Planning</h3>",
            'placeholder="From location"',
            'placeholder="To location"',
            "Calculate Route</button>",
            "Select Route Option:",
            "<span>Select an option</span>",
            "<h3>Saved Locations</h3>",
            "<h3>Customize Marker</h3>",
            '<label for="markerIcon">Icon:</label>',
            '<label for="markerColor">Color:</label>',
            '<option value="map-marker">Default Marker</option>',
            '<option value="cadetblue">Cadet Blue</option>',
            ">Cancel</button>",
            ">Confirm</button>",
        ):
            assert text in body, text

    def test_coarse_icon_list_is_the_eleven_places(self):
        body = markup(render())
        icons = re.findall(r'<option value="([^"]+)">', body.split('id="markerIcon"')[1].split("</select>")[0])
        assert icons == list(map_main.MARKER_ICONS)[:11]

    def test_fine_icon_list_has_every_icon(self):
        body = markup(render(extra=("apps.maps.granularity=fine",)))
        icons = re.findall(r'<option value="([^"]+)">', body.split('id="markerIcon"')[1].split("</select>")[0])
        assert icons == list(map_main.MARKER_ICONS)

    def test_script_strings(self):
        text = page_text(render())
        assert text["no_results_label"] == "No results found."
        assert text["option_label"] == "Option {n}"
        assert text["time_locale"] == "en-US"


@pytest.mark.parametrize("language,tag", LANGUAGES.items())
class TestTranslatedPacks:

    def test_html_lang(self, language, tag):
        assert f'<html lang="{tag}">' in render(extra=(f"apps/maps/content={language}",))

    def test_every_label_is_translated(self, language, tag):
        text = page_text(render(extra=(f"apps/maps/content={language}",)))
        # Strings that legitimately read the same in the target language.
        same = {"option_label"} if language == "german" else set()
        for key, english in map_main.LABELS.items():
            if key in same:
                continue
            assert text[key] != english, f"{language} pack leaves {key} in English"
        for key in ("marker_icons", "marker_colors"):
            untranslated = [k for k, v in text[key].items() if v == getattr(map_main, key.upper())[k]]
            allowed = {"utensils", "tree", "bed", "landmark", "orange"} if language == "german" else set()
            assert set(untranslated) <= allowed, (key, untranslated)

    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_each_layout_renders_the_pack(self, language, tag, layout):
        html = render(layout, extra=(f"apps/maps/content={language}", "apps.maps.allow_planning=true"))
        body = markup(html)
        text = page_text(html)
        assert f'class="layout-{layout}"' in html
        for key in ("saved_locations_label", "current_location_label", "route_planning_label",
                    "return_to_apps", "search", "customize_marker_label"):
            assert text[key] in body, key
        assert "Saved Locations" not in body and "Return to List of Apps" not in body
        # Ids agents and the page script look up do not move with the language.
        for element_id in ("searchInput", "searchBtn", "popupList", "returnButton"):
            assert f'id="{element_id}"' in body

    @pytest.mark.parametrize("theme", ["dark", "challenging_font"])
    def test_pack_under_each_theme(self, language, tag, theme):
        html = render(theme=theme, extra=(f"apps/maps/content={language}",))
        assert f'<html lang="{tag}">' in html
        assert ("tile-tone-filter" in html) == (theme == "dark")

    def test_server_messages_follow_the_pack(self, language, tag):
        render(extra=(f"apps/maps/content={language}",))
        assert map_main._label("location_not_found_label") != map_main.LABELS["location_not_found_label"]


def test_nyc_landmarks_keep_their_names_in_every_pack(tmp_path):
    """Place names are data: tasks such as remove_times_square_from_favorites
    match them by name, so the translated packs append places but never
    rename the five default landmarks."""
    from hydra import compose, initialize

    from open_apps.utils import merge_plus_keys

    for language in LANGUAGES:
        with initialize(version_base=None, config_path="../config/"):
            config = compose(config_name="config", overrides=[f"apps/maps/content={language}", f"logs_dir={tmp_path}"])
        names = [p["name"] for p in merge_plus_keys(config).apps.maps.saved_places]
        assert {"Times Square", "Statue of Liberty", "Central Park"} <= set(names)
