"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""The static-file route serves /assets/ and nothing else.

It used to match any URL ending in a static extension anywhere on the server,
so `/codeeditor/demo.yaml` was answered by the static route -- with an empty
200, since no such asset exists -- and never reached the code editor.
"""

from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("static_route")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


def test_assets_are_still_served(client):
    response = client.get("/assets/vendor/htmx-2.0.4.min.js")
    assert response.status_code == 200
    assert response.content


@pytest.mark.parametrize("name", ["notes.yaml", "app.js", "site.css", "readme.txt"])
def test_code_editor_files_with_static_extensions_open_in_the_editor(client, name):
    saved = client.post(f"/codeeditor/save/{name}", json={"content": "key: value\n"})
    assert saved.json() == {"success": True}
    page = client.get(f"/codeeditor/{name}")
    assert page.status_code == 200
    assert 'id="editor"' in page.text
    assert "key: value" in page.text


@pytest.mark.parametrize("path", [
    "/assets/nope.css",
    "/assets/../start_page/main.py.js",
    "/no-such-page.css",
])
def test_missing_static_paths_are_404_not_empty_200(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("name", ["pdf-6.4.299.min.js", "pdf.worker-6.4.299.min.js"])
def test_pdfjs_is_served_as_javascript(client, name):
    """pdf.js loads as an ES module and spawns its worker from this URL; a
    browser refuses either one unless it arrives with a JavaScript MIME type."""
    response = client.get(f"/assets/vendor/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] in {
        "text/javascript", "application/javascript",
    }
    assert len(response.content) > 100_000
