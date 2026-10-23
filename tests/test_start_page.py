"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Start page (app launcher) markup across layouts, themes and content.

The layouts are eval perturbations, so these pin what an agent can act on --
which elements are links, where they point, which icon each tile shows -- and
leave the look to the stylesheet. Rendering only needs ``app.config``; the
other apps' routes and databases are not involved, so no server is started.
"""
import random
import re
from pathlib import Path

import pytest
from hydra import compose, initialize_config_dir
from starlette.testclient import TestClient

from open_apps.apps.start_page import main as start_page
from open_apps.apps.start_page.helper import LAUNCHER_CSS

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

# Position order from `config/apps/start_page/default.yaml`. The online shop
# is on by default now that it needs no JDK; OpenBanking (position 8) closes the
# row because the UI Library's tile at 7 is disabled by default.
EXPECTED_TILES = [
    ("/todo", "OpenTodos"),
    ("/calendar", "OpenCalendar"),
    ("/messages", "OpenMessages"),
    ("/maps", "OpenMaps"),
    ("/codeeditor", "OpenCodeEditor"),
    ("/onlineshop", "OpenShop"),
    ("/openbanking", "OpenBanking"),
]

TILE_RE = re.compile(
    r'<a href="(?P<href>[^"]*)" class="item"[^>]*>\s*'
    r'<span class="tile-icon"><img src="(?P<icon>[^"]*)" alt="[^"]*"></span>\s*'
    r"<h3>(?P<title>[^<]*)</h3>"
)


def compose_apps(*overrides):
    with initialize_config_dir(version_base=None, config_dir=str(CONFIG_DIR)):
        cfg = compose(config_name="config", overrides=list(overrides))
    return cfg.apps


def render(monkeypatch, *overrides, rng_seed=0):
    """GET / under ``overrides``; returns the body with <style>/<script> removed.

    Mirrors what ``initialize_routes_and_configure_task`` does to the start
    page config (the icon shuffle) without seeding every app's database.

    Every test here is about the tile launcher, which is the `gallery` layout
    now that `desktop` is the default, so that is selected unless a test
    picks a layout of its own. The window chrome is off for the same reason:
    its dock injects links and tiles of its own, and tests/test_chrome.py
    covers it.
    """
    if not any(o.startswith("apps/start_page/layout=") for o in overrides):
        overrides = ("apps/start_page/layout=gallery", *overrides)
    if not any(o.startswith("apps/chrome=") for o in overrides):
        overrides = ("apps/chrome=none", *overrides)
    apps = compose_apps(*overrides)
    if apps.start_page.get("shuffle_icons"):
        start_page.shuffle_icons(apps.start_page.apps, rng=random.Random(rng_seed))
    monkeypatch.setattr(start_page.app, "config", apps, raising=False)
    response = TestClient(start_page.app).get("/")
    assert response.status_code == 200
    return re.sub(r"<(style|script)\b.*?</\1>", "", response.text, flags=re.S)


def tiles(markup):
    return [m.groupdict() for m in TILE_RE.finditer(markup)]


def links(markup):
    return re.findall(r'<a href="([^"]*)"', markup)


LAYOUTS = ["gallery", "broken_logos", "clickable_logos"]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_layout_links_each_app_once_in_position_order(monkeypatch, layout):
    markup = render(monkeypatch, f"apps/start_page/layout={layout}")
    assert [(t["href"], t["title"]) for t in tiles(markup)] == EXPECTED_TILES
    # The tiles and the footer's way home are the only links on the page.
    assert links(markup) == [href for href, _ in EXPECTED_TILES] + ["/"]
    assert '<div id="wrapper"' in markup
    assert '<h2 class="launcher-title">Welcome to OpenApps!</h2>' in markup


def test_clickable_logos_does_not_change_the_start_page(monkeypatch):
    """`clickable_logos` links each app's in-page logo back home; the launcher
    itself must render exactly as the plain gallery does."""
    assert render(monkeypatch, "apps/start_page/layout=clickable_logos") == render(
        monkeypatch
    )


OWN_ICON = {"/todo": "todo", "/calendar": "calendar", "/messages": "messages",
            "/maps": "maps", "/codeeditor": "code", "/onlineshop": "shop",
            "/openbanking": "wallet"}


def test_default_icons_match_their_tiles(monkeypatch):
    for tile in tiles(render(monkeypatch)):
        assert tile["icon"].endswith(f"/{OWN_ICON[tile['href']]}.svg"), tile


def test_broken_logos_detaches_icons_from_their_tiles(monkeypatch):
    default = {t["href"]: t["icon"] for t in tiles(render(monkeypatch))}
    broken = {
        t["href"]: t["icon"]
        for t in tiles(render(monkeypatch, "apps/start_page/layout=broken_logos"))
    }
    assert broken.keys() == default.keys()
    # The configured pictures, dealt to the wrong tiles. The deal covers every
    # configured app, so any configured icon can surface here.
    configured = {
        app.icon for app in compose_apps().start_page.apps.values()
    }
    assert set(broken.values()) <= configured
    assert any(broken[href] != default[href] for href in default)


def test_broken_logos_shuffle_survives_the_bw_icon_set(monkeypatch):
    color = tiles(render(monkeypatch, "apps/start_page/layout=broken_logos", rng_seed=3))
    bw = tiles(
        render(
            monkeypatch,
            "apps/start_page/layout=broken_logos",
            "apps/theme=mono",
            rng_seed=3,
        )
    )
    assert [Path(t["icon"]).name for t in bw] == [Path(t["icon"]).name for t in color]
    assert all("/real_icons_bw/" in t["icon"] for t in bw)


def fills(markup):
    return re.findall(r'class="item" style="--tile-fill: ([^"]+)"', markup)


def test_light_themes_keep_one_fill_per_tile(monkeypatch):
    for theme in ("default", "solarized", "challenging_font"):
        markup = render(monkeypatch, f"apps/theme={theme}")
        assert len(set(fills(markup))) == len(EXPECTED_TILES), theme


@pytest.mark.parametrize("theme", ["dark", "mono"])
def test_non_light_tones_collapse_the_fill(monkeypatch, theme):
    markup = render(monkeypatch, f"apps/theme={theme}")
    assert len(fills(markup)) == len(EXPECTED_TILES)
    assert len(set(fills(markup))) == 1
    # The icons carry their own tile and white glyph, so nothing inverts them.
    assert 'class="items launcher small"' in markup


ASSETS_DIR = Path(start_page.__file__).resolve().parents[1]
ICON_KEYS = ("icon", "bw_icon", "user_icon", "group_icon")


def test_every_configured_icon_is_a_real_svg():
    """Each icon path in the app inventory resolves to a parseable SVG on disk.

    The dock, title bar, app headers and gallery all take these paths straight
    from config into an ``<img>``, so a missing or malformed file surfaces only
    as a broken-image box in a screenshot -- exactly what an agent cannot tell
    from a missing affordance.
    """
    import xml.etree.ElementTree as ET

    apps = compose_apps().start_page.apps
    paths = {app[key] for app in apps.values() for key in ICON_KEYS if app.get(key)}
    assert paths, "no icons configured"
    for url in sorted(paths):
        assert url.endswith(".svg"), url
        file = ASSETS_DIR / url.lstrip("/")
        assert file.is_file(), f"{url} -> {file} missing"
        root = ET.parse(file).getroot()
        assert root.tag == "{http://www.w3.org/2000/svg}svg", url
        # Intrinsic size, so an <img> sized on one axis only (the app header
        # sets just `height`) keeps the square aspect.
        assert root.get("viewBox") == "0 0 64 64", url
        assert root.get("width") == root.get("height") == "64", url
        # No external references: the eval nodes have no outbound network.
        assert "http" not in file.read_text().replace("http://www.w3.org/2000/svg", ""), url


def test_label_only_content_renders_no_empty_paragraphs(monkeypatch):
    markup = render(monkeypatch)
    assert not re.search(r"<p[^>]*>\s*</p>", markup)
    assert "launcher-subtitle" not in markup
    assert "has-descriptions" not in markup


def test_descriptions_switch_the_grid_to_cards(monkeypatch):
    markup = render(monkeypatch, "apps/start_page/content=long_descriptions")
    assert 'class="items launcher small has-descriptions"' in markup
    assert '<p class="launcher-subtitle">Together, these apps' in markup
    # Every description is still on the page, inside its own tile's link.
    apps = compose_apps("apps/start_page/content=long_descriptions").start_page.apps
    chunks = dict(re.findall(r'<a href="([^"]*)" class="item"(.*?)</a>', markup, flags=re.S))
    for href, _ in EXPECTED_TILES:
        opening = apps[href.strip("/")].description.strip()[:60]
        assert f"<p>{opening}" in chunks[href].replace("\n", ""), href
    assert [(t["href"], t["title"]) for t in tiles(markup)] == EXPECTED_TILES


def test_launcher_css_takes_colors_from_theme_tokens():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", LAUNCHER_CSS)
    assert not re.search(r"\b(rgba?|hsla?)\(", LAUNCHER_CSS)
    assert ":focus-visible" in LAUNCHER_CSS


@pytest.mark.parametrize("layout", LAYOUTS)
def test_wordmark_heads_the_page_without_adding_a_link(monkeypatch, layout):
    markup = render(monkeypatch, f"apps/start_page/layout={layout}")
    brand = markup.index('class="launcher-brand"')
    headline = markup.index('class="launcher-title"')
    assert brand < headline
    header = markup[brand:headline]
    assert 'role="img"' in header and 'aria-label="OpenApps"' in header
    assert "<a " not in header


def test_tile_layout_also_covers_the_wrapper_main_js_injects():
    # main.js wraps every tile's children in <div class="inner"> at runtime;
    # an unstyled wrapper shrink-wraps to the label and pushes icons
    # off-centre. Markup tests never see it, so pin the CSS that handles it.
    css = re.sub(r"\s+", " ", LAUNCHER_CSS)
    assert "#wrapper .launcher > .item, #wrapper .launcher > .item > .inner {" in css
    assert (
        "#wrapper .launcher.has-descriptions > .item, "
        "#wrapper .launcher.has-descriptions > .item > .inner {"
    ) in css


@pytest.mark.parametrize(
    "pack",
    sorted(p.stem for p in (Path(__file__).parent.parent / "config/apps/start_page/content").glob("*.yaml")),
)
def test_every_content_pack_titles_every_app(pack):
    """A start-page content pack that omits an app leaves its `title` unset,
    and the app then fails to register its routes at all -- the UI Library
    404'd under `long_descriptions`, `misleading_descriptions` and
    `adversarial_descriptions` this way, silently."""
    from hydra import compose, initialize

    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"apps/start_page/content={pack}"])
    apps = config.apps.start_page.apps
    missing = [key for key in apps if not apps[key].get("title")]
    assert not missing, f"{pack} has no title for {missing}"
