"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""The online shop across its variation axes: language, theme and layout.

`tests/test_onlineshop.py` covers behaviour on the English fixture catalog.
This file covers what changes -- and, as importantly, what must not -- when
`apps/onlineshop/content=german|mandarin`, `apps/theme=dark|challenging_font`
and `apps/onlineshop/layout=` are varied, alone and together.

The translated packs layer on `webshop` (the real catalog), not on the
fixture, so the language tests run on that catalog. Product images are forced
to `glyphs` there: `webshop` hotlinks by default, and these pages are asserted
on, not looked at.
"""

import copy
import html
import re
from pathlib import Path

import pytest
import yaml
from hydra.utils import instantiate
from omegaconf import OmegaConf

from open_apps import config_dir
from open_apps.apps.onlineshop_app import main as shop
from open_apps.apps.start_page.main import onlineshop_has_catalog
from open_apps.tasks.tasks import BuyWithCardTask

from tests.test_onlineshop import GOOD_CARD, build_client

CONTENT = config_dir() / "apps" / "onlineshop" / "content"
MAIN = Path(shop.__file__)

LANGS = ["german", "mandarin"]
HTML_LANG = {"english": "en", "german": "de", "mandarin": "zh-Hans"}
LAYOUTS = ["default", "grid", "compact_table"]

# One page per surface the shop has. The webshop pack seeds a two-line cart
# and two delivered orders, so every one of these has content to translate.
PAGES = {
    "home": "/onlineshop",
    "results": "/onlineshop/search/table/1",
    "category": "/onlineshop/category/furniture/1",
    "product": "/onlineshop/item/B06Y3VLDFB",
    "cart": "/onlineshop/cart",
    "checkout": "/onlineshop/checkout",
    "orders": "/onlineshop/orders",
}

# A string from the pack expected on each page, per language -- one that only
# that page renders, so a page silently falling back to English is caught.
EXPECTED = {
    "german": {
        "home": "Ausgewählte Produkte",
        "results": "Suchergebnisse für „table“",
        "category": "Produkte",
        "product": "In den Warenkorb",
        "cart": "Zur Kasse gehen",
        "checkout": "Bestellung aufgeben",
        "orders": "Ihre Bestellungen",
    },
    "mandarin": {
        "home": "精选商品",
        "results": "“table”的搜索结果",
        "category": "件商品",
        "product": "加入购物车",
        "cart": "去结算",
        "checkout": "提交订单",
        "orders": "您的订单",
    },
}

# English chrome that must be gone from every translated page. Catalog text
# stays English by design, so these are chosen not to occur in any product
# title or description of the pages above.
ENGLISH_CHROME = ["Return to List of Apps", "Featured products", "Add to Cart",
                  "Proceed to Checkout", "Place Order", "Your orders", "Your cart",
                  "Search products", "Order summary"]


def text_of(page: str) -> str:
    """The visible text of a page (stylesheet and tags stripped, entities
    decoded), so assertions are not fooled by class names or attributes."""
    body = page.split("</style>")[-1]
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))


def html_lang(page: str) -> str:
    match = re.search(r'<html[^>]*\blang="([^"]+)"', page)
    return match.group(1) if match else ""


def lang_client(tmp_path, language, *extra):
    overrides = [f"apps/onlineshop/content={language}", "apps.onlineshop.product_images=glyphs"]
    return build_client(tmp_path, overrides + list(extra))


def load_pack(name: str) -> dict:
    return yaml.safe_load((CONTENT / f"{name}.yaml").read_text(encoding="utf-8"))


def code_labels() -> dict:
    """Every ``L("<key>", "<english>")`` in main.py, plus the two keys each
    ``L_count("<key>", n, "<one>", "<many>")`` expands to."""
    source = MAIN.read_text(encoding="utf-8")
    string = r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')"""
    labels = {}
    for m in re.finditer(rf'\bL\(\s*"([a-z_]+)",\s*{string}', source):
        labels[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    for m in re.finditer(rf'\bL_count\(\s*"([a-z_]+)",[^,]+,\s*{string},\s*{string}', source):
        key = m.group(1)
        labels[f"{key}_one_label"] = m.group(2) if m.group(2) is not None else m.group(3)
        labels[f"{key}_label"] = m.group(4) if m.group(4) is not None else m.group(5)
    return labels


class TestLabelPacks:
    """The `*_label` keys: one English source of truth, translated in full."""

    def test_the_code_reads_a_sensible_number_of_labels(self):
        # Guards the regex in `code_labels` -- if it stopped matching, every
        # test below would pass vacuously.
        assert len(code_labels()) > 60

    def test_every_label_the_code_reads_is_in_the_default_pack_verbatim(self):
        """`default.yaml` and the in-code fallback carry the same English, so
        a pack without a key and a pack with it render identically."""
        default = load_pack("default")
        mismatched = {
            key: (english, default.get(key))
            for key, english in code_labels().items()
            if default.get(key) != english
        }
        assert not mismatched

    @pytest.mark.parametrize("language", LANGS)
    def test_every_english_label_is_translated(self, language):
        default, pack = load_pack("default"), load_pack(language)
        english = {k for k in default if k.endswith("_label")}
        assert not english - set(pack), "untranslated keys"
        # Placeholders must survive translation or `.format` raises.
        for key in english:
            assert set(re.findall(r"{(\w+)}", default[key])) == set(
                re.findall(r"{(\w+)}", pack[key])
            ), key

    @pytest.mark.parametrize("language", LANGS)
    def test_packs_declare_their_language_and_translate_the_chrome(self, language):
        pack = load_pack(language)
        assert pack["content_language"] == language
        default = load_pack("default")
        assert set(pack["categories"]) == set(default["categories"])
        assert pack["categories"] != default["categories"]
        assert pack["description"] != default["description"]


class TestMandarinPack:
    def test_it_composes_and_keeps_the_catalog(self, tmp_path):
        client = lang_client(tmp_path, "mandarin")
        assert onlineshop_has_catalog(client.app.config)
        assert shop.language() == "mandarin"
        # The catalog stays English: the purchase tasks name products by
        # their catalog title.
        assert shop._row(shop.products, "B06Y3VLDFB").title.startswith("Vhomes Lights")

    def test_it_renders(self, tmp_path):
        body = lang_client(tmp_path, "mandarin").get("/onlineshop").text
        assert html_lang(body) == "zh-Hans"
        text = text_of(body)
        assert "电子产品" in text and "您值得信赖的开放商城" in text
        assert "返回应用列表" in text


class TestEnglishIsTheReference:
    """English must render the text every task and test was written against."""

    @pytest.mark.parametrize("pack", ["fixture", "webshop", "default"])
    def test_english_packs_say_html_lang_en(self, tmp_path, pack):
        client = build_client(tmp_path, [f"apps/onlineshop/content={pack}"])
        assert html_lang(client.get("/onlineshop").text) == "en"

    def test_english_chrome_is_unchanged(self, client):
        text = text_of(client.get("/onlineshop").text)
        for phrase in ["Shop", "Search", "Orders", "Cart", "Featured products",
                       "View details", "Return to List of Apps"]:
            assert phrase in text
        cart = text_of(client.get("/onlineshop/cart").text)
        assert "Proceed to Checkout" in cart and "Order summary" in cart
        orders = text_of(client.get("/onlineshop/orders").text)
        assert "Order 09bd3dfb" in orders and "Shipping" in orders

    def test_a_config_without_the_labels_still_reads_english(self, client):
        """`L` falls back to the code's English for a key a pack lacks."""
        assert shop.L("no_such_label", "Fallback {x}", x=1) == "Fallback 1"


@pytest.fixture
def client(tmp_path):
    return build_client(tmp_path)


class TestTranslatedPages:
    @pytest.fixture(params=LANGS, scope="class")
    def language(self, request):
        return request.param

    @pytest.fixture(scope="class")
    def pages(self, tmp_path_factory, language):
        client = lang_client(tmp_path_factory.mktemp(language), language)
        return {name: client.get(path) for name, path in PAGES.items()}

    @pytest.mark.parametrize("page", list(PAGES))
    def test_page_is_translated(self, pages, language, page):
        response = pages[page]
        assert response.status_code == 200
        assert html_lang(response.text) == HTML_LANG[language]
        text = text_of(response.text)
        assert EXPECTED[language][page] in text
        for phrase in ENGLISH_CHROME:
            assert phrase not in text, phrase

    def test_prices_are_formatted_as_in_english(self, pages):
        """Figures are identical in every language: tasks parse them."""
        text = text_of(pages["cart"].text)
        assert "$877.80" in text and "$897.79" in text

    def test_stored_status_is_untouched_but_its_pill_is_translated(self, pages, language):
        text = text_of(pages["orders"].text)
        assert {"german": "Zugestellt", "mandarin": "已送达"}[language] in text

    def test_form_fields_keep_their_names(self, pages):
        """Only the labels move; the names a checkout posts do not."""
        body = pages["checkout"].text
        for field in ["name", "address", "card_number", "card_expiry", "card_cvv", "card_name"]:
            assert f'name="{field}"' in body

    def test_checkout_errors_are_translated(self, tmp_path, language):
        client = lang_client(tmp_path, language)
        response = client.post("/onlineshop/checkout", data={
            "name": "x", "address": "y", **GOOD_CARD, "card_expiry": "nonsense"})
        expected = {"german": "Ablaufdatum", "mandarin": "有效期"}[language]
        assert expected in text_of(response.text)

    def test_pagination_and_empty_states_are_translated(self, tmp_path, language):
        client = lang_client(tmp_path, language)
        listing = text_of(client.get("/onlineshop/search/all/2").text)
        assert {"german": "Seite 2 von", "mandarin": "第 2 页"}[language] in listing
        empty = text_of(client.get("/onlineshop/search/zzzzqqqq/1").text)
        assert {"german": "Keine passenden Produkte",
                "mandarin": "没有匹配的商品"}[language] in empty


class TestPurchaseUnderTranslation:
    """The shipped purchase task stays winnable when the shop is translated.

    Driven the way an agent would: search by the title the goal quotes, open
    the result, add it, and check out with the card -- then score the shipped
    `BuyWithCardTask` against the before/after states.
    """

    @pytest.mark.parametrize("language", LANGS)
    def test_buy_the_console_table(self, tmp_path, language):
        from tests.test_buy_with_card import cross_state

        spec = OmegaConf.load(config_dir() / "tasks" / "openbanking.yaml")
        task = instantiate(spec["buy_the_console_table_with_the_business_card"])
        assert isinstance(task, BuyWithCardTask)

        client = lang_client(tmp_path, language, "apps.onlineshop.cart=[]")
        initial = cross_state(client)

        results = client.post("/onlineshop/search",
                              data={"search_query": "Vhomes Lights Reclaimed Wood Console Table"})
        assert f"/onlineshop/item/{task.sku}" in results.text
        product = client.get(f"/onlineshop/item/{task.sku}")
        assert f'action="/onlineshop/cart/add/{task.sku}"' in product.text
        client.post(f"/onlineshop/cart/add/{task.sku}", data={"quantity": 1})
        response = client.post(
            "/onlineshop/checkout",
            data={"name": task.ship_to_name, "address": task.ship_to_address, **GOOD_CARD},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/onlineshop/orders"
        assert task.check_if_task_is_complete(copy.deepcopy(initial), cross_state(client))


class TestThumbnailTone:
    """The generated swatches follow the theme's `tone` asset."""

    def _swatch(self, tmp_path, theme):
        client = build_client(tmp_path, [f"apps/theme={theme}"])
        body = client.get("/onlineshop").text.split("</style>")[-1]
        return re.findall(r'<rect[^>]*fill="([^"]+)"', body)

    @pytest.mark.parametrize("theme", ["default", "challenging_font", "solarized"])
    def test_light_themes_keep_the_pastel_tiles(self, tmp_path, theme):
        fills = self._swatch(tmp_path, theme)
        assert fills and all(f.endswith("88%)") for f in fills)

    @pytest.mark.parametrize("theme", ["dark", "meta_dark", "vscode_dark"])
    def test_dark_themes_get_deep_tiles(self, tmp_path, theme):
        fills = self._swatch(tmp_path, theme)
        assert fills and all(f.endswith("22%)") for f in fills)

    def test_mono_drops_the_hue(self, tmp_path):
        fills = self._swatch(tmp_path, "mono")
        assert fills and set(fills) == {"#ffffff"}


class TestLayoutMatrix:
    """Every layout under every non-default axis value, alone and combined."""

    VARIANTS = {
        "dark": ["apps/theme=dark"],
        "challenging_font": ["apps/theme=challenging_font"],
        "german": ["apps/onlineshop/content=german", "apps.onlineshop.product_images=glyphs"],
        "mandarin": ["apps/onlineshop/content=mandarin", "apps.onlineshop.product_images=glyphs"],
        "mandarin_dark": ["apps/onlineshop/content=mandarin", "apps/theme=dark",
                          "apps.onlineshop.product_images=glyphs"],
    }
    MARKER = {"default": 'class="product-row"', "grid": 'class="product-grid"',
              "compact_table": 'class="product-table"'}

    @pytest.mark.parametrize("variant", list(VARIANTS))
    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_layout_renders(self, tmp_path, layout, variant):
        client = build_client(tmp_path, [f"apps/onlineshop/layout={layout}",
                                         *self.VARIANTS[variant]])
        for path in ["/onlineshop", "/onlineshop/search/table/1"]:
            response = client.get(path)
            assert response.status_code == 200
            assert self.MARKER[layout] in response.text.split("</style>")[-1]
        for path in ["/onlineshop/cart", "/onlineshop/checkout", "/onlineshop/orders"]:
            assert client.get(path).status_code == 200

    @pytest.mark.parametrize("language,header", [("german", "Abteilung"), ("mandarin", "分类")])
    def test_compact_table_headers_are_translated(self, tmp_path, language, header):
        client = lang_client(tmp_path, language, "apps/onlineshop/layout=compact_table")
        assert f"<th>{header}</th>" in client.get("/onlineshop").text

    def test_challenging_font_reaches_the_shop(self, tmp_path):
        body = build_client(tmp_path, ["apps/theme=challenging_font"]).get("/onlineshop").text
        assert "Brush Script MT" in body
