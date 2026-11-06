"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The Files app across the variation axes: theme (dark, challenging_font),
content language (german, mandarin) and layout (default, icons, folder_tabs).

Rendered through ``TestClient`` against the Files app alone, so no browser and
no other app is needed. What is protected:

- the page frame and Pico are painted from the theme's tokens, so a dark theme
  leaves no white canvas and the script face reaches Pico's own font stack;
- a language pack translates the chrome and tags ``<html lang>``, while the
  English pack renders exactly the English labels;
- every layout x language keeps the same ids and routes.
"""

import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.files_app import main as files_app

LAYOUTS = ["default", "icons", "folder_tabs"]
LANGUAGES = ["default", "german", "mandarin"]
LANDMARKS = ("files-sidebar", "files-toolbar", "files-content", "files-preview", "files-dialog")

# A handful of chrome labels per pack, each of which must be on /files/Documents.
LABELS = {
    "default": ["Documents", "Recently Deleted", "Search files", "Date modified", "Upload"],
    "german": ["Dokumente", "Zuletzt gelöscht", "Dateien durchsuchen", "Änderungsdatum", "Hochladen"],
    "mandarin": ["文稿", "最近删除", "修改日期"],
}
HTML_LANG = {"default": "en", "german": "de", "mandarin": "zh-Hans"}


def render(tmp_path_factory, path: str = "/files/Documents", *overrides: str) -> str:
    logs_dir = tmp_path_factory.mktemp("files_variations")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}", *overrides])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    files_app.set_environment(cfg.apps)
    response = TestClient(files_app.app).get(path)
    assert response.status_code == 200, response.text[:500]
    return response.text


def css(html: str) -> str:
    return "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))


def ids(html: str) -> set[str]:
    return set(re.findall(r'\sid="([^"]+)"', html))


def hrefs(html: str) -> set[str]:
    return set(re.findall(r'\shref="([^"]+)"', html))


class TestThemes:
    @pytest.mark.parametrize("theme", ["dark", "challenging_font"])
    def test_page_frame_and_pico_follow_the_theme(self, tmp_path_factory, theme):
        html = render(tmp_path_factory, "/files/Documents", f"apps/theme={theme}")
        sheet = css(html)
        # The whole canvas, not just the app's box.
        assert re.search(r"html, body \{[^}]*background-color: var\(--color-bg\)", sheet)
        for bridge in ("--pico-h1-color: var(--color-fg)", "--pico-font-family: var(--font-family)",
                       "--pico-form-element-background-color: var(--color-bg)",
                       "--pico-card-background-color: var(--color-bg)"):
            assert bridge in sheet, bridge

    def test_dark_tokens_and_pico_polarity(self, tmp_path_factory):
        html = render(tmp_path_factory, "/files/Documents", "apps/theme=dark")
        assert "--color-bg: #121212" in html
        assert 'data-theme="dark"' in html

    def test_challenging_font_reaches_the_page(self, tmp_path_factory):
        html = render(tmp_path_factory, "/files/Documents", "apps/theme=challenging_font")
        assert "Brush Script MT" in html
        assert 'data-theme="light"' in html


class TestLanguages:
    @pytest.mark.parametrize("pack", LANGUAGES)
    def test_chrome_is_translated_and_tagged(self, tmp_path_factory, pack):
        html = render(tmp_path_factory, "/files/Documents", f"apps/files/content={pack}")
        assert f'<html lang="{HTML_LANG[pack]}"' in html
        for label in LABELS[pack]:
            assert label in html, f"{pack}: {label!r} missing"

    @pytest.mark.parametrize("pack", ["german", "mandarin"])
    def test_no_english_kind_or_size_labels_leak(self, tmp_path_factory, pack):
        html = render(tmp_path_factory, "/files/Documents", f"apps/files/content={pack}")
        html = re.sub(r"<(style|script)\b.*?</\1>", "", html, flags=re.S)
        for english in ("Recently Deleted", "Date modified", "PDF document", "Plain text", " bytes<"):
            assert english not in html, f"{pack}: {english!r} still English"

    @pytest.mark.parametrize("pack", ["german", "mandarin"])
    def test_every_kind_is_translated(self, tmp_path_factory, pack):
        logs_dir = tmp_path_factory.mktemp("files_kinds")
        with initialize(version_base=None, config_path="../config/"):
            english = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"]).apps.files
            local = compose(config_name="config",
                            overrides=[f"logs_dir={logs_dir}", f"apps/files/content={pack}"]).apps.files
        assert local.content_language == pack
        for kind, text in english.ui.kinds.items():
            if text not in ("JSON", "YAML", "HTML", "CSS", "JavaScript", "Markdown"):
                assert local.ui.kinds[kind] != text, f"{pack}: kind {kind} untranslated"
        assert local.ui.bytes_label != english.ui.bytes_label


@pytest.fixture(scope="module")
def reference(tmp_path_factory) -> str:
    """The English, default-layout page every variant is compared against."""
    return render(tmp_path_factory, "/files/Documents")


class TestLayouts:
    @pytest.mark.parametrize("layout", LAYOUTS)
    @pytest.mark.parametrize("pack", LANGUAGES)
    def test_layout_x_language_keeps_ids_and_routes(self, tmp_path_factory, reference, layout, pack):
        html = render(tmp_path_factory, "/files/Documents",
                      f"apps/files/layout={layout}", f"apps/files/content={pack}")
        assert f'data-layout="{layout}"' in html
        assert set(LANDMARKS) <= ids(html)
        # Same places to go: the folder and action links do not depend on
        # layout or language (the view toggle's query string may).
        strip = lambda links: {h.split("?")[0] for h in links}  # noqa: E731
        assert strip(hrefs(reference)) == strip(hrefs(html))

    def test_folder_tabs_runs_the_folders_across_the_top(self, tmp_path_factory):
        html = render(tmp_path_factory, "/files/Documents", "apps/files/layout=folder_tabs")
        sheet = css(html)
        assert '.fs-root[data-layout="folder_tabs"]:not(.is-phone) .fs-window' in sheet
        assert '"side side" "bar bar" "main preview"' in sheet
        # The sidebar still comes first in the DOM, so the tree reads top-down.
        assert html.index('id="files-sidebar"') < html.index('id="files-toolbar"') < html.index('id="files-content"')
        # A list, like default; `icons` is the grid.
        assert 'class="fs-table"' in html and 'class="fs-grid"' not in html
