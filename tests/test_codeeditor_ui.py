"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Markup tests for the code editor's chrome, per layout and theme.

Chromium does not run everywhere these tests do, so they assert on the
server-rendered DOM rather than on pixels: which region each control sits
in, that DOM order matches the layout, and that nothing the eval harness or
the UI questions (`tests/ui_questions/ui_questions.json`) depend on moved.
"""

import re
from pathlib import Path

import pytest
from fasthtml.common import to_xml
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.codeeditor_app import main as codeeditor

bs4 = pytest.importorskip("bs4")

LAYOUTS = ["default", "sidebar_right", "top_tree"]
THEMES = ["default", "dark", "solarized", "mono", "challenging_font"]

# Tailwind colour utilities the chrome used to carry. They hard-code a
# palette the shared theme cannot reach.
HARDCODED_COLOUR_UTILITIES = re.compile(
    r"\b(?:hover:)?(?:text|bg|border)-(?:white|black|(?:gray|blue|slate)-\d{2,3})\b"
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("codeeditor_ui")
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    codeeditor.set_environment(config.apps)
    yield TestClient(codeeditor.app)
    codeeditor.app.config.code_editor.layout = "default"
    codeeditor.app.config.code_editor.theme = None


def render(client, path="/codeeditor/", layout="default", theme=None):
    """GET `path` under a layout/theme; return the parsed <body> with
    <style> and <script> stripped, so assertions see only markup."""
    codeeditor.app.config.code_editor.layout = layout
    codeeditor.app.config.code_editor.theme = theme
    response = client.get(path)
    assert response.status_code == 200
    soup = bs4.BeautifulSoup(response.text, "html.parser")
    for tag in soup.find_all(["style", "script"]):
        tag.decompose()
    return soup.body


def position(body, node):
    """Document-order index of `node` within `body`."""
    return list(body.descendants).index(node)


class TestLayouts:

    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_dom_order_matches_layout(self, client, layout):
        body = render(client, layout=layout)
        window = body.select_one(f".codeeditor-app.layout-{layout}")
        assert window is not None
        sidebar = window.select_one(".codeeditor-sidebar")
        editor = window.select_one(".ce-editor")
        status = window.select_one(".ce-statusbar")
        if layout == "sidebar_right":
            assert position(body, editor) < position(body, sidebar)
        else:
            assert position(body, sidebar) < position(body, editor)
        # The status bar is the last thing in the window in every layout.
        assert position(body, status) > max(position(body, sidebar), position(body, editor))
        assert window.find_all(recursive=False)[-1] is status

    @pytest.mark.parametrize("layout", LAYOUTS)
    @pytest.mark.parametrize("path", ["/codeeditor/", "/codeeditor/developing", "/codeeditor/script.py"])
    def test_no_hardcoded_colour_utilities(self, client, layout, path):
        codeeditor.app.config.code_editor.layout = layout
        html = client.get(path).text
        assert HARDCODED_COLOUR_UTILITIES.findall(html) == []

    def test_component_styles_use_tokens_only(self):
        for style in (codeeditor._COMPONENT_STYLES, codeeditor._LAYOUT_STYLES, codeeditor._DIALOG_STYLES):
            css = to_xml(style)
            assert re.findall(r"#[0-9a-fA-F]{3,8}\b", css) == []
            assert re.findall(r"\brgba?\(", css) == []

    @pytest.mark.parametrize("theme", THEMES)
    def test_renders_under_every_theme(self, client, theme):
        body = render(client, "/codeeditor/script.py", theme=theme)
        assert body.select_one("#editor") is not None


class TestExplorer:

    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_section_header_and_labelled_actions(self, client, layout):
        sidebar = render(client, layout=layout).select_one(".codeeditor-sidebar")
        assert sidebar.select_one("h2.ce-pane-title").get_text(strip=True) == "Explorer"
        # UI question: "Two buttons labeled 'New File' and 'New Folder' at the
        # top of the sidebar" -- labels stay visible, ahead of the tree.
        labels = [b.get_text(strip=True) for b in sidebar.select(".ce-explorer-actions > button")]
        assert labels == ["New File", "New Folder"]
        tree = sidebar.select_one(".codeeditor-tree")
        assert position(sidebar, sidebar.select_one(".ce-explorer-actions")) < position(sidebar, tree)

    def test_icons_are_decorative(self, client):
        body = render(client)
        icons = body.select(".codeeditor-app i")
        assert icons
        assert all(i.get("aria-hidden") == "true" for i in icons)
        # Icons never leak into the accessible text of a row.
        names = [a.get_text() for a in body.select(".codeeditor-tree a")]
        assert "basic_imports.py" in names

    def test_top_level_entries_unchanged(self, client):
        tree = render(client).select_one(".codeeditor-tree")
        files = [a.get_text(strip=True) for a in tree.find_all("div", class_="ce-file", recursive=False)]
        folders = [b.get_text() for b in tree.select(":scope > .folder-container > .tree-row .folder-name")]
        assert files == ["basic_imports.py", "script.py", "untitled_c.c"]
        assert folders == ["developing", "empty_folder"]

    def test_folder_row_keeps_toggle_hooks(self, client):
        row = render(client).select_one(".folder-container > .tree-row")
        # The DOMContentLoaded script finds the row with `.flex` and reads
        # `data-path`; the chevron is `.folder-icon` and reports its state.
        assert "flex" in row["class"]
        assert row["data-path"] == "developing"
        assert row.select_one(".folder-icon")["aria-expanded"] == "false"

    def test_only_the_open_file_is_selected(self, client):
        body = render(client, "/codeeditor/developing/simple_python.py")
        selected = body.select(".codeeditor-tree .is-selected")
        assert len(selected) == 1
        link = selected[0].select_one("a")
        assert link.get_text() == "simple_python.py"
        assert link["aria-current"] == "page"


class TestEditorGroup:

    def test_empty_state_header(self, client):
        editor = render(client).select_one(".ce-editor")
        header = editor.select_one(".ce-editor-header")
        assert header.select_one("h2").get_text(strip=True) == "No file selected"
        textarea = editor.select_one("textarea#editor")
        assert textarea.has_attr("disabled")
        assert textarea.get_text() == codeeditor.app.config.code_editor.welcome_message

    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_tab_shows_open_file(self, client, layout):
        editor = render(client, "/codeeditor/developing/simple_python.py", layout=layout).select_one(".ce-editor")
        tab = editor.select_one("#tab-container .editor-tab.is-active")
        assert tab.select_one("span").get_text() == "simple_python.py"
        actions = [b.get_text(strip=True) for b in editor.select(".ce-tabs .ce-tab-actions button")]
        assert actions == ["Save", "Delete"]
        assert editor.select_one("[role=button][aria-label]").get_text(strip=True).startswith("developing/simple_python.py")

    def test_folder_view_keeps_delete(self, client):
        header = render(client, "/codeeditor/developing").select_one(".ce-editor-header")
        assert header.select_one("h2").get_text(strip=True) == "Folder: developing"
        assert [b.get_text(strip=True) for b in header.select("button")] == ["Delete Folder"]

    @pytest.mark.parametrize("path", ["/codeeditor/", "/codeeditor/developing", "/codeeditor/script.py"])
    def test_ids_and_options_unchanged(self, client, path):
        body = render(client, path)
        cfg = codeeditor.app.config.code_editor
        mode = body.select_one("select#mode-selector")
        theme = body.select_one("select#theme-selector")
        assert mode["name"] == "mode-selector" and theme["name"] == "theme-selector"
        assert sorted(o["value"] for o in mode.select("option")) == sorted(cfg.list_of_modes)
        assert sorted(o["value"] for o in theme.select("option")) == sorted(cfg.list_of_themes)
        assert [lbl.get_text() for lbl in body.select(".ce-field label")] == ["Language: ", "Theme: "]
        assert len(body.select("#editor")) == 1


class TestStatusBar:

    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_navigation_links(self, client, layout):
        status = render(client, layout=layout).select_one(".ce-statusbar")
        links = {a.get_text(): a["href"] for a in status.select("a")}
        assert links == {"Return to List of Apps": "/", "Code Editor Index Page": "/codeeditor"}

    def test_default_keeps_pickers_top_right(self, client):
        # UI questions built from the default screenshot place the Language /
        # Theme dropdowns "in the top-right area of the main editor pane".
        body = render(client, layout="default")
        header = body.select_one(".ce-editor .ce-editor-header")
        assert header.select_one("#mode-selector") is not None
        assert header.select_one("#theme-selector") is not None
        assert body.select_one(".ce-statusbar select") is None

    @pytest.mark.parametrize("layout", ["sidebar_right", "top_tree"])
    def test_other_layouts_move_pickers_to_status_bar(self, client, layout):
        body = render(client, layout=layout)
        status = body.select_one(".ce-statusbar")
        assert status.select_one("#mode-selector") is not None
        assert status.select_one("#theme-selector") is not None
        assert body.select_one(".ce-editor-header select") is None

    def test_file_facts(self, client):
        status = render(client, "/codeeditor/script.py").select_one(".ce-statusbar")
        facts = [s.get_text() for s in status.select("span.ce-status-item")]
        assert facts == ["Spaces: 4", "UTF-8"]
