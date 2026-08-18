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

def test_editor_resolves_to_the_vscode_theme():
    from src.open_apps.theme import resolve_theme

    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config")
    theme = resolve_theme(config.apps, "code_editor")
    assert theme["name"] == "vscode_dark"
    assert theme["tokens"]["color-bg"] == "#1e1e1e"
    assert theme["tokens"]["color-surface"] == "#252526"


def test_per_app_theme_does_not_restyle_other_apps():
    """The editor opts in; todo keeps the global default."""
    from src.open_apps.theme import resolve_theme

    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config")
    assert resolve_theme(config.apps, "todo")["name"] == "default"


def test_default_font_is_monospace():
    """A proportional font (the old "Arial") does not read as a code editor."""
    from src.open_apps.theme import resolve_theme

    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config")
    font = resolve_theme(config.apps, "code_editor")["tokens"]["font-family"]
    assert "monospace" in font.lower()


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
    from src.open_apps.theme import resolve_theme

    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config")
    tokens = resolve_theme(config.apps, "code_editor")["tokens"]
    assert tokens["color-surface"] != tokens["color-bg"]


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
    assert "window.applyTheme" in editor_html


def test_theme_selector_reaches_the_shim(editor_html):
    assert "editor.setOption('theme', this.value)" in editor_html


def test_every_selectable_theme_has_its_tokens_embedded(editor_html):
    """The selector must not offer a theme the page cannot render offline."""
    import json as _json
    import re as _re

    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config")
    offered = list(config.apps.code_editor.list_of_themes)

    match = _re.search(r"window\.OPENAPPS_THEMES = (\{.*?\});", editor_html, _re.S)
    assert match, "theme palettes were not embedded in the page"
    embedded = _json.loads(match.group(1))

    assert set(embedded) == set(offered)
    for name, tokens in embedded.items():
        assert tokens.get("color-bg"), f"{name} embedded with no colour tokens"


def test_theme_switch_needs_no_reload_or_network(editor_html):
    """Swapping is setProperty calls on :root -- no fetch, no reload."""
    assert "root.style.setProperty" in editor_html
    assert "window.applyTheme(this.value)" in editor_html


def test_configured_theme_is_applied_server_side(editor_html):
    """First paint must already be themed, not flash then correct."""
    assert "--color-bg: #1e1e1e;" in editor_html


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


# ---------------------------------------------------------------------------
# no_egress: the page must fetch nothing at all
# ---------------------------------------------------------------------------

def test_no_egress_flag_is_off_by_default():
    """Flipping it changes the look on a host that has egress."""
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config")
    assert config.apps.code_editor.no_egress is False


def test_default_page_still_declares_its_cdn_dependencies(editor_html):
    """Guards the premise of the test below."""
    assert "https://" in editor_html


def test_no_egress_page_has_zero_external_references(tmp_path_factory):
    """Rendered with no_egress, nothing may point off-host."""
    import re as _re
    import subprocess
    import sys
    import textwrap

    # Run in a subprocess: the app and its DBs are process-wide singletons and
    # are already configured by the module fixture above.
    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from hydra import compose, initialize
        from starlette.testclient import TestClient
        from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task

        logs = sys.argv[1]
        with initialize(version_base=None, config_path="config"):
            cfg = compose(
                config_name="config",
                overrides=[f"logs_dir={logs}", "apps.code_editor.no_egress=true"],
            )
        Path(cfg.logs_dir).mkdir(parents=True, exist_ok=True)
        Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
        initialize_routes_and_configure_task(cfg.apps)
        print(TestClient(app).get("/codeeditor/").text)
        """
    )
    logs = str(tmp_path_factory.mktemp("noegress"))
    out = subprocess.run(
        [sys.executable, "-c", script, logs],
        capture_output=True, text=True, cwd=".",
    )
    assert out.returncode == 0, out.stderr[-2000:]
    html = out.stdout

    # Same-origin absolute links are fine (TestClient renders its own base URL
    # as http://testserver/...); what must be gone is any third-party host.
    urls = _re.findall(r'(?:src|href)="((?://|https?://)[^"]*)"', html)
    external = [u for u in urls if "testserver" not in u]
    assert not external, f"no_egress page still fetches: {external}"
    # And it is still themed, i.e. the tokens carried the look.
    assert "--color-bg: #1e1e1e;" in html
    assert "width: 16.666667%" in html


# ---------------------------------------------------------------------------
# File tree icons
# ---------------------------------------------------------------------------

def test_rows_render_inline_svg_icons(editor_html):
    assert "<svg" in editor_html
    assert 'class="row-icon"' in editor_html


def test_no_unicode_triangle_carets_remain(editor_html):
    """The old caret was a filled U+25B6/U+25BC, blunt and over-angled."""
    assert "▶" not in editor_html
    assert "▼" not in editor_html


def test_chevron_rotates_instead_of_swapping_glyphs(editor_html):
    assert ".folder-row.is-expanded .folder-icon" in editor_html
    assert "rotate(90deg)" in editor_html
    assert "classList.toggle('is-expanded'" in editor_html


def test_folder_icons_are_not_buttons(editor_html):
    """They used to be Button(onclick="") wrappers, which put an unnamed
    button in the accessibility tree for every folder -- noise the agent has
    to read past."""
    import re as _re

    buttons = _re.findall(r"<button[^>]*>(.*?)</button>", editor_html, _re.S)
    for body in buttons:
        if "<svg" in body:
            assert body.strip() != "", "icon-only button found in the tree"
            # An icon-only button has no accessible name at all.
            text = _re.sub(r"<[^>]+>", "", body).strip()
            assert text, "button whose only content is an icon has no name"


def test_file_icon_does_not_change_the_links_accessible_name(editor_html):
    """The svg is aria-hidden, so the link's name stays the bare filename."""
    import re as _re

    links = _re.findall(r'<a[^>]*class="row-link[^"]*"[^>]*>(.*?)</a>', editor_html, _re.S)
    assert links, "no file links rendered"
    for body in links:
        assert 'aria-hidden="true"' in body
        text = _re.sub(r"<[^>]+>", "", body).strip()
        assert text and "<" not in text
