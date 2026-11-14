"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The code editor across the theme and content-language axes.

Server-rendered markup only (see tests/test_codeeditor_ui.py): the Pico bridge
that keeps the app title and form controls on the theme, a theme selector that
offers every shared theme with a CodeMirror stylesheet per theme for the live
swap, and German / Mandarin packs that translate the editor's own strings and
set ``<html lang>`` while file names, ids and routes stay put.
"""

import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps import config_dir
from open_apps.apps.codeeditor_app import main as codeeditor

bs4 = pytest.importorskip("bs4")

LAYOUTS = ["default", "sidebar_right", "top_tree"]
LANGUAGES = {"german": "de", "mandarin": "zh-Hans"}
PATHS = ["/codeeditor/", "/codeeditor/developing", "/codeeditor/script.py"]
THEME_DIR = config_dir() / "apps" / "theme"


def configure(tmp_path_factory, *overrides):
    """Seed the (process-wide) editor from a fresh composition."""
    logs_dir = tmp_path_factory.mktemp("codeeditor_variations")
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}", *overrides])
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    codeeditor.set_environment(config.apps)
    return TestClient(codeeditor.app)


@pytest.fixture(scope="module", autouse=True)
def restore_english(tmp_path_factory):
    """Leave the singleton as the other modules expect it: English, default."""
    yield
    configure(tmp_path_factory)


@pytest.fixture(scope="module")
def english(tmp_path_factory):
    return configure(tmp_path_factory)


def get(client, path, layout="default", theme=None):
    codeeditor.app.config.code_editor.layout = layout
    codeeditor.app.config.code_editor.theme = theme
    response = client.get(path)
    assert response.status_code == 200
    return response.text


def body_of(html):
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["style", "script"]):
        tag.decompose()
    return soup


class TestThemeSelector:

    def test_config_lists_every_shared_theme(self):
        with initialize(version_base=None, config_path="../config/"):
            config = compose(config_name="config")
        on_disk = {p.stem for p in THEME_DIR.glob("*.yaml")}
        assert set(config.apps.code_editor.list_of_themes) == on_disk

    def test_selector_offers_every_shared_theme(self, english):
        soup = body_of(get(english, "/codeeditor/"))
        offered = {o["value"] for o in soup.select("select#theme-selector option")}
        assert offered == {p.stem for p in THEME_DIR.glob("*.yaml")}

    def test_empty_list_means_every_theme(self):
        class Cfg:
            class code_editor:
                list_of_themes = []

        assert codeeditor.selectable_themes(Cfg) == sorted(p.stem for p in THEME_DIR.glob("*.yaml"))

    def test_unknown_theme_names_are_dropped(self):
        class Cfg:
            class code_editor:
                list_of_themes = ["dark", "no_such_theme"]

        assert codeeditor.selectable_themes(Cfg) == ["dark"]

    @pytest.mark.parametrize("theme", ["challenging_font", "dark", "meta_dark"])
    def test_active_theme_is_the_selected_option(self, english, theme):
        soup = body_of(get(english, "/codeeditor/", theme=theme))
        selected = soup.select_one("select#theme-selector option[selected]")
        assert selected is not None and selected["value"] == theme

    def test_every_option_has_tokens_and_a_codemirror_theme(self, english):
        import json

        html = get(english, "/codeeditor/")
        tokens = json.loads(re.search(r"window\.OPENAPPS_THEMES = (\{.*?\});", html, re.S).group(1))
        editor = json.loads(re.search(r"window\.OPENAPPS_EDITOR_THEMES = (\{.*?\});", html, re.S).group(1))
        offered = {p.stem for p in THEME_DIR.glob("*.yaml")}
        assert set(tokens) == offered and set(editor) == offered
        cfg = codeeditor.app.config.code_editor
        assert editor["dark"] == cfg.editor_theme_by_tone.dark
        assert editor["vscode_dark"] == cfg.editor_theme_by_tone.dark
        assert editor["default"] == cfg.editor_theme

    def test_live_switch_swaps_the_codemirror_stylesheet(self, english):
        html = get(english, "/codeeditor/script.py")
        assert "editor.setOption('theme', window.editorThemeFor(this.value))" in html
        assert "editor.setOption('theme', this.value)" not in html


class TestPicoBridge:

    @pytest.mark.parametrize("theme", ["dark", "challenging_font", "vscode_dark"])
    def test_bridge_and_canvas(self, english, theme):
        html = get(english, "/codeeditor/", theme=theme)
        css = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
        bridge = re.search(r"\bbody \{([^}]*--pico-h1-color[^}]*)\}", css)
        assert bridge, "no Pico bridge on body"
        for var in ("--pico-h1-color: var(--color-fg)", "--pico-color: var(--color-fg)",
                    "--pico-form-element-background-color: var(--color-bg)"):
            assert var in bridge.group(1), var
        assert re.search(r":root, body \{[^}]*background-color: var\(--color-bg\)", css)
        assert re.search(r"h1, h2, h3, h4 \{[^}]*color: var\(--color-fg\)", css)

    def test_title_heading_is_on_the_page(self, english):
        soup = body_of(get(english, "/codeeditor/", theme="dark"))
        assert soup.select_one("h1") is not None


class TestEnglishUnchanged:

    def test_html_lang(self, english):
        assert '<html lang="en">' in get(english, "/codeeditor/")

    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_strings(self, english, layout):
        index = body_of(get(english, "/codeeditor/", layout=layout)).get_text(" ")
        folder = body_of(get(english, "/codeeditor/developing", layout=layout)).get_text(" ")
        file = get(english, "/codeeditor/script.py", layout=layout)
        for text in ("Explorer", "New File", "New Folder", "No file selected", "Language:",
                     "Theme:", "Code Editor Index Page", "Return to List of Apps"):
            assert text in index, text
        assert "Folder: developing" in folder and "Delete Folder" in folder
        assert "Select a file to edit or create a new one." in folder
        file_text = body_of(file).get_text(" ")
        for text in ("Save", "Delete", "Double-click to rename", "Spaces: 4", "Reveal in Files"):
            assert text in file_text, text
        assert 'aria-label="Close script.py"' in file
        assert "showErrorModal('Failed to save file: ' + data.error);" in file


@pytest.fixture(scope="module", params=list(LANGUAGES.items()), ids=list(LANGUAGES))
def pack(request, tmp_path_factory):
    """(client, language, html lang tag), seeded from that language's pack."""
    language, tag = request.param
    return configure(tmp_path_factory, f"apps/code_editor/content={language}"), language, tag


