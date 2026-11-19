"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The code editor's offline syntax highlighting (`code_editor.syntax_highlight`).

What has to hold, in order of how badly an eval breaks if it does not:

* the textarea is still THE editor -- same id, aria attributes and value --
  so Save, `fill()` and the accessibility tree are unchanged; the coloured
  copy is aria-hidden;
* nothing is fetched from off the box (the highlighter is a local asset);
* the off switch, and CodeMirror's `highlight`, give back the plain textarea;
* each file opens in its own language, and every theme's colours are
  readable on its own background.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.codeeditor_app import main as codeeditor
from open_apps.theme import load_theme

bs4 = pytest.importorskip("bs4")

ASSET = (
    Path(__file__).resolve().parents[1]
    / "src/open_apps/apps/assets/js/code_highlight.js"
)
THEMES = [
    "default", "dark", "vscode_dark", "mono", "challenging_font", "colorblind",
    "solarized", "material", "bootstrap", "meta", "meta_dark",
]
SYNTAX_TOKENS = [
    "keyword", "control", "string", "number", "comment",
    "function", "type", "property", "constant", "tag",
]
EXTRA_FILES = {
    "config.yaml": "name: demo\nretries: 3\n",
    "data.json": '{"a": [1, true, null]}\n',
    "notes.txt": "plain words\n",
}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("codeeditor_highlight")
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    codeeditor.set_environment(config.apps)
    for name, content in EXTRA_FILES.items():
        (Path(codeeditor.current_dir) / name).write_text(content)
    yield TestClient(codeeditor.app)
    cfg = codeeditor.app.config.code_editor
    cfg.syntax_highlight = True
    cfg.highlight = False


