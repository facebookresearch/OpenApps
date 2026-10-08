"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Guards against pages that only work when the host has internet.

The eval nodes have no outbound network. Anything a page fetches from a CDN
simply does not arrive there, and nothing reports it -- the page still returns
200, the DOM still renders, and the run still produces a trajectory. The two
failures that matter:

* **htmx missing** makes every ``hx-*`` attribute inert. A checkbox still
  toggles when clicked, because that is the browser's own behaviour, so a
  screenshot shows the interaction landing while no request is sent and no
  server state changes. Every task depending on that state scores zero, and
  the trajectory looks like a model that clicked the right thing.
* **Pico missing** renders every page unstyled, which changes the observation a
  screenshot-scored agent is graded on.

``test_interactive_routes_load_htmx_locally`` is the one that catches the first
case. The rest keep the cleanup from regressing.
"""

from pathlib import Path

import pytest
import re
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.start_page.main import (
    app,
    initialize_routes_and_configure_task,
)
from open_apps.frontend import HTMX_URL, PICO_URL

# Every route a browser (or an agent) actually lands on.
ROUTES = ["/", "/todo", "/calendar", "/messages", "/codeeditor/", "/maps",
          "/onlineshop"]

# External origins each route is still allowed to reference, by hostname.
#
# This is a ratchet, not a target: a new entry means new egress and should be
# argued for, while entries disappear as apps move onto local assets. Empty
# lists are the goal state and two routes are already there.
#
# Everything below is a styling or widget dependency. htmx and Pico are
# deliberately absent -- they are covered by their own stricter test, because
# they are the two whose absence changes behaviour rather than appearance.
ALLOWED_EXTERNAL_HOSTS = {
    "/": set(),
    "/todo": set(),
    "/calendar": {"cdn.jsdelivr.net", "unpkg.com"},          # highlight.js, phosphor-icons
    "/messages": {"cdn.jsdelivr.net", "cdn.tailwindcss.com", "cdnjs.cloudflare.com"},
    "/codeeditor/": {"cdn.jsdelivr.net", "cdn.tailwindcss.com"},
    "/maps": {"cdnjs.cloudflare.com", "unpkg.com"},          # leaflet + awesome-markers
    # Unlike every other entry here, this is content rather than a styling
    # dependency: the default `content=webshop` pack sets
    # `product_images: hotlink`, so each product renders its own Amazon photo.
    # Drops out under `apps.onlineshop.product_images=glyphs`, which is what
    # an eval node without egress should pass.
    "/onlineshop": {"m.media-amazon.com"},
}

_URL_RE = re.compile(r'(?:src|href)="(https?://[^"]+)"')
_HX_RE = re.compile(r"\bhx-(?:get|post|put|delete|patch)=")


def external_urls(html: str) -> list[str]:
    """Absolute URLs the page tells the browser to go fetch.

    TestClient serves from ``http://testserver``, so its own origin is not
    external and is filtered out.
    """
    return [u for u in _URL_RE.findall(html) if "testserver" not in u]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # A dedicated temp dir rather than the shared ``getbasetemp()``: the apps
    # seed their tables with fixed primary keys at startup, so re-initializing
    # over another module's database collides on insert.
    logs_dir = tmp_path_factory.mktemp("no_egress")
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(config.apps)
    return TestClient(app)


@pytest.mark.parametrize("route", ROUTES)
def test_htmx_and_pico_never_come_from_a_cdn(client, route):
    """No page may fetch htmx or Pico from an external origin.

    FastHTML's default headers do exactly this, so any app constructed without
    ``default_hdrs=False`` reintroduces it. That is the regression this catches:
    it passes on a laptop either way, and only diverges on an offline host.
    """
    offenders = [
        u for u in external_urls(client.get(route).text)
        if "htmx" in u.lower() or "pico" in u.lower()
    ]
    assert not offenders, (
        f"{route} fetches htmx/Pico from a CDN: {offenders}. "
        "Construct the app with default_hdrs=False and hdrs=local_hdrs() "
        "(see src/open_apps/frontend.py)."
    )


@pytest.mark.parametrize("route", ROUTES)
def test_interactive_routes_load_htmx_locally(client, route):
    """A page with ``hx-*`` attributes must load htmx from local assets.

    This is the pairing that actually broke: ``/todo`` carries 15 ``hx-put``
    attributes and htmx came from jsdelivr, so offline every one of them was
    dead while the page looked and behaved almost normally.

    Routes with no ``hx-*`` attributes are skipped rather than required to load
    htmx -- there is no reason to ship it to a page that does not use it.
    """
    html = client.get(route).text
    hx_count = len(_HX_RE.findall(html))
    if hx_count == 0:
        pytest.skip(f"{route} has no hx-* attributes")
    assert HTMX_URL in html, (
        f"{route} has {hx_count} hx-* attributes but does not load {HTMX_URL}. "
        "Offline, every one of those interactions silently does nothing."
    )


@pytest.mark.parametrize("route", ROUTES)
def test_external_origins_match_allowlist(client, route):
    """Fail on *new* egress; allow the known, still-to-be-cleaned dependencies."""
    hosts = {u.split("/")[2] for u in external_urls(client.get(route).text)}
    unexpected = hosts - ALLOWED_EXTERNAL_HOSTS[route]
    assert not unexpected, (
        f"{route} gained new external origins: {sorted(unexpected)}. "
        "Vendor the asset under apps/assets/vendor, or add the host to "
        "ALLOWED_EXTERNAL_HOSTS with a reason."
    )


@pytest.mark.parametrize("asset_url", [HTMX_URL, PICO_URL])
def test_vendored_assets_are_served(client, asset_url):
    """The vendored files exist and the static route actually serves them.

    A broken path here fails exactly like the CDN did -- 404, no error, dead
    interactions -- so it is worth asserting rather than assuming.
    """
    response = client.get(asset_url)
    assert response.status_code == 200, f"{asset_url} -> {response.status_code}"
    assert len(response.content) > 10_000, (
        f"{asset_url} served only {len(response.content)} bytes; "
        "the file is probably a truncated or failed download."
    )