class TestTranslatedPacks:

    def test_every_label_is_translated(self, pack):
        client, language, tag = pack
        cfg = codeeditor.app.config.code_editor
        same = {"explorer_label"} if language == "german" else set()
        for key, english in codeeditor.LABELS.items():
            if key in same:
                continue
            assert cfg.get(key) and cfg.get(key) != english, f"{language}: {key}"

    @pytest.mark.parametrize("layout", LAYOUTS)
    @pytest.mark.parametrize("path", PATHS)
    def test_pages_render_translated(self, pack, layout, path):
        client, language, tag = pack
        html = get(client, path, layout=layout)
        assert f'<html lang="{tag}">' in html
        soup = body_of(html)
        text = soup.get_text(" ")
        L = codeeditor.L
        for key in ("explorer_label", "new_file_label", "new_folder_label", "index_page_label",
                    "return_to_apps"):
            assert L(key) in text, key
        for english in ("New File", "New Folder", "Code Editor Index Page", "Return to List of Apps",
                        "Language:", "Theme:"):
            assert english not in text, english
        # Ids, routes and file names do not move with the language.
        assert soup.select_one("#mode-selector") and soup.select_one("#theme-selector")
        assert {a["href"] for a in soup.select(".codeeditor-tree a")} >= {"/codeeditor/script.py"}

    def test_file_view_strings(self, pack):
        client, language, tag = pack
        html = get(client, "/codeeditor/script.py")
        L = codeeditor.L
        text = body_of(html).get_text(" ")
        for key in ("save", "delete", "rename_hint_label", "reveal_in_files_label"):
            assert L(key) in text, key
        assert L("spaces_label", n=4) in text
        assert f'aria-label="{L("close_tab_label", name="script.py")}"' in html
        assert "Failed to save file" not in html

    @pytest.mark.parametrize("theme", ["dark", "challenging_font"])
    def test_pack_under_each_theme(self, pack, theme):
        client, language, tag = pack
        html = get(client, "/codeeditor/script.py", theme=theme)
        assert f'<html lang="{tag}">' in html
        assert codeeditor.L("save") in body_of(html).get_text(" ")


@pytest.mark.parametrize("language", LANGUAGES)
def test_seeded_comments_are_valid_for_their_language(language):
    """The translated tree is written over the default one on disk (the
    launcher appends `+filesystem`); its C file must still be C."""
    import yaml

    pack = yaml.safe_load((config_dir() / "apps" / "code_editor" / "content" / f"{language}.yaml").read_text())
    files = {f["name"]: f["content"] for f in pack["+filesystem"] if f["type"] == "file"}
    first_line = files["untitled_c.c"].splitlines()[0]
    assert first_line.startswith("//"), first_line
    assert "script.py" in files
