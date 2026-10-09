"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Tests that the Code Editor's Save button can actually reach disk.

Regression coverage for the bug where the emitted editor binding was
``var editor = (document.getElementById('editor'), {...});`` — the comma
operator, which bound ``editor`` to the options object instead of an editor.
``editor.getValue()`` in the Save handler then threw a TypeError, so the POST
never fired and ``EditFileTask`` could never earn reward.
"""

import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.codeeditor_app import main as codeeditor_main
from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task
from open_apps.tasks.tasks import EditFileTask


@pytest.fixture(scope="module")
def client(tmpdir_factory):
    # The app and its databases are process-wide singletons, so running setup
    # again on top of a module that already configured them re-populates the
    # shared DBs and raises. Reuse whatever is already wired up in that case.
    if codeeditor_main.current_dir is None:
        logs_dir = str(tmpdir_factory.getbasetemp())
        with initialize(version_base=None, config_path="../config/"):
            config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
        Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
        Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
        initialize_routes_and_configure_task(config.apps)
    return TestClient(app)


def editor_script(html: str) -> str:
    """Return the inline ``<script>`` block that binds the page's ``editor``."""
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.DOTALL)
    binding = [b for b in blocks if "var editor" in b]
    assert binding, "no editor binding found on the page"
    return binding[0]


class TestEditorBinding:
    """The page-global ``editor`` must expose the API the page calls."""

    def test_binding_is_not_the_options_object(self, client):
        """The comma-operator form must never be emitted again."""
        html = client.get("/codeeditor/script.py").text
        assert "var editor =  (document.getElementById" not in html

    def test_binding_exposes_getvalue_backed_by_the_textarea(self, client):
        """Save reads ``editor.getValue()``; it must read the textarea."""
        script = editor_script(client.get("/codeeditor/script.py").text)
        assert "getValue" in script
        assert "editorTextarea.value" in script
        assert "document.getElementById('editor')" in script

    def test_save_handler_calls_getvalue(self, client):
        html = client.get("/codeeditor/script.py").text
        assert "const content = editor.getValue();" in html
        assert "fetch('/codeeditor/save/script.py'" in html

    def test_highlight_mode_uses_codemirror(self, monkeypatch):
        """With highlighting on the binding is a real CodeMirror instance."""
        monkeypatch.setattr(
            codeeditor_main.app.config.code_editor, "highlight", True, raising=False
        )
        binding = codeeditor_main.editor_binding("{mode: 'python'}")
        assert binding.startswith(
            "var editor = CodeMirror.fromTextArea(document.getElementById('editor')"
        )

    def test_folder_and_index_views_also_bind_an_editor(self, client):
        """The read-only views drive ``editor.setOption`` from the selectors."""
        for url in ("/codeeditor/", "/codeeditor/developing"):
            script = editor_script(client.get(url).text)
            assert "setOption" in script


class TestEditorOptions:
    """The CodeMirror options must name a stylesheet that is on the page.

    Regression coverage for the themes/layouts merge, which kept the extracted
    ``editor_options`` string but dropped the ``current_editor_theme()`` call
    it had been rewritten to use, leaving ``theme: '{app.config.code_editor
    .theme}'``. That field is now the *shared* design-token theme and defaults
    to null, so every view initialised CodeMirror with ``theme: 'None'`` -- an
    unstyled editor, and no error anywhere.
    """

    @pytest.fixture
    def highlighting(self, client, monkeypatch):
        monkeypatch.setattr(
            codeeditor_main.app.config.code_editor, "highlight", True, raising=False
        )
        return client

    def theme_options(self, html: str) -> list:
        return re.findall(r"\n\s*theme: '([^']*)'", html)

    @pytest.mark.parametrize(
        "url", ["/codeeditor/", "/codeeditor/script.py", "/codeeditor/developing"]
    )
    def test_view_requests_the_active_editor_theme(self, highlighting, url):
        emitted = self.theme_options(highlighting.get(url).text)
        assert emitted == [codeeditor_main.current_editor_theme()], url
        assert "None" not in emitted, url

    def test_theme_is_one_the_page_loaded_a_stylesheet_for(self, highlighting):
        cfg = codeeditor_main.app.config.code_editor
        available = {
            *cfg.list_of_themes,
            *codeeditor_main._as_dict(
                getattr(cfg, "editor_theme_by_tone", None)
            ).values(),
            cfg.editor_theme,
        }
        assert codeeditor_main.current_editor_theme() in available


class TestSaveRoundTrip:
    """A save must land on disk and be visible to the reward endpoint."""

    def test_saved_content_reaches_codeeditor_all(self, client):
        original = client.get("/codeeditor/script.py")
        assert original.status_code == 200

        initial_state = {"codeeditor": client.get("/codeeditor_all").json()}
        initial_content = EditFileTask(
            goal="", file_path="script.py", required_fragment="x"
        )._find_file_content(initial_state["codeeditor"], "script.py")
        assert initial_content is not None
        assert "# Reviewed by Bob" not in initial_content

        response = client.post(
            "/codeeditor/save/script.py",
            json={"content": initial_content + "# Reviewed by Bob\n"},
        )
        assert response.json() == {"success": True}

        current_state = {"codeeditor": client.get("/codeeditor_all").json()}
        task = EditFileTask(
            goal="Open 'script.py' in the Code Editor, add the line "
            "'# Reviewed by Bob' as a comment, and save the file.",
            file_path="script.py",
            required_fragment="# Reviewed by Bob",
        )
        assert task.check_if_task_is_complete(initial_state, current_state)
