"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Render checks for the UI Library across themes, languages and layouts.

``/uilibrary`` is rendered through ``TestClient`` for each composition and
asserted on as markup: the Pico bridge and page background that keep a dark or
script-font theme from leaking Pico's palette, the content-language labels and
``<html lang>``, and that English renders the strings it always did.
"""
from __future__ import annotations

import itertools
import re
from pathlib import Path

import bs4
import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.uilibrary_app import main as uilib

LAYOUTS = ["default", "grid_gallery", "single_column"]
LANGS = {"default": "en", "german": "de", "mandarin": "zh-Hans"}
PAGES = ["/uilibrary", "/uilibrary/atoms", "/uilibrary/molecules", "/uilibrary/brand"]

_counter = itertools.count()


def client_for(tmp_path_factory, *overrides: str) -> TestClient:
    """A client over a freshly seeded app for one composition."""
    logs_dir = tmp_path_factory.mktemp(f"uilib{next(_counter)}")
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}", *overrides])
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    uilib.set_environment(config.apps)
    return TestClient(uilib.app)


def get(client: TestClient, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, path
    return response.text


def visible(html: str) -> bs4.BeautifulSoup:
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["style", "script"]):
        tag.decompose()
    return soup


def styles(html: str) -> str:
    return "\n".join(re.findall(r"<style>(.*?)</style>", html, flags=re.S))


@pytest.fixture(scope="module")
def english(tmp_path_factory):
    return client_for(tmp_path_factory)


class TestEnglishUnchanged:
    def test_tokens_page_strings(self, english):
        text = visible(get(english, "/uilibrary")).get_text(" ")
        for s in ("Active theme:", "Color", "Typography", "Shape and spacing",
                  "The quick brown fox jumps over the lazy dog", "Return to List of Apps",
                  "Tokens", "Atoms", "Molecules", "Brand"):
            assert s in text, s

    def test_atoms_page_strings(self, english):
        html = get(english, "/uilibrary/atoms")
        soup = visible(html)
        text = soup.get_text(" ")
        for s in ("A panel with a token background and border.", "The quick brown fox (title)",
                  "Primary action", "one", "two", "three", "above", "below", "Pin",
                  "UIButton(variant='primary')"):
            assert s in text, s
        assert soup.find(attrs={"aria-label": "Show UIButton danger variant"})
        assert soup.find(attrs={"aria-label": "Pin the UIButton story"})
        assert soup.find(attrs={"aria-label": "ghost icon button"})

    def test_html_lang_is_en(self, english):
        assert re.search(r'<html[^>]*\blang="en"', get(english, "/uilibrary"))

    def test_routes_and_ids(self, english):
        soup = visible(get(english, "/uilibrary/atoms"))
        assert soup.find(id="uilibrary") and soup.find(id="story-UIButton")
        hrefs = {a["href"] for a in soup.select(".uilib-nav a")}
        assert hrefs == {"/uilibrary/tokens", "/uilibrary/atoms", "/uilibrary/molecules",
                         "/uilibrary/brand", "/"}

    def test_variant_and_pin_still_change_state(self, english):
        r = english.post("/uilibrary/atoms/Badge/variant/danger")
        assert r.status_code == 200 and 'data-variant="danger"' in r.text
        r = english.post("/uilibrary/atoms/Badge/pin")
        assert "Unpin" in r.text and "pinned" in r.text
        rows = {row["component"]: row for row in english.get("/uilibrary_all").json()}
        assert rows["Badge"]["variant"] == "danger" and rows["Badge"]["pinned"]


@pytest.mark.parametrize("theme", ["dark", "challenging_font"])
class TestThemes:
    def test_bridge_and_root_background(self, tmp_path_factory, theme):
        client = client_for(tmp_path_factory, f"apps/theme={theme}")
        for path in PAGES:
            html = get(client, path)
            css = styles(html)
            # The canvas, not just <body>: Pico paints <html> itself.
            assert re.search(r"html,\s*body\s*\{[^}]*background-color:\s*var\(--color-bg\)", css)
            for var in ("--pico-h1-color: var(--color-fg)", "--pico-primary: var(--color-primary)",
                        "--pico-code-background-color: var(--color-surface)",
                        "--pico-color: var(--color-fg)"):
                assert var in css, var
            # Nav links in the theme's ink, not Pico's link blue.
            assert re.search(r"\.uilib-nav a \{[^}]*color: var\(--color-fg\)", css)
            assert f"Active theme: {theme}" in html or path != "/uilibrary"

    def test_theme_tokens_emitted(self, tmp_path_factory, theme):
        client = client_for(tmp_path_factory, f"apps/theme={theme}")
        css = styles(get(client, "/uilibrary"))
        if theme == "dark":
            assert "--color-bg: #121212" in css
        else:
            assert "Brush Script MT" in css


@pytest.mark.parametrize("pack,lang", [("german", "de"), ("mandarin", "zh-Hans")])
class TestLanguages:
    EXPECT = {
        "german": ["Aktives Design:", "Farbe", "Typografie", "Form und Abstände",
                   "Zurück zur App-Übersicht", "Atome"],
        "mandarin": ["当前主题：", "颜色", "字体排印", "形状与间距", "返回应用列表", "原子组件"],
    }
    ATOMS = {
        "german": ["Anheften", "eins", "oben", "Aktion Primary"],
        "mandarin": ["置顶", "一", "上方", "Primary 操作"],
    }

    def test_tokens_page_localized(self, tmp_path_factory, pack, lang):
        client = client_for(tmp_path_factory, f"apps/ui_library/content={pack}")
        html = get(client, "/uilibrary")
        assert re.search(rf'<html[^>]*\blang="{lang}"', html)
        text = visible(html).get_text(" ")
        for s in self.EXPECT[pack]:
            assert s in text, s
        for s in ("Active theme:", "Shape and spacing", "Return to List of Apps"):
            assert s not in text, s

    def test_atoms_page_localized(self, tmp_path_factory, pack, lang):
        client = client_for(tmp_path_factory, f"apps/ui_library/content={pack}")
        soup = visible(get(client, "/uilibrary/atoms"))
        text = soup.get_text(" ")
        for s in self.ATOMS[pack]:
            assert s in text, s
        # Identifiers stay: component names, variant knobs, call snippets.
        assert "UIButton(variant='primary')" in text and "danger" in text
        assert not soup.find(attrs={"aria-label": "Show UIButton danger variant"})


@pytest.mark.parametrize(
    "layout,pack,theme",
    [(layout, pack, theme) for layout in LAYOUTS for pack in LANGS for theme in ("dark", "challenging_font")],
)
def test_every_layout_language_and_theme_renders(tmp_path_factory, layout, pack, theme):
    client = client_for(
        tmp_path_factory,
        f"apps/ui_library/layout={layout}",
        f"apps/ui_library/content={pack}",
        f"apps/theme={theme}",
    )
    html = get(client, "/uilibrary")
    assert re.search(rf'<html[^>]*\blang="{LANGS[pack]}"', html)
    soup = visible(html)
    root = soup.find(id="uilibrary")
    assert root is not None and root["data-layout"] == layout
    assert soup.select_one(f".uilib.is-{layout}") is not None
    assert "--pico-h1-color: var(--color-fg)" in styles(html)
    if layout == "single_column":
        # every section on the one page
        assert soup.find(id="uilib-atoms") and soup.find(id="uilib-brand")


@pytest.mark.parametrize("pack", ["adversarial_descriptions", "misleading_descriptions", "long_descriptions"])
def test_other_packs_compose_and_render(tmp_path_factory, pack):
    client = client_for(tmp_path_factory, f"apps/ui_library/content={pack}")
    html = get(client, "/uilibrary/atoms")
    assert "Return to List of Apps" in html
    assert re.search(r'<html[^>]*\blang="en"', html)
