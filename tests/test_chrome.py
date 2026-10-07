"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Tests for the global window chrome: title bar, dock, agent cursor.

Three layers, cheapest first:

* ``inject`` -- the string surgery, on literal documents;
* ``ChromeMiddleware`` -- on a throwaway Starlette app, so what passes through
  untouched (JSON, htmx fragments, POSTs) is checked without the real server;
* the real server -- every app page carries the chrome, the window controls
  move the transient state, and none of it leaks into ``/desktop_all``.

The cursor's motion is JavaScript and is not exercised here; what is checked
is that the element ships with the configuration the script reads, and that it
is invisible to the accessibility tree.
"""

import re
from pathlib import Path

import pytest
from fasthtml.common import to_xml
from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import OmegaConf
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from open_apps.chrome_middleware import ChromeMiddleware, ChromeParts, inject
from open_apps.theme import load_theme, render_theme_css
from open_apps.ui.chrome import AgentCursor, ChromeApp, Dock, TitleBar, chrome_parts

CONFIG_DIR = str((Path(__file__).resolve().parent.parent / "config").resolve())

APPS = [
    ChromeApp("todo", "OpenTodos", "/todo", "/assets/icons/real_icons/todo.png"),
    ChromeApp("calendar", "OpenCalendar", "/calendar", "/assets/icons/real_icons/calendar.png"),
    ChromeApp("maps", "OpenMaps", "/maps", "/assets/icons/real_icons/maps.png"),
]


def compose_chrome(*overrides: str) -> dict:
    """``apps.chrome`` as the plain dict the renderer receives."""
    if GlobalHydra.instance().is_initialized():
        GlobalHydra.instance().clear()
    with initialize_config_dir(version_base=None, config_dir=CONFIG_DIR):
        cfg = compose(config_name="config", overrides=list(overrides))
    return OmegaConf.to_container(cfg.apps.chrome, resolve=True)


def parts(cfg=None, **kw) -> ChromeParts | None:
    args = dict(
        apps=APPS,
        current="todo",
        docked=["todo", "calendar", "maps"],
        running=[],
        maximized=False,
        form_factor="desktop",
        shell_tokens_css="",
    )
    args.update(kw)
    return chrome_parts(cfg if cfg is not None else compose_chrome(), **args)


def ids(markup: str) -> set[str]:
    return set(re.findall(r'data-testid="([^"]+)"', markup))


# --------------------------------------------------------------------------
# inject
# --------------------------------------------------------------------------

DOC = "<!doctype html><html lang=\"en\"><head><title>t</title></head><body><p>hi</p></body></html>"


class TestInject:
    def test_splices_head_body_and_html_attrs(self):
        out = inject(DOC, ChromeParts(head="<style>H</style>", body="<div>B</div>", html_attrs={"data-x": "1"}))
        assert out.startswith('<!doctype html><html data-x="1" lang="en">')
        assert "<style>H</style></head>" in out
        assert "<p>hi</p><div>B</div></body>" in out

    def test_fragment_is_left_alone(self):
        fragment = "<li>just a row</li>"
        assert inject(fragment, ChromeParts(head="H", body="B")) == fragment

    def test_last_body_close_wins(self):
        # A page can carry "</body>" inside a script; the document's own is last.
        doc = "<html><head></head><body><script>var s='</body>';</script></body></html>"
        out = inject(doc, ChromeParts(body="<i>B</i>"))
        assert out.endswith("<i>B</i></body></html>")
        assert "var s='</body>'" in out

    def test_missing_head_puts_styles_ahead_of_body_markup(self):
        out = inject("<html><body>x</body></html>", ChromeParts(head="<style/>", body="<b/>"))
        assert out == "<html><body>x<style/><b/></body></html>"

    def test_attribute_values_are_escaped(self):
        out = inject(DOC, ChromeParts(html_attrs={"data-x": '"><script>'}))
        assert "<script>" not in out.split("<head>")[0]


# --------------------------------------------------------------------------
# ChromeMiddleware on a minimal app
# --------------------------------------------------------------------------


def _mini_client(render):
    async def page(request):
        return HTMLResponse(DOC)

    async def data(request):
        return JSONResponse({"ok": True})

    async def post(request):
        return HTMLResponse(DOC)

    app = Starlette(routes=[Route("/page", page), Route("/data", data), Route("/post", post, methods=["POST"])])
    app.add_middleware(ChromeMiddleware, render=render)
    return TestClient(app)


class TestMiddleware:
    @pytest.fixture
    def client(self):
        return _mini_client(lambda path: ChromeParts(body=f"<b>{path}</b>"))

    def test_full_page_gets_chrome_and_correct_length(self, client):
        response = client.get("/page")
        assert "<b>/page</b></body>" in response.text
        assert int(response.headers["content-length"]) == len(response.content)

    def test_json_passes_through(self, client):
        assert client.get("/data").json() == {"ok": True}

    def test_htmx_fragment_request_passes_through(self, client):
        assert "<b>" not in client.get("/page", headers={"HX-Request": "true"}).text

    def test_boosted_request_is_a_navigation(self, client):
        response = client.get("/page", headers={"HX-Request": "true", "HX-Boosted": "true"})
        assert "<b>/page</b>" in response.text

    def test_post_passes_through(self, client):
        assert "<b>" not in client.post("/post").text

    def test_render_failure_serves_the_page_without_chrome(self):
        def broken(path):
            raise RuntimeError("boom")

        response = _mini_client(broken).get("/page")
        assert response.status_code == 200
        assert response.text == DOC


# --------------------------------------------------------------------------
# Components and assembly
# --------------------------------------------------------------------------


class TestTitleBar:
    def test_mac_controls_are_named_posted_forms_in_mac_order(self):
        markup = to_xml(TitleBar(APPS[0], style="mac"))
        labels = re.findall(r'aria-label="(\w+) OpenTodos"', markup)
        assert labels == ["Close", "Minimize", "Maximize"]
        for action in ("close", "minimize", "maximize"):
            assert f'action="/chrome/{action}/todo"' in markup
        assert 'method="post"' in markup

    def test_windows_order_and_restore_when_maximized(self):
        markup = to_xml(TitleBar(APPS[0], style="windows", maximized=True))
        assert re.findall(r'aria-label="(\w+) OpenTodos"', markup) == ["Minimize", "Restore", "Close"]
        assert "is-windows" in markup


class TestDock:
    def test_items_hrefs_current_and_shortcuts(self):
        markup = to_xml(Dock(APPS, docked=["todo", "calendar"], running=[], current="calendar"))
        assert {"dock-desktop", "dock-todo", "dock-calendar", "dock-launcher"} <= ids(markup)
        assert re.search(r'<a[^>]*aria-current="page"[^>]*data-testid="dock-calendar"', markup)
        assert re.search(r'aria-keyshortcuts="Alt\+0"[^>]*data-testid="dock-desktop"', markup)
        assert re.search(r'aria-keyshortcuts="Alt\+2"[^>]*data-testid="dock-calendar"', markup)

    def test_running_app_without_a_slot_follows_a_separator(self):
        markup = to_xml(Dock(APPS, docked=["todo"], running=["maps"], current="maps", launcher=False))
        assert markup.index("dock-todo") < markup.index("oa-dock-sep") < markup.index('data-testid="dock-maps"')
        assert re.search(r'data-running="true"[^>]*data-testid="dock-maps"', markup)

    def test_unlabelled_dock_keeps_names_in_the_tree(self):
        markup = to_xml(Dock(APPS, docked=["todo"], running=[], current=None, labels=False))
        # The all-apps panel is a grid with room for names; only the row is bare.
        row = markup[: markup.index('id="oa-dock-panel"')]
        assert "oa-dock-label" not in row
        assert 'aria-label="OpenTodos"' in row

    def test_launcher_panel_lists_every_app_and_starts_closed(self):
        markup = to_xml(Dock(APPS, docked=[], running=[], current=None))
        panel_tag = re.search(r'<div[^>]*id="oa-dock-panel"[^>]*>', markup).group(0)
        assert re.search(r"\shidden(\s|>|=)", panel_tag)
        assert {f"dock-panel-{a.key}" for a in APPS} <= ids(markup)
        assert 'aria-expanded="false"' in markup


class TestCursor:
    def test_hidden_from_the_tree_and_configured_by_attributes(self):
        markup = to_xml(AgentCursor(show="always", glide_ms=180, click_ripple=False, label="Agent"))
        assert 'aria-hidden="true"' in markup
        assert 'data-show="always"' in markup and 'data-glide="180"' in markup
        assert 'data-ripple="false"' in markup and "Agent" in markup

    def test_unknown_values_fall_back(self):
        markup = to_xml(AgentCursor(show="sometimes", style="neon", glide_ms=-5))
        assert 'data-show="auto"' in markup and "is-glow" in markup and 'data-glide="0"' in markup


class TestChromeParts:
    def test_app_page_gets_titlebar_dock_cursor_and_reserved_space(self):
        result = parts()
        assert {"window-close", "dock", "agent-cursor"} <= ids(result.body)
        assert result.html_attrs["data-oa-titlebar"] == "mac"
        assert result.html_attrs["data-oa-dock"] == "reserve"
        assert result.html_attrs["data-oa-app"] == "todo"
        assert result.head.startswith("<style") and "oa-chrome-js" in result.body

    def test_desktop_has_no_titlebar_and_can_float_the_dock(self):
        result = parts(current=None, dock_overlay=True)
        assert 'id="oa-titlebar"' not in result.body
        assert result.html_attrs["data-oa-dock"] == "overlay"

    def test_none_variant_renders_nothing(self):
        assert parts(compose_chrome("apps/chrome=none")) is None

    def test_windows_variant(self):
        assert parts(compose_chrome("apps/chrome=windows")).html_attrs["data-oa-titlebar"] == "windows"

    def test_phone_has_no_titlebar(self):
        result = parts(form_factor="phone")
        assert 'id="oa-titlebar"' not in result.body
        assert "data-oa-titlebar" not in result.html_attrs

    def test_maximized_hands_back_the_dock_space(self):
        assert "data-oa-maximized" in parts(maximized=True).html_attrs

    def test_cursor_colours_come_from_tokens_not_hexes(self):
        body = parts(compose_chrome("apps.chrome.cursor.colors=[color-primary,color-accent]")).body
        assert "--oa-cursor-a: var(--color-primary," in body

    def test_hostile_token_name_is_dropped(self):
        body = parts(compose_chrome("apps.chrome.cursor.colors=['x);background:url(evil']")).body
        assert "evil" not in body


def test_theme_tokens_can_be_scoped_to_a_subtree():
    css = render_theme_css(load_theme("meta"), selector="#oa-chrome")
    assert css.startswith("#oa-chrome {") and ":root" not in css


# --------------------------------------------------------------------------
# The real server
# --------------------------------------------------------------------------

APP_PAGES = ["/todo", "/calendar", "/messages", "/maps", "/codeeditor", "/uilibrary"]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task

    logs_dir = tmp_path_factory.mktemp("chrome_logs")
    if GlobalHydra.instance().is_initialized():
        GlobalHydra.instance().clear()
    with initialize_config_dir(version_base=None, config_dir=CONFIG_DIR):
        config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(config.apps)
    return TestClient(app)


@pytest.fixture
def chrome_state():
    from open_apps.apps.start_page import main as start_page

    start_page.reset_chrome_state()
    yield start_page._chrome_state
    start_page.reset_chrome_state()


@pytest.mark.parametrize("route", APP_PAGES)
def test_every_app_page_has_the_chrome(client, route):
    html = client.get(route).text
    key = route.strip("/")
    assert f'data-oa-app="{key}"' in html
    assert {"window-close", "dock", "agent-cursor"} <= ids(html)
    assert re.search(rf'aria-current="page"[^>]*data-testid="dock-{key}"', html)


def test_start_page_has_dock_and_cursor_but_no_titlebar(client):
    html = client.get("/").text
    assert 'id="oa-dock"' in html and 'id="oa-cursor"' in html
    assert 'id="oa-titlebar"' not in html


def test_scoring_endpoints_are_untouched(client):
    assert "oa-chrome" not in client.get("/todo_all").text
    assert "oa-chrome" not in client.get("/desktop_all").text


def test_visiting_marks_running_and_controls_move_state(client, chrome_state):
    client.get("/todo")
    assert chrome_state["running"] == ["todo"]
    assert re.search(r'data-running="true"[^>]*data-testid="dock-todo"', client.get("/calendar").text)

    response = client.post("/chrome/maximize/todo", headers={"referer": "http://testserver/todo"}, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/todo")
    assert chrome_state["maximized"] == ["todo"]
    assert "data-oa-maximized" in client.get("/todo").text

    response = client.post("/chrome/minimize/todo", follow_redirects=False)
    assert response.headers["location"] == "/" and "todo" in chrome_state["running"]

    client.post("/chrome/close/todo", follow_redirects=False)
    assert "todo" not in chrome_state["running"] and chrome_state["maximized"] == []


@pytest.mark.parametrize("referer", ["http://testserver//todo", "http://evil.example/todo", ""])
def test_maximize_never_redirects_off_the_app(client, chrome_state, referer):
    response = client.post("/chrome/maximize/todo", headers={"referer": referer}, follow_redirects=False)
    assert response.headers["location"] == "/todo"


def test_unknown_app_is_a_harmless_redirect(client, chrome_state):
    response = client.post("/chrome/close/nope", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/")
    assert chrome_state == {"running": [], "maximized": []}


def test_chrome_state_is_not_scoreable(client, chrome_state):
    client.get("/todo")
    assert set(client.get("/desktop_all").json()) == {"mode", "pinned", "units"}


def test_dock_follows_desktop_pins_and_exclusions(client, chrome_state):
    from open_apps.apps.start_page import main as start_page

    saved = list(start_page._desktop_state["pinned"])
    try:
        start_page._desktop_state["pinned"] = ["calendar"]
        markup = client.get("/").text
        dock = markup[markup.index('id="oa-dock"'):markup.index('id="oa-dock-panel"')]
        assert "dock-calendar" in dock and "dock-todo" not in dock
    finally:
        start_page._desktop_state["pinned"] = saved