def body(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return bs4.BeautifulSoup(response.text, "html.parser").body


class TestOverlayMarkup:

    def test_file_page_wraps_textarea_with_hidden_layer(self, client):
        page = body(client, "/codeeditor/script.py")
        wrap = page.select_one(".ce-code-wrap")
        assert wrap is not None
        layer = wrap.select_one("pre#editor-highlight")
        assert layer["aria-hidden"] == "true"
        assert layer.select_one("code") is not None
        # The layer comes first so the textarea paints above it.
        children = [c for c in wrap.children if getattr(c, "name", None)]
        assert [c.name for c in children][:2] == ["pre", "textarea"]

    def test_textarea_is_unchanged(self, client):
        page = body(client, "/codeeditor/script.py")
        textareas = page.select("textarea#editor")
        assert len(textareas) == 1
        textarea = textareas[0]
        assert textarea["aria-label"] == "Code editor - script.py"
        assert textarea["aria-multiline"] == "true"
        assert textarea["role"] == "textbox"
        assert textarea["wrap"] == "off"
        on_disk = (Path(codeeditor.current_dir) / "script.py").read_text()
        assert textarea.get_text() == on_disk

    def test_highlighter_is_a_local_script_before_the_binding(self, client):
        html = client.get("/codeeditor/script.py").text
        src_at = html.index(f'src="{codeeditor.HIGHLIGHT_SCRIPT_URL}"')
        bind_at = html.index("var editorTextarea")
        assert src_at < bind_at
        assert codeeditor.HIGHLIGHT_SCRIPT_URL.startswith("/assets/")

    def test_asset_exists_and_reaches_nowhere(self):
        source = ASSET.read_text()
        assert "OpenAppsHighlight" in source
        assert re.findall(r"https?://", source.split("*/", 1)[1]) == []

    @pytest.mark.parametrize("path", ["/codeeditor/", "/codeeditor/developing"])
    def test_no_layer_without_an_open_file(self, client, path):
        page = body(client, path)
        assert page.select_one(".ce-code-wrap") is None
        assert page.select_one("textarea#editor").has_attr("disabled")


class TestSwitches:

    def test_off_gives_the_plain_textarea(self, client):
        codeeditor.app.config.code_editor.syntax_highlight = False
        try:
            html = client.get("/codeeditor/script.py").text
        finally:
            codeeditor.app.config.code_editor.syntax_highlight = True
        page = bs4.BeautifulSoup(html, "html.parser")
        assert page.select_one(".ce-code-wrap") is None
        assert page.select_one("#editor-highlight") is None
        assert codeeditor.HIGHLIGHT_SCRIPT_URL not in html
        assert page.select_one(".ce-editor-body > textarea#editor") is not None

    def test_codemirror_suppresses_the_layer(self, client):
        codeeditor.app.config.code_editor.highlight = True
        try:
            assert codeeditor.syntax_overlay_enabled() is False
        finally:
            codeeditor.app.config.code_editor.highlight = False
        assert codeeditor.syntax_overlay_enabled() is True

    def test_on_by_default(self):
        with initialize(version_base=None, config_path="../config/"):
            config = compose(config_name="config")
        assert config.apps.code_editor.syntax_highlight is True
        assert config.apps.code_editor.highlight is False


class TestLanguage:

    @pytest.mark.parametrize("path,expected", [
        ("script.py", "python"),
        ("untitled_c.c", "clike"),
        ("config.yaml", "yaml"),
        ("data.json", "json"),
        ("a/b/app.TS", "javascript"),
        ("main.go", "go"),
        ("README.md", "markdown"),
        ("run.sh", "shell"),
    ])
    def test_detected_from_extension(self, client, path, expected):
        assert codeeditor.language_for(path) == expected

    def test_unknown_extension_uses_configured_mode(self, client):
        assert codeeditor.language_for("notes.txt") == codeeditor.app.config.code_editor.mode
        assert codeeditor.language_for("Makefile") == codeeditor.app.config.code_editor.mode

    @pytest.mark.parametrize("path,expected", [
        ("/codeeditor/script.py", "python"),
        ("/codeeditor/config.yaml", "yaml"),
        ("/codeeditor/untitled_c.c", "clike"),
    ])
    def test_layer_and_picker_agree(self, client, path, expected):
        page = body(client, path)
        assert page.select_one(".ce-code-wrap")["data-language"] == expected
        picked = page.select_one("#mode-selector option[selected]")
        assert picked["value"] == expected

    def test_language_missing_from_the_list_is_still_offered(self, client):
        page = body(client, "/codeeditor/data.json")
        values = [o["value"] for o in page.select("#mode-selector option")]
        assert values.count("json") == 1
        assert page.select_one("#mode-selector option[selected]")["value"] == "json"
        # CodeMirror has no json mode loaded, so it is not asked for one.
        assert "mode: 'json'" not in client.get("/codeeditor/data.json").text

    def test_picker_switches_the_layer_live(self, client):
        html = client.get("/codeeditor/script.py").text
        assert "editorHighlight.setLanguage(value)" in html
        assert "editor.setOption('mode', this.value)" in html


def _luminance(hex_colour: str) -> float:
    rgb = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class TestPalette:

    @pytest.mark.parametrize("theme", THEMES)
    def test_every_theme_defines_the_syntax_tokens(self, theme):
        tokens = load_theme(theme)["tokens"]
        missing = [t for t in SYNTAX_TOKENS if f"color-syntax-{t}" not in tokens]
        assert missing == [], f"{theme} lacks {missing}"

    @pytest.mark.parametrize("theme", THEMES)
    def test_code_is_readable_on_the_theme_background(self, theme):
        """WCAG AA (4.5:1) for everything but comments, which editors
        deliberately mute -- they still have to be visible at all."""
        tokens = load_theme(theme)["tokens"]
        bg = tokens["color-bg"].split()[0]
        for name in SYNTAX_TOKENS:
            ratio = _contrast(tokens[f"color-syntax-{name}"], bg)
            floor = 2.5 if name == "comment" else 4.5
            assert ratio >= floor, f"{theme} {name}: {ratio:.2f}:1 on {bg}"

    def test_vscode_dark_is_dark_plus(self):
        tokens = load_theme("vscode_dark")["tokens"]
        assert tokens["color-syntax-keyword"] == "#569cd6"
        assert tokens["color-syntax-string"] == "#ce9178"
        assert tokens["color-syntax-comment"] == "#6a9955"
        assert tokens["color-syntax-function"] == "#dcdcaa"
        assert tokens["color-syntax-type"] == "#4ec9b0"
        assert tokens["color-syntax-number"] == "#b5cea8"
        assert tokens["color-syntax-property"] == "#9cdcfe"


SAMPLES = {
    "python": 'import os\n@dec\ndef f(x=1.5):\n    """<b>&"""\n    return self.y  # c\n',
    "javascript": "const a = `t${x}`; // <c>\nfunction f() { return new Map(); }\n",
    "css": ".a:hover { color: #fff; width: calc(100% - 2px); }\n",
    "yaml": "---\nkey: value # c\nlist:\n  - 42\nflag: true\n",
    "markdown": "# T\n\n`c` **b** [l](u)\n```python\ndef f(): pass\n```\n",
    "shell": 'echo "$HOME" # c\nif true; then exit 1; fi\n',
    "clike": '#include <stdio.h>\nint main(void) { return 0; }\n',
    "go": 'package main\nfunc main() { s := `raw` }\n',
    "json": '{"a": [1, true, null, "<s>"]}',
    "html": '<div class="x">a &amp; <b>b</b></div><!-- c -->',
    "unknown-lang": "<script>alert(1)</script>",
}


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the tokenizer")
class TestTokenizer:
    """Runs the shipped JS under node: the coloured copy must be the text,
    exactly, or the colours drift off the characters they belong to."""

    @pytest.fixture(scope="class")
    def rendered(self):
        script = (
            "global.window = {};"
            f"require({json.dumps(str(ASSET))});"
            "const s = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            "const out = {};"
            "for (const [l, t] of Object.entries(s)) out[l] = window.OpenAppsHighlight.highlight(t, l);"
            "process.stdout.write(JSON.stringify(out));"
        )
        result = subprocess.run(
            ["node", "-e", script], input=json.dumps(SAMPLES),
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)

    @pytest.mark.parametrize("lang", sorted(SAMPLES))
    def test_text_round_trips(self, rendered, lang):
        html = rendered[lang]
        text = re.sub(r"<[^>]+>", "", html)
        text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        assert text == SAMPLES[lang]

    @pytest.mark.parametrize("lang", sorted(SAMPLES))
    def test_only_span_markup_is_emitted(self, rendered, lang):
        tags = set(re.findall(r"</?(\w+)", rendered[lang]))
        assert tags <= {"span"}

    def test_classes_are_assigned(self, rendered):
        py = rendered["python"]
        assert '<span class="tok-keyword">def</span>' in py
        assert '<span class="tok-function">f</span>' in py
        assert '<span class="tok-control">return</span>' in py
        assert '<span class="tok-comment"># c</span>' in py
        assert '<span class="tok-constant">self</span>' in py
        assert '<span class="tok-property">"a"</span>' in rendered["json"]
        assert '<span class="tok-tag">div</span>' in rendered["html"]
