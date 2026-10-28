"""The agent's goto must not reach the state endpoints rewards read.

Runs the real action code in a real browser, because the bypasses this guards
against (`/todo_all/.`, `/todo_all/%2e`, `/x/../todo_all`) only exist in how
Chromium normalises URLs.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from browsergym.core.action.base import execute_python_code

from open_apps.agent.utils import CustomActionSetArgs


@pytest.fixture(scope="module")
def server():
    requested = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>ok</body></html>")

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", requested
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as error:
            pytest.skip(f"chromium unavailable: {error}")
        yield browser
        browser.close()


@pytest.fixture
def run(server, browser):
    base, requested = server
    page = browser.new_page()
    page.goto(base + "/")
    requested.clear()
    action_set = CustomActionSetArgs(subsets=("custom",)).make_action_set()

    def _run(url):
        code = action_set.to_python_code(f"goto({url.replace('BASE', base)!r})")
        execute_python_code(code, page, lambda _: None, lambda _: None)
        return list(requested)

    yield _run
    page.close()


@pytest.mark.parametrize(
    "url",
    [
        "BASE/todo_all",
        "/calendar_all",
        "/messages_all/",
        "/onlineshop_all?include_catalog=true",
        "BASE/openbanking_all#x",
        "/desktop%5Fall",
        "/todo_all/.",
        "/todo_all/%2e",
        "/x/../todo_all/.",
        "/x/%2e%2e/todo_all",
        "/x\\..\\todo_all",
        "/todo_\tall",
        "/TODO_ALL",
        "/maps/landmarks",
        "/maps/landmarks/",
        "/maps/x/../landmarks",
        "data:text/html,<iframe src='BASE/todo_all'></iframe>",
        "javascript:location='/todo_all'",
    ],
)
def test_goto_refuses_state_endpoints(run, url):
    with pytest.raises(ValueError, match="not allowed"):
        run(url)


@pytest.mark.parametrize(
    "url, path",
    [
        ("BASE/todo", "/todo"),
        ("/calendar?view=all", "/calendar?view=all"),
        ("/maps", "/maps"),
        ("/", "/"),
    ],
)
def test_goto_allows_app_pages(run, url, path):
    assert run(url) == [path]
