"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Tests for the Code Editor's appearance layer.

Two things are easy to break here and invisible when they break:

1. Theme swapping. ``code_editor.highlight`` is off by default, so there is no
   CodeMirror on the page and the editor shim owns ``setOption``. That shim's
   ``setOption`` used to be an empty function, which made the theme selector a
   decorative dropdown -- it fired, hit the no-op, and nothing changed.

2. CDN independence. Tailwind, DaisyUI and CodeMirror are all loaded from
   third-party CDNs. On a host without egress none of them arrive, and
   anything that depends on a Tailwind utility class silently disappears --
   the sidebar loses its width and the whole page renders as unstyled HTML.
   The inline <style> block is the only styling guaranteed to be present, so
   the layout and colours have to live there.
"""

import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.codeeditor_app import main as codeeditor_main
from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task


@pytest.fixture(scope="module")
def client(tmpdir_factory):
    # App and DBs are process-wide singletons; reuse if already configured.
    if codeeditor_main.current_dir is None:
        logs_dir = str(tmpdir_factory.getbasetemp())
        with initialize(version_base=None, config_path="../config/"):
            config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
        Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
        Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
        initialize_routes_and_configure_task(config.apps)
    return TestClient(app)


@pytest.fixture(scope="module")
def editor_html(client):
    response = client.get("/codeeditor/")
    assert response.status_code == 200
    return response.text


@pytest.fixture(scope="module")
def editor_html_for_open_file(client):
    """The view an agent actually works in, with a file selected."""
    response = client.get("/codeeditor/script.py")
    assert response.status_code == 200
    return response.text


# ---------------------------------------------------------------------------
# The default appearance is the VS Code palette
# ---------------------------------------------------------------------------

def test_default_appearance_is_vscode_dark():
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config")
    ce = config.apps.code_editor
    assert ce.main_background_color == "#1e1e1e"
    assert ce.sidebar_background_color == "#252526"
    assert ce.fontcolor == "#d4d4d4"


def test_default_font_is_monospace():
    """A proportional font (the old "Arial") does not read as a code editor."""
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config")
    assert "monospace" in config.apps.code_editor.font.lower()


def test_palette_reaches_the_rendered_page(editor_html):
    for colour in ["#1e1e1e", "#252526", "#d4d4d4", "#3c3c3c"]:
        assert colour in editor_html, f"{colour} missing from rendered CSS"


# ---------------------------------------------------------------------------
# Sidebar is visually distinct from the editor pane
# ---------------------------------------------------------------------------

def test_sidebar_has_its_own_surface(editor_html):
    """Both panes carry .main-content; without this they render as one slab."""
    assert "sidebar main-content" in editor_html
    assert "--sidebar-bg-color" in editor_html
    assert ".sidebar {" in editor_html


def test_sidebar_colour_differs_from_editor_pane():
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config")
    ce = config.apps.code_editor
    assert ce.sidebar_background_color != ce.main_background_color


def test_file_rows_carry_semantic_classes(editor_html):
    """Hover/current cues must not depend on Tailwind's hover:bg-gray-700."""
    assert "file-row" in editor_html
    assert ".sidebar .file-row:hover" in editor_html


def test_row_colours_do_not_depend_on_tailwind(editor_html):
    """Tailwind's CDN injects at runtime and would win the cascade, so the
    editor would look different with and without egress."""
    # Scan class attributes only -- the stylesheet's comments mention these
    # utility names, and matching raw text would flag those instead.
    classes = " ".join(re.findall(r'class="([^"]*)"', editor_html))
    assert "hover:bg-gray-700" not in classes
    assert "bg-blue-800" not in classes


def test_current_file_is_marked(editor_html_for_open_file):
    assert "is-current" in editor_html_for_open_file


# ---------------------------------------------------------------------------
# Theme swapping works with highlight off (no CodeMirror on the page)
# ---------------------------------------------------------------------------

def test_highlight_is_off_so_no_codemirror_cdn_dependency():
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config")
    assert config.apps.code_editor.highlight is False


def test_set_option_is_no_longer_a_no_op(editor_html):
    """The regression: setOption used to be `function() {}`."""
    assert "setOption: function() {}" not in editor_html
    assert "applyLocalTheme" in editor_html


def test_theme_selector_reaches_the_shim(editor_html):
    assert "editor.setOption('theme', this.value)" in editor_html


def test_local_theme_classes_exist_for_every_locally_backed_theme(editor_html):
    for theme in ["vscode-dark", "vscode-light", "monokai", "high-contrast"]:
        assert f"textarea#editor.theme-{theme}" in editor_html, (
            f"{theme} is offered in the selector but has no local CSS class, "
            "so selecting it would do nothing with highlight off"
        )


def test_configured_theme_is_applied_on_load(editor_html):
    assert "applyLocalTheme('vscode-dark')" in editor_html


# ---------------------------------------------------------------------------
# The page survives an unreachable CDN
# ---------------------------------------------------------------------------

def test_layout_utilities_have_local_fallbacks(editor_html):
    """Tailwind is a CDN fetch; these are the utilities the app depends on."""
    for rule in [".w-1\\/6", ".w-5\\/6", ".flex {", ".overflow-y-auto"]:
        assert rule in editor_html, f"no local fallback for {rule}"


def test_sidebar_width_survives_without_tailwind(editor_html):
    assert "width: 16.666667%" in editor_html


# ---------------------------------------------------------------------------
# Legacy appearance presets still work
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "preset", ["default", "dark_theme", "black_and_white", "colorblind_access"]
)
def test_legacy_presets_still_compose(preset):
    """They predate the new keys and must fall back, not raise."""
    with initialize(version_base=None, config_path="../config/"):
        config = compose(
            config_name="config",
            overrides=[f"apps/code_editor/appearance={preset}"],
        )
    assert "sidebar_background_color" not in config.apps.code_editor
