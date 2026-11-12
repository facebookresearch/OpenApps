"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

OpenBanking's third layout (``grouped_by_date``), its localized chrome, and
the card-purchase tasks under the german / mandarin bank.

The purchase tasks are scored against states taken from the running shop and
bank, as in ``tests/test_buy_with_card.py``: under a translated bank pack the
ledger rows a checkout posts are in that language ("Karte", "ABGELEHNT - ..."),
and a task config written in English has to score them.
"""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from omegaconf import OmegaConf
from starlette.testclient import TestClient

from open_apps import config_dir, files_store, i18n
from open_apps.apps.openbanking_app import main as bank
from open_apps.apps.openbanking_app import statements
from open_apps.tasks.tasks import BuyWithCardTask, DeclinedCardPurchaseTask

LAYOUTS = ["default", "card_list", "grouped_by_date"]
CONTENTS = ["default", "german", "mandarin"]
LANG_TAG = {"default": "en", "german": "de", "mandarin": "zh-Hans"}
CONTENT_DIR = config_dir() / "apps" / "openbanking" / "content"


def _language(content: str) -> str:
    return "english" if content == "default" else content


def _client(tmp_path: Path, *overrides: str) -> TestClient:
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"logs_dir={tmp_path}", *overrides])
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()):
        bank.set_environment(config.apps)
    return TestClient(bank.app)


def _markup(html: str) -> str:
    html = re.sub(r"<style\b.*?</style\b[^>]*>", "", html, flags=re.S | re.I)
    return re.sub(r"<script\b.*?</script\b[^>]*>", "", html, flags=re.S | re.I)


# ---------------------------------------------------------------------------
# grouped_by_date
# ---------------------------------------------------------------------------
@pytest.fixture
def timeline(tmp_path):
    return _client(tmp_path, "apps/openbanking/layout=grouped_by_date")


class TestGroupedByDate:
    def test_one_heading_per_posting_day(self, timeline):
        html = _markup(timeline.get("/openbanking/accounts/0?expand=1").text)
        headings = re.findall(r'<h3 id="ob-day-\d+" class="ob-day-heading">([^<]*)</h3>', html)
        dates = []
        for txn in bank.txns_for(0):
            day = txn.date
            if not dates or dates[-1] != day:
                dates.append(day)
        assert headings == dates
        assert all(headings)  # every row is posted, so every heading is a date

    def test_rows_carry_no_date_of_their_own(self, timeline):
        html = _markup(timeline.get("/openbanking/accounts/0?expand=1").text)
        rows = re.findall(r'<li [^>]*id="txn-\d+"[^>]*>(.*?)</li>', html, flags=re.S)
        assert len(rows) == len(bank.txns_for(0))
        for row in rows:
            assert "Aug" not in row

    def test_each_row_keeps_its_figures(self, timeline):
        html = _markup(timeline.get("/openbanking/accounts/0?expand=1").text)
        for txn in bank.txns_for(0):
            start = html.index(f'id="txn-{txn.id}"')
            row = html[start : html.index("</li>", start)]
            assert bank.fmt_money(txn.amount) in row
            assert txn.type in row
            assert f"Balance: {bank.fmt_money(txn.balance)}" in row

    def test_truncation_and_filters_still_apply(self, timeline):
        html = _markup(timeline.get("/openbanking/accounts/0").text)
        assert len(re.findall(r'id="txn-\d+"', html)) == 4
        assert "See more activity" in html
        deposits = _markup(timeline.get("/openbanking/accounts/0/ledger?showing=1&expand=1").text)
        assert "-$" not in deposits

    def test_search_with_no_match_is_empty_not_broken(self, timeline):
        html = timeline.get("/openbanking/accounts/0/ledger?q=zzzz").text
        assert "No transactions match your search." in html


# ---------------------------------------------------------------------------
# Localized chrome, html lang, every layout x theme x language
# ---------------------------------------------------------------------------
class TestLocalized:
    @pytest.mark.parametrize("content", CONTENTS)
    def test_back_link_and_html_lang_on_every_page(self, tmp_path, content):
        client = _client(tmp_path, f"apps/openbanking/content={content}")
        back = i18n.t("return_to_apps", _language(content))
        for route in (
            "/openbanking",
            "/openbanking/accounts/0",
            "/openbanking/accounts/1/statements",
        ):
            response = client.get(route)
            assert response.status_code == 200, route
            assert f'<html lang="{LANG_TAG[content]}"' in response.text, route
            html = _markup(response.text)
            assert f">{back}</a>" in html, route
            if content != "default":
                assert "Return to List of Apps" not in html, route

    @pytest.mark.parametrize(
        "content,label,period",
        [
            ("default", "July 2026", "Jul 1, 2026 - Jul 31, 2026"),
            ("german", "Juli 2026", "1. Juli 2026 - 31. Juli 2026"),
            ("mandarin", "2026年7月", "2026年7月1日 - 2026年7月31日"),
        ],
    )
    def test_statement_months_follow_the_pack(self, tmp_path, content, label, period):
        client = _client(tmp_path, f"apps/openbanking/content={content}")
        html = _markup(client.get("/openbanking/accounts/1/statements").text)
        assert f'<div class="ob-statement-month">{label}</div>' in html
        assert f'<div class="ob-statement-period">{period}</div>' in html
        # The file the row downloads keeps its English name in every pack:
        # the View link carries it.
        name = statements.statement_name("BUS SELECT SAVINGS (...8891)", (2026, 7))
        assert name == "BUS SELECT SAVINGS (...8891) — July 2026.pdf"
        account = bank.get_account(1)
        assert statements.statement_path(account.name, (2026, 7)).endswith(name)

    def test_mandarin_descriptions_are_translated(self):
        pack = OmegaConf.load(CONTENT_DIR / "mandarin.yaml")
        descriptions = [t.description for a in pack.accounts for t in a.transactions]
        assert not any(d.startswith(("COOL APPS INC", "CLOUD HOST LLC")) for d in descriptions)

    @pytest.mark.parametrize("content", CONTENTS)
    @pytest.mark.parametrize("theme", ["dark", "challenging_font"])
    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_renders_under_theme_and_language(self, tmp_path, layout, theme, content):
        client = _client(
            tmp_path,
            f"apps/theme={theme}",
            f"apps/openbanking/layout={layout}",
            f"apps/openbanking/content={content}",
        )
        for account in (0, 2):  # a deposit account and the card
            response = client.get(f"/openbanking/accounts/{account}?expand=1")
            assert response.status_code == 200
            assert "--color-fg" in response.text
            assert f'<html lang="{LANG_TAG[content]}"' in response.text
            if theme == "dark":
                assert 'data-theme="dark"' in response.text
            assert len(re.findall(r'id="txn-\d+"', response.text)) == len(bank.txns_for(account))


# ---------------------------------------------------------------------------
# Card purchases under the translated bank
# ---------------------------------------------------------------------------
# The fixture catalog's products, as in tests/test_buy_with_card.py.
SKU, PRICE, OPTIONS = "elec-hdph-001", 249.0, {"color": "navy"}
GRACE_SKU, GRACE_PRICE, GRACE_QTY, GRACE_OPTIONS = "elec-mntr-002", 379.0, 23, {"size": "27 inch"}
DECLINE_SKU, DECLINE_PRICE, DECLINE_QTY, DECLINE_TITLE = "furn-table-201", 899.0, 20, "Oak Dining Table"
SHIP_TO = {"ship_to_name": "Dana Reyes", "ship_to_address": "44 Wharf Street"}
CARD = {"card_number": "9024007155992043", "card_expiry": "09/29", "card_cvv": "418"}


def _holder(content: str) -> str:
    pack = OmegaConf.load(CONTENT_DIR / f"{content}.yaml")
    return next(str(a.holder) for a in pack.accounts if a.get("kind") == "credit_card")


def _wording(content: str) -> dict:
    return OmegaConf.to_container(OmegaConf.load(CONTENT_DIR / f"{content}.yaml"))


@pytest.fixture(params=["german", "mandarin"])
def translated_shop(request, tmp_path):
    from tests.test_onlineshop import build_client

    content = request.param
    client = build_client(
        tmp_path,
        overrides=["apps.onlineshop.cart=[]", f"apps/openbanking/content={content}"],
    )
    return client, content


def _cross_state(shop_client) -> dict:
    return {
        "todo": [],
        "calendar": [],
        "map": [],
        "messenger": [],
        "online_shop": shop_client.get("/onlineshop_all").json(),
        "openbanking": TestClient(bank.app).get("/openbanking_all").json(),
        "files": files_store.snapshot(),
    }


def _add(shop_client, sku, quantity=1, **options):
    form = {"quantity": quantity, **{f"option_{k}": v for k, v in options.items()}}
    shop_client.post(f"/onlineshop/cart/add/{sku}", data=form, follow_redirects=False)


def _checkout(shop_client, content):
    form = {"name": SHIP_TO["ship_to_name"], "address": SHIP_TO["ship_to_address"], **CARD}
    form["card_name"] = _holder(content)
    return shop_client.post("/onlineshop/checkout", data=form, follow_redirects=False)


def _quiet(check) -> bool:
    with contextlib.redirect_stdout(io.StringIO()):
        return bool(check())


class TestPurchasesUnderTranslatedBank:
    def test_a_purchase_scores_with_the_translated_charge_type(self, translated_shop):
        shop, content = translated_shop
        _add(shop, SKU, **OPTIONS)
        initial = _cross_state(shop)
        assert _checkout(shop, content).headers["location"] == "/onlineshop/orders"
        current = _cross_state(shop)
        assert current["openbanking"]["transactions"][-1]["type"] == _wording(content)["purchase_type"]
        task = BuyWithCardTask(
            goal="Buy the headphones.", sku=SKU, unit_price=PRICE, card_last4="2043",
            options=OPTIONS, **SHIP_TO,
        )
        assert _quiet(lambda: task.check_if_task_is_complete(initial, current))
        assert not _quiet(lambda: task.check_if_task_is_complete(initial, initial))

    def test_the_grace_band_notice_scores_in_the_pack_wording(self, translated_shop):
        shop, content = translated_shop
        _add(shop, GRACE_SKU, quantity=GRACE_QTY, **GRACE_OPTIONS)
        initial = _cross_state(shop)
        assert _checkout(shop, content).headers["location"] == "/onlineshop/orders"
        current = _cross_state(shop)
        notice = current["openbanking"]["transactions"][-1]
        assert notice["type"] == _wording(content)["overlimit_type"]
        task = BuyWithCardTask(
            goal="Buy 23 monitors.", sku=GRACE_SKU, unit_price=GRACE_PRICE,
            quantity=GRACE_QTY, card_last4="2043", options=GRACE_OPTIONS, **SHIP_TO,
        )
        assert _quiet(lambda: task.check_if_task_is_complete(initial, current))

    def test_a_refused_checkout_scores_in_the_pack_wording(self, translated_shop):
        shop, content = translated_shop
        initial = _cross_state(shop)
        _add(shop, DECLINE_SKU, quantity=DECLINE_QTY)
        location = _checkout(shop, content).headers.get("location", "")
        assert location.startswith("/onlineshop/checkout?error=")
        current = _cross_state(shop)
        row = current["openbanking"]["transactions"][-1]
        assert row["type"] == _wording(content)["declined_type"]
        task = DeclinedCardPurchaseTask(
            goal="Try to buy 20 tables.", sku=DECLINE_SKU, unit_price=DECLINE_PRICE,
            title=DECLINE_TITLE, quantity=DECLINE_QTY, card_last4="2043",
        )
        assert _quiet(lambda: task.check_if_task_is_complete(initial, current))
        assert not _quiet(lambda: task.check_if_task_is_complete(initial, initial))

    def test_an_explicit_override_is_left_alone(self, translated_shop):
        """Only the English defaults follow the pack; a config that names its
        own wording keeps it (and so fails against the pack's rows)."""
        shop, content = translated_shop
        _add(shop, SKU, **OPTIONS)
        initial = _cross_state(shop)
        _checkout(shop, content)
        task = BuyWithCardTask(
            goal="Buy the headphones.", sku=SKU, unit_price=PRICE, card_last4="2043",
            options=OPTIONS, charge_type="Something else", **SHIP_TO,
        )
        assert not _quiet(lambda: task.check_if_task_is_complete(initial, _cross_state(shop)))

    def test_the_english_bank_keeps_the_english_wording(self, tmp_path):
        from tests.test_onlineshop import build_client

        shop = build_client(tmp_path, overrides=["apps.onlineshop.cart=[]"])
        state = _cross_state(shop)
        target = DeclinedCardPurchaseTask(
            goal="x", sku=DECLINE_SKU, unit_price=DECLINE_PRICE, title=DECLINE_TITLE,
            quantity=DECLINE_QTY, card_last4="2043",
        ).get_target_state(state)
        row = target["openbanking"]["transactions"][-1]
        assert row["type"] == "Declined"
        assert row["description"].startswith("DECLINED - OPENAPPS SHOP ")
