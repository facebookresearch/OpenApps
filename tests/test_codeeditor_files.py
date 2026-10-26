"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The code editor's workspace is Files' Code folder -- the same directory, not
a copy -- and the editor can reveal what is open in Files.

``/files_all`` leaves Code out, so ``/codeeditor_all`` stays the only state
source for those files: an edit in the editor must not show up as a files
change, or every existing code task would see a spurious diff.
"""

import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps import files_store
from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("codeeditor_files")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


def reveal_href(body: str) -> str | None:
    link = re.search(r'<a[^>]*id="ce-reveal-in-files"[^>]*>', body)
    if not link:
        return None
    return re.search(r'href="([^"]*)"', link.group(0)).group(1).replace("&amp;", "&")


class TestRevealInFiles:
    def test_a_file_reveals_its_folder_with_the_file_selected(self, client):
        body = client.get("/codeeditor/developing/simple_python.py").text
        assert reveal_href(body) == "/files/Code/developing?selected=simple_python.py"
        assert "Reveal in Files" in body

    def test_following_reveal_lands_with_the_file_selected(self, client):
        href = reveal_href(client.get("/codeeditor/developing/simple_python.py").text)
        page = client.get(href).text
        row = re.search(r'<[^>]*aria-selected="true"[^>]*>(?:(?!</tr>).)*', page, re.S)
        assert row and "simple_python.py" in row.group(0)

    def test_a_top_level_file_reveals_the_code_folder(self, client):
        assert reveal_href(client.get("/codeeditor/script.py").text) == "/files/Code?selected=script.py"

    def test_a_folder_reveals_itself(self, client):
        body = client.get("/codeeditor/developing/folder_in_folder").text
        assert reveal_href(body) == "/files/Code/developing/folder_in_folder"

    def test_an_unsaved_file_has_nothing_to_reveal(self, client):
        response = client.get("/codeeditor/Untitled-1")
        assert response.status_code == 200
        assert reveal_href(response.text) is None
        assert ">None<" not in response.text

    def test_names_are_url_encoded(self, client):
        client.post("/codeeditor/save/my notes/a b.py", json={"content": "x = 1\n"})
        body = client.get("/codeeditor/my notes/a b.py").text
        assert reveal_href(body) == "/files/Code/my%20notes?selected=a%20b.py"


class TestSharedWorkspace:
    def test_the_code_folder_is_the_editor_workspace(self, client):
        client.post("/codeeditor/save/developing/simple_python.py",
                    json={"content": "print('edited')\n"})
        assert files_store.read("Code/developing/simple_python.py") == b"print('edited')\n"
        names = {e.name for e in files_store.list_folder("Code")}
        assert {"developing", "script.py", "basic_imports.py"} <= names

    def test_editing_code_leaves_files_state_byte_identical(self, client):
        before = client.get("/files_all").text
        client.post("/codeeditor/save/script.py", json={"content": "print('changed')\n"})
        client.post("/codeeditor/save/brand_new.py", json={"content": "pass\n"})
        client.post("/codeeditor/create_folder/new_dir")
        client.post("/codeeditor/delete/basic_imports.py")
        assert client.get("/files_all").text == before
