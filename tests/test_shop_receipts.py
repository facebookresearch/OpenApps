"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Order receipts: written to Files at checkout and for the seeded orders.

A receipt is a document an agent reads back ("what did I pay for ...?"), so
it has to say what the order says. The seeded receipts are part of the
initial Files state, so they must come out byte-identical on every launch;
a checkout receipt carries the checkout time and a random order id, which
scoring normalises.
"""

import html
import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps import files_store
from open_apps.apps.onlineshop_app import main as shop
from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task

SEEDED = {
    "a41f07c2": "Receipts/OpenShop order a41f07c2.pdf",
    "7d93e5b0": "Receipts/OpenShop order 7d93e5b0.pdf",
}
GOOD_CARD = {
    "name": "Dana Reyes",
    "address": "44 Wharf Street Portland ME 04101",
    "card_number": "9024007155992043",
    "card_expiry": "09/29",
    "card_cvv": "418",
    "card_name": "Cardinal Freight LLC",
}


def _compose(tmp_path_factory, *overrides):
    logs_dir = tmp_path_factory.mktemp("receipts")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}", *overrides])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    cfg = _compose(tmp_path_factory, "apps.onlineshop.product_images=glyphs")
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


def pdf_text(data: bytes) -> str:
    runs = re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", data)
    return " ".join(r.decode("latin-1").replace("\\(", "(").replace("\\)", ")") for r in runs)


def receipts(client) -> list[str]:
    return sorted(
        r["path"] for r in client.get("/files_all").json()["files"]
        if r["path"].startswith("Receipts/") and r["kind"] != "folder" and not r["deleted"]
    )


def modified_day(path: str) -> str:
    """The date Files shows in its "Date modified" column."""
    import datetime

    return datetime.date.fromtimestamp(files_store.resolve(path).stat().st_mtime).isoformat()


class TestSeededReceipts:
    def test_a_seeded_receipt_is_dated_by_its_order(self, client):
        """Not by the day the run happens: the column is part of what an agent
        sees, and the world's clock is pinned."""
        assert modified_day("Receipts/OpenShop order a41f07c2.pdf") == "2026-06-12"

    def test_each_seeded_order_has_its_receipt(self, client):
        assert set(SEEDED.values()) <= set(receipts(client))

    def test_receipt_says_what_the_order_says(self, client):
        order = next(o for o in client.get("/onlineshop_all").json()["orders"]
                     if o["order_id"] == "7d93e5b0")
        text = pdf_text(files_store.read(SEEDED["7d93e5b0"]))
        for expected in ("Order 7d93e5b0", "Dana Reyes", "44 Wharf Street Portland ME 04101",
                         "Card ending 2043", "2026-07-17 09:32:10", shop.money(order["total"])):
            assert expected in text
        assert "2" in text  # the quantity column carries the 2x line

    def test_stored_receipt_is_the_rendered_one(self, client):
        for order_id, path in SEEDED.items():
            assert files_store.read(path) == shop.receipt_pdf(shop._row(shop.orders, order_id))

    def test_reseeding_is_byte_identical_and_keeps_one_copy(self, client):
        before = {p: files_store.read(p) for p in SEEDED.values()}
        shop.set_environment(app.config)
        assert {p: files_store.read(p) for p in SEEDED.values()} == before
        assert not any("(2)" in p for p in receipts(client))


class TestOrdersPage:
    def test_every_order_links_its_receipt(self, client):
        body = client.get("/onlineshop/orders").text
        for order_id in SEEDED:
            reader = files_store.pdf_view_url(f"/onlineshop/orders/{order_id}/receipt", shop.receipt_name(order_id))
            link = re.search(rf'<a[^>]*href="{re.escape(html.escape(reader))}"[^>]*>', body)
            assert link, f"no receipt link for {order_id}"
            assert 'target="_blank"' in link.group(0)

    def test_receipt_route_serves_an_inline_pdf(self, client):
        response = client.get("/onlineshop/orders/a41f07c2/receipt")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"].startswith("inline")
        assert response.content == files_store.read(SEEDED["a41f07c2"])

    def test_unknown_order_404s(self, client):
        assert client.get("/onlineshop/orders/nope/receipt").status_code == 404


class TestCheckout:
    def test_checkout_writes_a_receipt_for_the_new_order(self, client):
        before = {o["order_id"] for o in client.get("/onlineshop_all").json()["orders"]}
        client.post("/onlineshop/cart/add/B097PCJMP5", data={})
        assert client.get("/onlineshop_all").json()["cart"], "the cart should hold the item"
        client.post("/onlineshop/checkout", data=GOOD_CARD)
        after = client.get("/onlineshop_all").json()["orders"]
        (new,) = [o for o in after if o["order_id"] not in before]
        path = f"Receipts/OpenShop order {new['order_id']}.pdf"
        assert path in receipts(client)
        text = pdf_text(files_store.read(path))
        assert f"Order {new['order_id']}" in text and shop.money(new["total"]) in text

    def test_a_declined_checkout_writes_nothing(self, client):
        before = receipts(client)
        client.post("/onlineshop/cart/add/B097PCJMP5", data={})
        client.post("/onlineshop/checkout", data=dict(GOOD_CARD, card_cvv="000"))
        assert receipts(client) == before


def test_a_shop_on_its_own_writes_into_its_own_runs_store(tmp_path_factory):
    """Stood up without the start page (as the shop's own tests do), the shop
    must write into the store its config names, not whichever store some
    other launch in the process configured last."""
    cfg = _compose(tmp_path_factory, "apps/onlineshop/content=fixture")
    shop.set_environment(cfg.apps)
    root = Path(cfg.apps.files.root)
    assert (root / "Receipts" / "OpenShop order 0efcf51f.pdf").is_file()
