"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for the online shop.

The shop is exercised through its own FastHTML app rather than through the
start page, so a test can re-seed it with a different `content`, `layout` or
`theme` selection without re-registering every other app's routes.
"""

import json
import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.onlineshop_app import main as shop
from open_apps.apps.start_page.main import onlineshop_has_catalog


def build_client(tmp_path, overrides=None):
    """Compose a config, seed the shop from it, and return a client.

    The shipped `default` content pack has an empty catalog on purpose -- the
    real one is the WebShop dump, downloaded per-machine by
    `scripts/fetch_webshop.py` and never committed. So every test runs against
    `content=fixture`, the small mechanical catalog, unless it is deliberately
    exercising a different pack.
    """
    overrides = list(overrides or [])
    if not any(o.startswith("apps/onlineshop/content=") for o in overrides):
        overrides.insert(0, "apps/onlineshop/content=fixture")
    with initialize(version_base=None, config_path="../config/"):
        config = compose(
            config_name="config",
            overrides=[f"logs_dir={tmp_path}"] + overrides,
        )
    Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    shop.set_environment(config.apps)
    return TestClient(shop.app)


@pytest.fixture
def client(tmp_path):
    return build_client(tmp_path)


def state(client) -> dict:
    return client.get("/onlineshop_all").json()


class TestRoutes:
    @pytest.mark.parametrize(
        "path",
        [
            "/onlineshop",
            "/onlineshop/cart",
            "/onlineshop/orders",
            "/onlineshop/checkout",
            "/onlineshop/item/elec-hdph-001",
            "/onlineshop/search/coffee/1",
            "/onlineshop/category/furniture/1",
            "/onlineshop_all",
        ],
    )
    def test_route_renders(self, client, path):
        assert client.get(path).status_code == 200

    def test_unknown_product_does_not_500(self, client):
        response = client.get("/onlineshop/item/no-such-sku")
        assert response.status_code == 200
        assert "not found" in response.text.lower()

    def test_search_form_redirects_to_canonical_url(self, client):
        response = client.post(
            "/onlineshop/search", data={"search_query": "Oak Table"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/onlineshop/search/oak,table/1"


class TestSearch:
    def test_title_match_ranks_first(self, client):
        results = shop.search_products("oak dining table")
        assert results[0].sku == "furn-table-201"

    def test_option_values_are_searchable(self, client):
        """The original indexed option text alongside the description."""
        skus = {p.sku for p in shop.search_products("walnut")}
        assert "furn-desk-205" in skus  # walnut is a finish option, not in the text

    def test_partial_match_still_returns_results(self, client):
        """Tokens are OR-ed, as the Lucene-backed original behaved."""
        assert shop.search_products("coffee unicorn") != []

    def test_fts_operators_in_user_input_are_not_executed(self, client):
        """An unbalanced quote or a bare NEAR must not raise."""
        for query in ['"', "NEAR(", "*", "a OR", "^%$"]:
            client.get(f"/onlineshop/search/{query}/1")
            shop.search_products(query)

    def test_no_match_renders_empty_state(self, client):
        response = client.get("/onlineshop/search/zzzznotathing/1")
        assert response.status_code == 200
        assert "No products matched" in response.text


class TestCart:
    def test_add_to_cart_records_options(self, client):
        client.post(
            "/onlineshop/cart/add/furn-table-201",
            data={"option_finish": "walnut", "option_size": "seats six", "quantity": 2},
        )
        line = [c for c in state(client)["cart"] if c["sku"] == "furn-table-201"][0]
        assert line["quantity"] == 2
        assert line["options"] == {"finish": "walnut", "size": "seats six"}

    def test_same_product_same_options_merges(self, client):
        for _ in range(2):
            client.post(
                "/onlineshop/cart/add/furn-table-201",
                data={"option_finish": "walnut", "option_size": "seats six"},
            )
        lines = [c for c in state(client)["cart"] if c["sku"] == "furn-table-201"]
        assert len(lines) == 1
        assert lines[0]["quantity"] == 2

    def test_same_product_different_options_does_not_merge(self, client):
        client.post("/onlineshop/cart/add/furn-table-201",
                    data={"option_finish": "walnut", "option_size": "seats six"})
        client.post("/onlineshop/cart/add/furn-table-201",
                    data={"option_finish": "natural", "option_size": "seats six"})
        lines = [c for c in state(client)["cart"] if c["sku"] == "furn-table-201"]
        assert len(lines) == 2

    def test_option_order_does_not_split_the_line(self, client):
        """`_options_key` sorts, so form field order cannot create a duplicate."""
        assert shop._options_key({"size": "m", "color": "navy"}) == shop._options_key(
            {"color": "navy", "size": "m"}
        )

    def test_unavailable_option_is_dropped(self, client):
        client.post("/onlineshop/cart/add/furn-table-201",
                    data={"option_finish": "neon pink"})
        line = [c for c in state(client)["cart"] if c["sku"] == "furn-table-201"][0]
        assert line["options"] == {}

    def test_remove_and_quantity_and_toggle(self, client):
        before = state(client)["cart"]
        assert before, "the default content seeds a non-empty cart"

        client.post("/onlineshop/cart/add/offi-pen-702",
                    data={"option_nib": "fine", "option_finish": "black"})
        row_id = [r.id for r in shop.cart_items() if r.sku == "offi-pen-702"][0]

        client.post(f"/onlineshop/cart/quantity/{row_id}", data={"quantity": 5})
        assert shop._row(shop.cart_items, row_id).quantity == 5

        client.post(f"/onlineshop/cart/toggle/{row_id}")
        assert not shop._row(shop.cart_items, row_id).selected

        client.post(f"/onlineshop/cart/remove/{row_id}")
        assert shop._row(shop.cart_items, row_id) is None

    def test_cart_line_shows_a_line_total(self, client):
        client.post("/onlineshop/cart/add/offi-pen-702",
                    data={"option_nib": "fine", "option_finish": "black", "quantity": 3})
        body = client.get("/onlineshop/cart").text
        assert "$89.00 each" in body
        assert "$267.00" in body  # 89.00 x 3

    def test_lines_differing_only_by_options_are_distinguishable(self, client):
        """Two lines of one product differ *only* by options, so the options
        have to be the most visible thing on the line."""
        for nib in ("fine", "broad"):
            client.post("/onlineshop/cart/add/offi-pen-702",
                        data={"option_nib": nib, "option_finish": "black"})
        body = client.get("/onlineshop/cart").text
        assert body.count('class="option-chip"') >= 4  # 2 options x 2 lines
        assert "nib: fine" in body and "nib: broad" in body

    def test_every_page_can_navigate_back_to_the_shop(self, client):
        """`clickable_logo` defaults to false and is a variation axis, so
        navigation cannot depend on the header."""
        for path in ("/onlineshop/cart", "/onlineshop/orders",
                     "/onlineshop/item/offi-pen-702", "/onlineshop/search/coffee/1"):
            assert 'href="/onlineshop"' in client.get(path).text, path

    def test_adding_unknown_sku_is_a_noop(self, client):
        before = len(state(client)["cart"])
        client.post("/onlineshop/cart/add/no-such-sku", data={"quantity": 1})
        assert len(state(client)["cart"]) == before


class TestCheckout:
    def test_checkout_moves_selected_cart_lines_into_an_order(self, client):
        cart_before = state(client)["cart"]
        expected_total = round(
            sum(line["unit_price"] * line["quantity"] for line in cart_before), 2
        )

        client.post("/onlineshop/checkout",
                    data={"name": "Dana Reed", "address": "9 Mill Lane"})

        after = state(client)
        assert after["cart"] == []
        order = after["orders"][-1]
        assert order["name"] == "Dana Reed"
        assert order["status"] == "Processing"
        assert order["total"] == expected_total
        assert len(order["items"]) == len(cart_before)

    def test_deselected_lines_stay_in_the_cart(self, client):
        row_id = [r.id for r in shop.cart_items()][0]
        client.post(f"/onlineshop/cart/toggle/{row_id}")
        client.post("/onlineshop/checkout", data={"name": "A", "address": "B"})

        remaining = [line["sku"] for line in state(client)["cart"]]
        assert remaining == [shop._row(shop.cart_items, row_id).sku]

    def test_empty_selection_does_not_create_an_order(self, client):
        for row in shop.cart_items():
            client.post(f"/onlineshop/cart/toggle/{row.id}")
        before = len(state(client)["orders"])
        client.post("/onlineshop/checkout", data={"name": "A", "address": "B"})
        assert len(state(client)["orders"]) == before

    def test_rejected_card_does_not_create_an_order(self, tmp_path):
        client = build_client(
            tmp_path, ["apps.onlineshop.enable_credit_card_check=true"]
        )
        before = len(state(client)["orders"])
        client.post(
            "/onlineshop/checkout",
            data={"name": "A", "address": "B", "card_type": "Diners Club"},
        )
        assert len(state(client)["orders"]) == before

    def test_allowed_card_creates_an_order(self, tmp_path):
        client = build_client(
            tmp_path, ["apps.onlineshop.enable_credit_card_check=true"]
        )
        before = len(state(client)["orders"])
        client.post(
            "/onlineshop/checkout",
            data={"name": "A", "address": "B", "card_type": "Visa"},
        )
        assert len(state(client)["orders"]) == before + 1


class TestRewardState:
    def test_state_is_json_serialisable_and_flat(self, client):
        payload = state(client)
        assert set(payload) == {"cart", "orders"}
        # The previous implementation keyed order lines by a stringified
        # Python tuple that needed ast.literal_eval to read back.
        for order in payload["orders"]:
            assert isinstance(order["items"], list)
            for item in order["items"]:
                assert isinstance(item["options"], dict)
        json.dumps(payload)

    def test_seeded_state_matches_the_config(self, client):
        payload = state(client)
        assert [line["sku"] for line in payload["cart"]] == [
            "elec-hdph-001", "home-brew-102"
        ]
        assert [order["order_id"] for order in payload["orders"]] == [
            "0efcf51f", "09bd3dfb"
        ]

    def test_table_names_are_plural_and_not_reserved_words(self, client):
        """The schema is meant to be read directly by other apps and by
        humans. fastlite would otherwise singularise `Order` to `order`, a
        SQL reserved word that breaks `SELECT * FROM order`."""
        names = set(shop.db.table_names())
        assert {"products", "cart_items", "orders", "order_items"} <= names
        assert "order" not in names
        # and the obvious query works unquoted
        assert shop.db.q("SELECT order_id FROM orders LIMIT 1") is not None

    def test_catalog_is_opt_in(self, client):
        assert "catalog" not in state(client)
        payload = client.get("/onlineshop_all?include_catalog=true").json()
        assert len(payload["catalog"]) == 18  # the fixture pack

    def test_reseeding_is_idempotent(self, tmp_path):
        """set_environment runs again on reset; it must not double-insert."""
        client = build_client(tmp_path)
        first = state(client)
        client = build_client(tmp_path)
        assert state(client) == first


class TestVariations:
    @pytest.mark.parametrize("layout", ["default", "grid", "compact_table"])
    def test_every_layout_renders(self, tmp_path, layout):
        client = build_client(tmp_path, [f"apps/onlineshop/layout={layout}"])
        assert client.get("/onlineshop").status_code == 200
        assert client.get("/onlineshop/search/coffee/1").status_code == 200

    def test_layouts_produce_different_markup(self, tmp_path):
        markup = {}
        for layout in ("default", "grid", "compact_table"):
            client = build_client(tmp_path, [f"apps/onlineshop/layout={layout}"])
            markup[layout] = client.get("/onlineshop").text
        assert 'class="product-grid"' in markup["grid"]
        assert 'class="product-table"' in markup["compact_table"]
        assert 'class="product-row"' in markup["default"]
        assert len({*markup.values()}) == 3

    def test_compact_table_has_no_imagery(self, tmp_path):
        client = build_client(tmp_path, ["apps/onlineshop/layout=compact_table"])
        assert "<svg" not in client.get("/onlineshop").text

    @pytest.mark.parametrize("theme", ["default", "dark", "solarized", "mono"])
    def test_theme_tokens_are_emitted(self, tmp_path, theme):
        client = build_client(tmp_path, [f"apps/theme={theme}"])
        body = client.get("/onlineshop").text
        assert "--color-bg" in body and "--color-primary" in body

    def test_theme_changes_the_rendered_tokens(self, tmp_path):
        light = build_client(tmp_path, ["apps/theme=default"]).get("/onlineshop").text
        dark = build_client(tmp_path, ["apps/theme=dark"]).get("/onlineshop").text
        assert light != dark

    def test_per_app_theme_overrides_the_global_one(self, tmp_path):
        client = build_client(
            tmp_path, ["apps/theme=default", "apps.onlineshop.theme=solarized"]
        )
        # solarized's background token, which `default` never emits.
        assert "#fdf6e3" in client.get("/onlineshop").text

    def test_german_content_translates_the_category_strip(self, tmp_path):
        client = build_client(tmp_path, ["apps/onlineshop/content=german"])
        body = client.get("/onlineshop").text
        assert "Möbel" in body and "Lebensmittel" in body

    def test_adversarial_content_reaches_the_page(self, tmp_path):
        client = build_client(
            tmp_path, ["apps/onlineshop/content=adversarial_descriptions"]
        )
        assert client.get("/onlineshop").status_code == 200


class TestCatalogGate:
    """The shop is registered only when it has something to sell.

    Asserted against the config rather than by re-registering routes, because
    `initialize_routes_and_configure_task` mutates the shared start-page app
    and would leak the shop's routes into every test that runs after it.
    """

    def _apps_config(self, tmp_path, overrides=None):
        with initialize(version_base=None, config_path="../config/"):
            return compose(
                config_name="config",
                overrides=[f"logs_dir={tmp_path}"] + list(overrides or []),
            ).apps

    def test_shipped_config_has_no_catalog(self, tmp_path):
        """`content=default` is chrome only -- see config/.../content/default.yaml."""
        assert not onlineshop_has_catalog(self._apps_config(tmp_path))

    def test_a_content_pack_with_products_opens_the_shop(self, tmp_path):
        config = self._apps_config(tmp_path, ["apps/onlineshop/content=fixture"])
        assert onlineshop_has_catalog(config)

    def test_gate_is_independent_of_the_enable_flag(self, tmp_path):
        """Two separate reasons to hide the shop; neither implies the other."""
        config = self._apps_config(
            tmp_path,
            ["apps/onlineshop/content=fixture", "apps.onlineshop.enable=False"],
        )
        assert onlineshop_has_catalog(config)
        assert not config.onlineshop.enable

    def test_missing_shop_config_does_not_raise(self, tmp_path):
        """A config with the shop stripped out entirely still answers False."""
        config = self._apps_config(tmp_path)
        del config.onlineshop
        assert not onlineshop_has_catalog(config)

    def test_empty_catalog_still_renders_the_shop_app_directly(self, tmp_path):
        """Gating lives in the start page, not the shop.

        The app itself has to survive an empty catalog: tests, resets and
        `content` sweeps all seed it with zero products at some point.
        """
        client = build_client(tmp_path, ["apps/onlineshop/content=default"])
        assert client.get("/onlineshop").status_code == 200
        assert state(client)["cart"] == []


class TestProductImagery:
    """Thumbnails are generated line art, not fetched images."""

    @pytest.mark.parametrize(
        "sku,glyph",
        [
            ("elec-hdph-001", "headphones"),
            ("home-skil-103", "pan"),
            ("furn-desk-205", "desk"),
            # "LED Desk Lamp" and "Walnut Desk Organizer" both contain "desk";
            # keyword order has to resolve them to their own glyphs.
            ("offi-lamp-704", "lamp"),
            ("offi-orgz-703", "organizer"),
            # "Backpacking Tent" contains "backpack".
            ("outd-tent-401", "tent"),
            ("groc-oliv-603", "oil"),
        ],
    )
    def test_products_get_the_right_glyph(self, client, sku, glyph):
        product = shop._row(shop.products, sku)
        assert shop._glyph_for(product) is shop._GLYPHS[glyph]

    @pytest.mark.parametrize(
        "title,not_glyph",
        [
            # Each of these matched the wrong keyword as a bare substring
            # before `_GLYPH_PATTERNS` added word boundaries. Real catalog
            # titles are long retailer strings, so all of them occur.
            ("Open Toe Sandal for Women", "pen"),
            ("Content Creator Ring Light", "tent"),
            ("Pendant Necklace, Silver", "pen"),
            ("Heavy Duty Clamp Set", "lamp"),
            ("Satin Curtain Panel", "tin"),
        ],
    )
    def test_keywords_match_whole_words_only(self, client, title, not_glyph):
        product = shop._row(shop.products, "elec-hdph-001")
        product.title, product.category = title, "office"
        assert shop._glyph_for(product) is not shop._GLYPHS[not_glyph]

    def test_word_boundaries_do_not_break_real_matches(self, client):
        """The boundaries must not cost the matches they were protecting."""
        product = shop._row(shop.products, "elec-hdph-001")
        for title, glyph in [
            ("Fountain Pen, Fine Nib", "pen"),
            ("Backpacking Tent, 2-Person", "tent"),
            ("LED Desk Lamp", "lamp"),
            ("Open Toe Sandal for Women", "shoe"),
        ]:
            product.title = title
            assert shop._glyph_for(product) is shop._GLYPHS[glyph], title

    def test_every_product_resolves_to_a_glyph(self, client):
        for product in shop.products():
            assert shop._glyph_for(product), product.title

    def test_unknown_category_still_renders(self, client):
        """A product added to the config with no matching keyword must not 500."""
        product = shop._row(shop.products, "elec-hdph-001")
        product.title, product.category = "Nondescript Widget", "not_a_category"
        assert shop._glyph_for(product)
        assert "<svg" in str(shop.product_image(product))

    def test_thumbnail_is_inline_svg_with_no_src(self, client):
        body = client.get("/onlineshop").text
        assert "<svg" in body
        assert "<img" not in body.split("</head>")[-1] or "media-amazon" not in body

    def test_hue_is_stable_for_a_sku(self, client):
        product = shop._row(shop.products, "elec-hdph-001")
        assert str(shop.product_image(product)) == str(shop.product_image(product))


def markup(client, path="/onlineshop") -> str:
    """Page markup with the stylesheet stripped.

    The shop inlines its CSS, and that CSS mentions every carousel class name,
    so counting class names in the whole document would find matches on a page
    that renders none of them.
    """
    return client.get(path).text.split("</style>")[-1]


class TestHotlinkedImages:
    """`apps.onlineshop.product_images=hotlink` renders the catalog's photos.

    Off by default: the eval nodes have no outbound network, so hotlinked
    images do not arrive there while the page still returns 200, and a
    screenshot-scored agent would be graded on an observation that quietly
    lost its imagery.
    """

    def hotlink(self, tmp_path, extra=None):
        return build_client(
            tmp_path,
            ["apps.onlineshop.product_images=hotlink"] + list(extra or []),
        )

    def test_default_mode_emits_no_product_images(self, client):
        # The page header carries a local logo <img>, so this checks for
        # product imagery specifically rather than for any <img> at all.
        page = markup(client)
        assert "carousel-slide" not in page
        assert "product-media" not in page
        assert "example.invalid" not in page

    def test_hotlink_renders_the_catalog_urls(self, tmp_path):
        page = markup(self.hotlink(tmp_path))
        assert "https://images.example.invalid/hdph-front.jpg" in page
        assert page.count("carousel-slide") >= 3

    def test_multiple_images_get_carousel_controls(self, tmp_path):
        page = markup(self.hotlink(tmp_path), "/onlineshop/item/elec-hdph-001")
        assert page.count('class="carousel-slide"') == 3
        assert page.count('class="carousel-dot"') == 3
        # One radio per slide, exactly one of them checked.
        assert page.count('class="carousel-state"') == 3
        assert page.count("checked") == 1

    def test_a_single_image_gets_no_controls(self, tmp_path):
        page = markup(self.hotlink(tmp_path), "/onlineshop/item/elec-mntr-002")
        assert page.count('class="carousel-slide"') == 1
        assert "carousel-dot" not in page

    def test_products_without_images_still_get_a_glyph(self, tmp_path):
        """Most of the fixture has no `images`; those must not go blank."""
        page = markup(self.hotlink(tmp_path), "/onlineshop/item/furn-table-201")
        assert "carousel-slide" not in page
        assert "<svg" in page

    def test_the_glyph_is_kept_as_a_fallback_behind_the_photos(self, tmp_path):
        """An image that fails to load must degrade to line art, not to a
        broken-image icon: the glyph ships in the markup and `onerror` flips
        the container class that reveals it."""
        page = markup(self.hotlink(tmp_path), "/onlineshop/item/elec-hdph-001")
        assert "<svg" in page
        assert "images-failed" in page

    def test_each_product_gets_its_own_radio_group(self, tmp_path):
        """Two carousels on one listing must not steer each other."""
        page = markup(self.hotlink(tmp_path))
        groups = re.findall(r'name="(carousel-[^"]+)"', page)
        assert groups and len(set(groups)) == len({g for g in groups})
        assert "carousel-elec-hdph-001" in groups

    def test_an_unrecognised_mode_falls_back_to_glyphs(self, tmp_path):
        """A typo in a sweep override must not start hotlinking."""
        page = markup(build_client(
            tmp_path, ["apps.onlineshop.product_images=nonsense"]))
        assert "product-media" not in page
        assert "example.invalid" not in page

    def test_compact_table_stays_imageless_in_hotlink_mode(self, tmp_path):
        """The compact_table layout drops imagery entirely; turning images on
        must not put it back."""
        client = self.hotlink(tmp_path, ["apps/onlineshop/layout=compact_table"])
        page = markup(client)
        assert "<svg" not in page
        assert "product-media" not in page and "example.invalid" not in page

    def test_mode_is_a_variation_axis_not_a_reimport(self, tmp_path):
        """URLs live in the database either way, so flipping the mode does not
        require rebuilding the catalog."""
        client = build_client(tmp_path)
        product = shop._row(shop.products, "elec-hdph-001")
        assert len(json.loads(product.images)) == 3


class TestNoEgress:
    """The old shop pointed every thumbnail at an Amazon CDN URL."""

    @pytest.mark.parametrize(
        "path",
        ["/onlineshop", "/onlineshop/cart", "/onlineshop/item/elec-hdph-001"],
    )
    def test_pages_reference_no_external_hosts(self, client, path):
        # `testserver` is TestClient's own base host, `www.w3.org` is the SVG
        # namespace declaration -- neither is a request the browser makes.
        allowed = ("localhost", "testserver", "www.w3.org")
        body = client.get(path).text
        for marker in ("http://", "https://"):
            for chunk in body.split(marker)[1:]:
                host = chunk.split("/")[0].split('"')[0]
                assert host in allowed, f"external host {host}"
