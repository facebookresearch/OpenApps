"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for `BuyWithCardTask` -- the reward for a shop purchase paid by card.

These score the task against states taken from the *running apps* rather than
a hand-built fixture: seed the shop and the bank, snapshot the cross-app
state, drive a real checkout through the shop's HTTP client, snapshot again.
That is the only way to catch the failure mode that matters here, which is the
target state drifting away from what a checkout actually writes (a renamed
field, a changed rounding, a second ledger row nobody expected).

`tests/test_card_payments.py` covers the authorization itself and
`tests/test_onlineshop.py::TestCheckout` the shop's side of the form; neither
knows anything about rewards.
"""

import contextlib
import copy
import io

import pytest
from hydra import compose, initialize
from hydra.utils import instantiate
from omegaconf import OmegaConf
from starlette.testclient import TestClient

from open_apps import config_dir
from open_apps.apps.openbanking_app import main as bank
from open_apps.tasks.tasks import (
    AddToDoTask,
    BuyWithCardTask,
    CompositeTask,
    DeclinedCardPurchaseTask,
)

from tests.test_onlineshop import GOOD_CARD, build_client

# A product from `content=fixture`, the small mechanical catalog the shop's
# tests run on. 249.00 sits far inside the seeded card's 8715.81 of headroom.
SKU = "elec-hdph-001"
PRICE = 249.0
OPTIONS = {"color": "navy"}
LAST4 = "2043"

SHIP_TO = {"ship_to_name": "Dana Reyes", "ship_to_address": "44 Wharf Street"}

# The seeded card's headroom, and the grace band `apps.openbanking` allows on
# top of it. Both are asserted against the composed config in
# `TestShippedTaskConfigs`, so a seed change breaks loudly rather than quietly
# moving these tests into a different authorization band.
HEADROOM = 8715.81
GRACE = 10.0

# 23 x 379.00 = 8717.00, which is 1.19 past the headroom and so inside the
# grace band: the bank takes it and posts an OVERLIMIT NOTICE alongside.
GRACE_SKU = "elec-mntr-002"
GRACE_PRICE = 379.0
GRACE_QTY = 23
GRACE_OPTIONS = {"size": "27 inch"}
GRACE_TITLE = "27-inch Monitor"

# 20 x 899.00 = 17980.00, far past headroom + grace, so the charge is refused:
# no order, balances untouched, one zero-amount DECLINED row on the card.
DECLINE_SKU = "furn-table-201"
DECLINE_PRICE = 899.0
DECLINE_QTY = 20
DECLINE_TITLE = "Oak Dining Table"

# The purchase tasks in `config/tasks/openbanking.yaml`.
SHIPPED = [
    "buy_the_console_table_with_the_business_card",
    "buy_the_console_table_and_log_the_charge",
    "buy_the_mirrors_just_over_the_card_limit",
    "attempt_the_copier_far_over_the_card_limit",
]


def task(**overrides) -> BuyWithCardTask:
    """The task that the default purchase below should satisfy."""
    fields = {
        "goal": "Buy the headphones on the business card.",
        "sku": SKU,
        "unit_price": PRICE,
        "card_last4": LAST4,
        **SHIP_TO,
    }
    fields.update(overrides)
    return BuyWithCardTask(**fields)


def cross_state(shop_client) -> dict:
    """The slice of `get_current_state` these tasks actually read.

    The other apps are stubbed empty rather than stood up: `preprocess`
    indexes `todo`/`calendar`/`messenger`/`map` directly, and `compare`
    requires both states to carry the same keys, but nothing here touches
    their contents.
    """
    return {
        "todo": [],
        "calendar": [],
        "map": [],
        "messenger": [],
        "online_shop": shop_client.get("/onlineshop_all").json(),
        "openbanking": TestClient(bank.app).get("/openbanking_all").json(),
    }


def buy(shop_client, **overrides):
    """Check out the cart with the correct card unless told otherwise."""
    form = {"name": SHIP_TO["ship_to_name"], "address": SHIP_TO["ship_to_address"]}
    form.update(GOOD_CARD)
    form.update(overrides)
    response = shop_client.post("/onlineshop/checkout", data=form, follow_redirects=False)
    assert response.headers["location"] == "/onlineshop/orders", "checkout was declined"
    return response


def buy_expecting_decline(shop_client, **overrides):
    """Check out expecting a refusal, and return where the shopper is sent.

    A decline bounces back to `/onlineshop/checkout` with the reason in the
    query string, rather than on to `/onlineshop/orders`. The cart survives
    because the handler returns before it writes an order: it only clears the
    lines it managed to turn into one.
    """
    form = {"name": SHIP_TO["ship_to_name"], "address": SHIP_TO["ship_to_address"]}
    form.update(GOOD_CARD)
    form.update(overrides)
    response = shop_client.post("/onlineshop/checkout", data=form, follow_redirects=False)
    location = response.headers.get("location", "")
    assert location.startswith("/onlineshop/checkout?error="), "checkout was accepted"
    return location


def clear_cart(shop_client):
    """Empty the cart through the UI route, the way an agent giving up would.

    `/onlineshop_all` deliberately omits the cart row id, so the ids come from
    the shop module directly -- the same shortcut `cross_state` takes to reach
    the bank.
    """
    from open_apps.apps.onlineshop_app import main as onlineshop

    for row in list(onlineshop.cart_items()):
        shop_client.post(
            f"/onlineshop/cart/remove/{row.id}", follow_redirects=False
        )
    assert shop_client.get("/onlineshop_all").json()["cart"] == []


def add_to_cart(shop_client, sku, quantity=1, **options):
    """Put a line in the cart the way the item page does."""
    form = {"quantity": quantity}
    form.update({f"option_{name}": value for name, value in options.items()})
    shop_client.post(
        f"/onlineshop/cart/add/{sku}", data=form, follow_redirects=False
    )


@pytest.fixture
def shop(tmp_path):
    """A shop that starts with an empty cart and one line added through the UI.

    The fixture pack seeds two cart lines; clearing them and adding the one
    line back through `/onlineshop/cart/add` is both simpler than deleting by
    row id (`/onlineshop_all` does not expose one) and closer to the episode
    an agent actually runs.
    """
    client = build_client(tmp_path, overrides=["apps.onlineshop.cart=[]"])
    assert client.get("/onlineshop_all").json()["cart"] == []
    add_to_cart(client, SKU, **OPTIONS)
    return client


@pytest.fixture
def empty_shop(tmp_path):
    """A shop with nothing in the cart at all.

    The over-the-limit tests below put their own line in, at their own
    quantity, and the declined one scores *on* the cart -- so a pre-seeded
    line from the `shop` fixture would be part of the target.
    """
    client = build_client(tmp_path, overrides=["apps.onlineshop.cart=[]"])
    assert client.get("/onlineshop_all").json()["cart"] == []
    return client


def fails(check) -> bool:
    """Run a negative check with `compare`'s diff printout suppressed."""
    with contextlib.redirect_stdout(io.StringIO()):
        return check()


class TestTheHappyPath:
    def test_a_real_purchase_scores(self, shop):
        initial = cross_state(shop)
        buy(shop)
        assert task(options=OPTIONS).check_if_task_is_complete(initial, cross_state(shop))

    def test_the_order_and_the_charge_both_have_to_land(self, shop):
        """The target is not just a new order -- it is the bank moving too."""
        initial = cross_state(shop)
        buy(shop)
        current = cross_state(shop)

        card = next(a for a in current["openbanking"]["accounts"] if a["id"] == 2)
        start = next(a for a in initial["openbanking"]["accounts"] if a["id"] == 2)
        assert card["available_credit"] == round(start["available_credit"] - PRICE, 2)
        assert current["online_shop"]["orders"][-1]["card_last4"] == LAST4

    def test_a_quantity_above_one_scores_at_the_multiplied_total(self, shop):
        # Adding the same sku and options again folds into the existing line.
        add_to_cart(shop, SKU, quantity=2, **OPTIONS)
        assert shop.get("/onlineshop_all").json()["cart"][0]["quantity"] == 3
        initial = cross_state(shop)
        buy(shop)
        assert task(options=OPTIONS, quantity=3).check_if_task_is_complete(
            initial, cross_state(shop)
        )

    def test_shipping_details_are_matched_loosely(self, shop):
        """StringSimilarityOperator, so case and punctuation are free."""
        initial = cross_state(shop)
        buy(shop, name="dana reyes", address="44 Wharf Street.")
        assert task(options=OPTIONS).check_if_task_is_complete(initial, cross_state(shop))


class TestWhatMustFail:
    def test_doing_nothing_fails(self, shop):
        initial = cross_state(shop)
        assert fails(
            lambda: task(options=OPTIONS).check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_buying_the_wrong_quantity_fails(self, shop):
        initial = cross_state(shop)
        buy(shop)
        assert fails(
            lambda: task(options=OPTIONS, quantity=2).check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_a_purchase_left_in_the_cart_fails(self, shop):
        """Buying the right thing is not enough if the cart gained junk."""
        initial = cross_state(shop)
        buy(shop)
        add_to_cart(shop, "home-skil-103", size="10 inch")
        assert fails(
            lambda: task(options=OPTIONS).check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_a_second_order_fails(self, shop):
        initial = cross_state(shop)
        buy(shop)
        add_to_cart(shop, "home-skil-103", size="10 inch")
        buy(shop)
        assert fails(
            lambda: task(options=OPTIONS).check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_the_wrong_shipping_name_fails(self, shop):
        initial = cross_state(shop)
        buy(shop, name="Someone Else")
        assert fails(
            lambda: task(options=OPTIONS).check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_a_card_the_bank_does_not_hold_fails_to_build_a_target(self, shop):
        initial = cross_state(shop)
        buy(shop)
        assert fails(
            lambda: task(options=OPTIONS, card_last4="9999").check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_a_purchase_past_the_headroom_refuses_a_target(self, shop):
        """Over the limit is a different task -- and a second ledger row."""
        initial = cross_state(shop)
        with pytest.raises(ValueError, match="available credit"):
            task(options=OPTIONS, unit_price=99999.0).get_target_state(initial)

    def test_a_missing_bank_refuses_a_target(self, shop):
        initial = cross_state(shop)
        initial.pop("openbanking")
        with pytest.raises(ValueError, match="shop and the bank"):
            task(options=OPTIONS).get_target_state(initial)


class TestNormalization:
    """What `_normalize_purchases` is allowed to forgive, and what it is not."""

    def test_the_descriptor_still_has_to_name_the_order(self, shop):
        """The premise the substitution rests on: the shop really does echo the
        order id into the ledger, so the token is standing in for something."""
        buy(shop)
        current = cross_state(shop)
        charge = current["openbanking"]["transactions"][-1]
        order_id = current["online_shop"]["orders"][-1]["order_id"]
        assert order_id.upper() in charge["description"]

    def test_a_charge_naming_no_order_fails(self, shop):
        """Swap the order id out of the descriptor: the token no longer matches."""
        initial = cross_state(shop)
        buy(shop)
        current = cross_state(shop)
        current["openbanking"]["transactions"][-1]["description"] = "OPENAPPS SHOP DEADBEEF"
        assert fails(
            lambda: task(options=OPTIONS).check_if_task_is_complete(initial, current)
        ) is False

    def test_the_random_order_id_and_wall_clock_date_are_forgiven(self, shop):
        initial = cross_state(shop)
        buy(shop)
        current = cross_state(shop)
        order = current["online_shop"]["orders"][-1]
        assert order["order_id"] and order["date"], "nothing volatile to forgive"
        assert task(options=OPTIONS).check_if_task_is_complete(initial, current)

    def test_an_unrelated_task_still_sees_the_ids(self, shop):
        """The normalization is opt-in, so a todo task keeps its strictness."""
        initial = cross_state(shop)
        buy(shop)
        todo_task = AddToDoTask(goal="unrelated", todo_name="Call mom", is_done=False)
        assert fails(
            lambda: todo_task.check_if_task_is_complete(initial, cross_state(shop))
        ) is False


class TestComposite:
    def test_a_purchase_plus_a_todo_scores(self, shop):
        initial = cross_state(shop)
        buy(shop)
        current = cross_state(shop)
        headroom = next(
            a for a in current["openbanking"]["accounts"] if a["id"] == 2
        )["available_credit"]
        current["todo"] = [{"title": f"Card headroom {headroom}", "done": False}]

        composite = CompositeTask(
            goal="Buy it, then log what is left on the card.",
            subtasks=[
                task(options=OPTIONS),
                AddToDoTask(
                    goal="Log the headroom.",
                    todo_name=f"Card headroom {headroom}",
                    is_done=False,
                ),
            ],
        )
        assert composite.check_if_task_is_complete(initial, current)

    def test_the_purchase_alone_does_not_satisfy_the_composite(self, shop):
        initial = cross_state(shop)
        buy(shop)
        composite = CompositeTask(
            goal="Buy it, then log what is left on the card.",
            subtasks=[
                task(options=OPTIONS),
                AddToDoTask(goal="Log it.", todo_name="Card headroom 1.00", is_done=False),
            ],
        )
        assert fails(
            lambda: composite.check_if_task_is_complete(initial, cross_state(shop))
        ) is False

    def test_the_todo_alone_does_not_satisfy_the_composite(self, shop):
        initial = cross_state(shop)
        current = cross_state(shop)
        current["todo"] = [{"title": "Card headroom 8466.81", "done": False}]
        composite = CompositeTask(
            goal="Buy it, then log what is left on the card.",
            subtasks=[
                task(options=OPTIONS),
                AddToDoTask(
                    goal="Log it.", todo_name="Card headroom 8466.81", is_done=False
                ),
            ],
        )
        assert fails(
            lambda: composite.check_if_task_is_complete(initial, current)
        ) is False


class TestTheGraceBand:
    """A charge a few dollars past the headroom, which the bank still takes.

    The interesting half is the *second* ledger row: `authorize_card_purchase`
    posts an OVERLIMIT NOTICE naming the overage, so a target that only knows
    about the purchase itself fails the diff on a purchase that succeeded.
    """

    def grace_task(self, **overrides) -> BuyWithCardTask:
        fields = {
            "goal": "Buy 23 monitors on the business card.",
            "sku": GRACE_SKU,
            "unit_price": GRACE_PRICE,
            "quantity": GRACE_QTY,
            "card_last4": LAST4,
            "options": GRACE_OPTIONS,
            "overlimit_grace": GRACE,
            **SHIP_TO,
        }
        fields.update(overrides)
        return BuyWithCardTask(**fields)

    def test_the_premise(self, empty_shop):
        """The charge really does land in the band, not either side of it."""
        total = round(GRACE_PRICE * GRACE_QTY, 2)
        assert HEADROOM < total <= HEADROOM + GRACE

    def test_a_purchase_inside_the_band_scores(self, empty_shop):
        add_to_cart(empty_shop, GRACE_SKU, quantity=GRACE_QTY, **GRACE_OPTIONS)
        initial = cross_state(empty_shop)
        buy(empty_shop)
        assert self.grace_task().check_if_task_is_complete(
            initial, cross_state(empty_shop)
        )

    def test_the_bank_really_posts_the_overlimit_row(self, empty_shop):
        """The premise the target rests on -- two rows, not one."""
        add_to_cart(empty_shop, GRACE_SKU, quantity=GRACE_QTY, **GRACE_OPTIONS)
        before = len(cross_state(empty_shop)["openbanking"]["transactions"])
        buy(empty_shop)
        txns = cross_state(empty_shop)["openbanking"]["transactions"]
        assert len(txns) == before + 2
        assert txns[-1]["type"] == "Fee"
        assert "$1.19" in txns[-1]["description"]
        assert txns[-1]["amount"] == 0.0
        # Order matters: `/openbanking_all` sorts by id and the charge is
        # inserted first, so the notice is last.
        assert txns[-2]["amount"] == -round(GRACE_PRICE * GRACE_QTY, 2)

    def test_a_target_without_the_notice_fails(self, empty_shop):
        """Zero grace is the old behaviour: refuse rather than half-score."""
        add_to_cart(empty_shop, GRACE_SKU, quantity=GRACE_QTY, **GRACE_OPTIONS)
        initial = cross_state(empty_shop)
        buy(empty_shop)
        strict = self.grace_task(overlimit_grace=0.0)
        with pytest.raises(ValueError, match="overlimit grace"):
            strict.get_target_state(initial)
        assert fails(
            lambda: strict.check_if_task_is_complete(initial, cross_state(empty_shop))
        ) is False

    def test_the_wrong_overage_in_the_notice_fails(self, empty_shop):
        add_to_cart(empty_shop, GRACE_SKU, quantity=GRACE_QTY, **GRACE_OPTIONS)
        initial = cross_state(empty_shop)
        buy(empty_shop)
        current = cross_state(empty_shop)
        current["openbanking"]["transactions"][-1]["description"] = (
            "OVERLIMIT NOTICE - credit limit exceeded by $99.99"
        )
        assert fails(
            lambda: self.grace_task().check_if_task_is_complete(initial, current)
        ) is False

    def test_doing_nothing_fails(self, empty_shop):
        initial = cross_state(empty_shop)
        assert fails(
            lambda: self.grace_task().check_if_task_is_complete(
                initial, cross_state(empty_shop)
            )
        ) is False

    def test_past_the_band_refuses_a_target(self, empty_shop):
        initial = cross_state(empty_shop)
        with pytest.raises(ValueError, match="DeclinedCardPurchaseTask"):
            self.grace_task(quantity=DECLINE_QTY, unit_price=DECLINE_PRICE)\
                .get_target_state(initial)


class TestADeclinedPurchase:
    """Past the grace band: the cart line and the bank's declined row are the reward."""

    def declined_task(self, **overrides) -> DeclinedCardPurchaseTask:
        fields = {
            "goal": "Try to buy 20 oak tables on the business card.",
            "sku": DECLINE_SKU,
            "unit_price": DECLINE_PRICE,
            "title": DECLINE_TITLE,
            "quantity": DECLINE_QTY,
            "card_last4": LAST4,
            "overlimit_grace": GRACE,
        }
        fields.update(overrides)
        return DeclinedCardPurchaseTask(**fields)

    def test_the_premise(self):
        total = round(DECLINE_PRICE * DECLINE_QTY, 2)
        assert total > HEADROOM + GRACE

    def test_a_refused_checkout_scores(self, empty_shop):
        initial = cross_state(empty_shop)
        add_to_cart(empty_shop, DECLINE_SKU, quantity=DECLINE_QTY)
        buy_expecting_decline(empty_shop)
        assert self.declined_task().check_if_task_is_complete(
            initial, cross_state(empty_shop)
        )

    def test_a_refusal_moves_only_the_cart_and_one_ledger_row(self, empty_shop):
        initial = cross_state(empty_shop)
        add_to_cart(empty_shop, DECLINE_SKU, quantity=DECLINE_QTY)
        location = buy_expecting_decline(empty_shop)
        current = cross_state(empty_shop)

        assert "insufficient+available+credit" in location
        assert current["online_shop"]["orders"] == initial["online_shop"]["orders"]
        assert current["online_shop"]["cart"][0]["quantity"] == DECLINE_QTY
        assert current["openbanking"]["accounts"] == initial["openbanking"]["accounts"]

        before = initial["openbanking"]["transactions"]
        after = current["openbanking"]["transactions"]
        assert after[: len(before)] == before
        (declined,) = after[len(before):]
        total = DECLINE_PRICE * DECLINE_QTY
        assert declined["amount"] == 0.0
        assert declined["type"] == "Declined"
        # The merchant, not the descriptor: no order id, because no order.
        assert declined["description"] == (
            f"DECLINED - OPENAPPS SHOP ${total:,.2f} - insufficient available credit"
        )

    def test_filling_the_cart_without_checking_out_fails(self, empty_shop):
        """The reported hole: before the declined row, this state was
        identical to a refused checkout and scored as one."""
        initial = cross_state(empty_shop)
        add_to_cart(empty_shop, DECLINE_SKU, quantity=DECLINE_QTY)
        assert fails(
            lambda: self.declined_task().check_if_task_is_complete(
                initial, cross_state(empty_shop)
            )
        ) is False

    def test_a_typo_before_the_refusal_still_scores(self, empty_shop):
        """A wrong CVV writes nothing, so correcting it costs nothing."""
        initial = cross_state(empty_shop)
        add_to_cart(empty_shop, DECLINE_SKU, quantity=DECLINE_QTY)
        location = buy_expecting_decline(empty_shop, card_cvv="999")
        assert "insufficient" not in location
        buy_expecting_decline(empty_shop)
        assert self.declined_task().check_if_task_is_complete(
            initial, cross_state(empty_shop)
        )

    def test_a_second_refused_attempt_fails(self, empty_shop):
        initial = cross_state(empty_shop)
        add_to_cart(empty_shop, DECLINE_SKU, quantity=DECLINE_QTY)
        buy_expecting_decline(empty_shop)
        buy_expecting_decline(empty_shop)
        assert fails(
            lambda: self.declined_task().check_if_task_is_complete(
                initial, cross_state(empty_shop)
            )
        ) is False

    def test_the_decline_does_not_name_the_shortfall(self, empty_shop):
        """Why the reporting sub-task is worth points: the figure is not on
        the page the agent is looking at when it fails."""
        add_to_cart(empty_shop, DECLINE_SKU, quantity=DECLINE_QTY)
        location = buy_expecting_decline(empty_shop)
        page = empty_shop.get(location).text
        for figure in ("12784", "8715.81", "8,715.81"):
            assert figure not in location
            assert figure not in page

    def test_doing_nothing_fails(self, empty_shop):
        """The failure mode the cart assertion exists to catch."""
        initial = cross_state(empty_shop)
        assert fails(
            lambda: self.declined_task().check_if_task_is_complete(
                initial, cross_state(empty_shop)
            )
        ) is False

    def test_an_empty_cart_after_giving_up_fails(self, empty_shop):
        initial = cross_state(empty_shop)
        add_to_cart(empty_shop, DECLINE_SKU, quantity=DECLINE_QTY)
        buy_expecting_decline(empty_shop)
        clear_cart(empty_shop)
        assert fails(
            lambda: self.declined_task().check_if_task_is_complete(
                initial, cross_state(empty_shop)
            )
        ) is False

    def test_the_wrong_quantity_fails(self, empty_shop):
        initial = cross_state(empty_shop)
        add_to_cart(empty_shop, DECLINE_SKU, quantity=DECLINE_QTY - 1)
        buy_expecting_decline(empty_shop)
        assert fails(
            lambda: self.declined_task().check_if_task_is_complete(
                initial, cross_state(empty_shop)
            )
        ) is False

    def test_a_charge_that_would_be_approved_refuses_a_target(self, empty_shop):
        initial = cross_state(empty_shop)
        with pytest.raises(ValueError, match="BuyWithCardTask"):
            self.declined_task(quantity=1).get_target_state(initial)

    def test_a_charge_inside_the_grace_band_refuses_a_target(self, empty_shop):
        """The boundary: the bank takes this one, so it is not this task."""
        initial = cross_state(empty_shop)
        with pytest.raises(ValueError, match="BuyWithCardTask"):
            self.declined_task(
                sku=GRACE_SKU, unit_price=GRACE_PRICE, quantity=GRACE_QTY,
                title=GRACE_TITLE,
            ).get_target_state(initial)

    def test_the_shortfall_is_the_figure_the_todo_asks_for(self, empty_shop):
        initial = cross_state(empty_shop)
        expected = round(DECLINE_PRICE * DECLINE_QTY - HEADROOM, 2)
        assert self.declined_task().shortfall(initial) == expected

    def test_the_composite_needs_both_halves(self, empty_shop):
        initial = cross_state(empty_shop)
        shortfall = self.declined_task().shortfall(initial)
        composite = CompositeTask(
            goal="Try to buy it, then log how far short the card fell.",
            subtasks=[
                self.declined_task(),
                AddToDoTask(
                    goal="Log the shortfall.",
                    todo_name=f"Card short {shortfall}",
                    is_done=False,
                ),
            ],
        )

        add_to_cart(empty_shop, DECLINE_SKU, quantity=DECLINE_QTY)
        # The todo without the attempt is not enough: the figure is computable
        # from the cart total and the card page without ever checking out.
        current = cross_state(empty_shop)
        current["todo"] = [{"title": f"Card short {shortfall}", "done": False}]
        assert fails(lambda: composite.check_if_task_is_complete(initial, current)) is False

        buy_expecting_decline(empty_shop)
        # The decline alone is not enough.
        assert fails(
            lambda: composite.check_if_task_is_complete(initial, cross_state(empty_shop))
        ) is False

        current = cross_state(empty_shop)
        current["todo"] = [{"title": f"Card short {shortfall}", "done": False}]
        assert composite.check_if_task_is_complete(initial, current)

    def test_buying_it_on_a_card_that_could_pay_fails_the_composite(self, empty_shop):
        """A run that somehow completes the purchase is not a pass."""
        initial = cross_state(empty_shop)
        add_to_cart(empty_shop, DECLINE_SKU, quantity=1)
        buy(empty_shop)
        assert fails(
            lambda: self.declined_task(quantity=1).check_if_task_is_complete(
                initial, cross_state(empty_shop)
            )
        ) is False


class TestShippedTaskConfigs:
    """The two configs in `config/tasks/openbanking.yaml` agree with the apps.

    They target the shop's *default* pack (`webshop`) rather than the fixture
    catalog the tests above use, so their figures are checked against the
    composed config instead of against a purchase.
    """

    @pytest.fixture(scope="class")
    def tasks_cfg(self):
        return OmegaConf.load(config_dir() / "tasks" / "openbanking.yaml")

    @pytest.fixture(scope="class")
    def apps_cfg(self):
        with initialize(version_base=None, config_path="../config/"):
            return compose(config_name="config", overrides=["logs_dir=/tmp/openapps-cfg"])

    @pytest.mark.parametrize("name", SHIPPED)
    def test_the_task_instantiates(self, tasks_cfg, name):
        assert isinstance(instantiate(tasks_cfg[name]), (BuyWithCardTask, CompositeTask))

    @pytest.mark.parametrize("name", SHIPPED)
    def test_target_passes_and_initial_fails(self, tasks_cfg, name, shop):
        """The self-consistency check `test_openbanking.py` runs on every other
        task in the file, run here because these two need a state with both
        apps in it -- see `_buys` there."""
        task = instantiate(tasks_cfg[name])
        initial = cross_state(shop)
        target = task.get_target_state(copy.deepcopy(initial))
        assert task.check_if_task_is_complete(copy.deepcopy(initial), target)
        assert fails(
            lambda: task.check_if_task_is_complete(
                copy.deepcopy(initial), copy.deepcopy(initial)
            )
        ) is False

    def test_the_priced_product_is_in_the_default_catalog(self, tasks_cfg, apps_cfg):
        cfg = tasks_cfg["buy_the_console_table_with_the_business_card"]
        catalog = {p.sku: p for p in apps_cfg.apps.onlineshop.products}
        assert cfg.sku in catalog, "the default catalog no longer carries this sku"
        assert catalog[cfg.sku].price == cfg.unit_price

    def test_the_test_constants_match_the_seeded_card(self, apps_cfg):
        """`HEADROOM` and `GRACE` at the top of this file are the real thing."""
        card = next(
            a
            for a in apps_cfg.apps.openbanking.accounts
            if a.get("kind") == "credit_card"
        )
        assert card.available_credit == HEADROOM
        assert apps_cfg.apps.openbanking.overlimit_grace == GRACE

    def test_the_over_limit_products_are_in_the_default_catalog(
        self, tasks_cfg, apps_cfg
    ):
        """Both figures and the copier's title are pinned, so a catalog
        regeneration has to break here rather than silently rescore."""
        catalog = {p.sku: p for p in apps_cfg.apps.onlineshop.products}
        grace = tasks_cfg["buy_the_mirrors_just_over_the_card_limit"]
        declined = tasks_cfg["attempt_the_copier_far_over_the_card_limit"].subtasks[0]

        for cfg in (grace, declined):
            assert cfg.sku in catalog, f"{cfg.sku} is no longer in the catalog"
            assert catalog[cfg.sku].price == cfg.unit_price

        # The declined task carries the title too -- `/onlineshop_all` joins it
        # onto the cart line the refused checkout leaves behind.
        assert catalog[declined.sku].title.strip() == declined.title.strip()

    def test_the_mirrors_land_inside_the_grace_band(self, tasks_cfg, apps_cfg):
        cfg = tasks_cfg["buy_the_mirrors_just_over_the_card_limit"]
        card = next(
            a
            for a in apps_cfg.apps.openbanking.accounts
            if a.get("kind") == "credit_card"
        )
        grace = apps_cfg.apps.openbanking.overlimit_grace
        total = round(cfg.unit_price * cfg.quantity, 2)
        over_by = round(total - card.available_credit, 2)

        assert cfg.overlimit_grace == grace, "task and app disagree on the band"
        assert 0 < over_by <= grace, f"{total} is not inside the grace band"

    def test_the_copier_lands_past_the_grace_band(self, tasks_cfg, apps_cfg):
        composite = tasks_cfg["attempt_the_copier_far_over_the_card_limit"]
        purchase, todo = composite.subtasks
        card = next(
            a
            for a in apps_cfg.apps.openbanking.accounts
            if a.get("kind") == "credit_card"
        )
        grace = apps_cfg.apps.openbanking.overlimit_grace
        total = round(purchase.unit_price * purchase.quantity, 2)
        over_by = round(total - card.available_credit, 2)

        assert purchase.overlimit_grace == grace
        assert over_by > grace, f"{total} would still authorize"
        # The todo names the shortfall the agent has to compute for itself.
        assert f"{over_by:.2f}" in todo.todo_name

    def test_the_card_can_afford_it(self, tasks_cfg, apps_cfg):
        cfg = tasks_cfg["buy_the_console_table_with_the_business_card"]
        card = next(
            a
            for a in apps_cfg.apps.openbanking.accounts
            if a.get("kind") == "credit_card"
        )
        assert str(card.account_number).endswith(cfg.card_last4)
        assert cfg.unit_price * cfg.quantity <= card.available_credit

    def test_the_descriptor_and_charge_type_match_the_apps(self, tasks_cfg, apps_cfg):
        cfg = tasks_cfg["buy_the_console_table_with_the_business_card"]
        assert cfg.get("descriptor", "OPENAPPS SHOP {order_id}") == (
            apps_cfg.apps.onlineshop.card_descriptor
        )
        assert cfg.get("charge_type", "Card") == apps_cfg.apps.openbanking.purchase_type

    def test_the_declined_row_matches_the_apps(self, tasks_cfg, apps_cfg):
        """The declined task's defaults mirror the bank's content pack and the
        shop's descriptor with the order id dropped -- what checkout passes
        as `merchant`."""
        purchase = instantiate(
            tasks_cfg["attempt_the_copier_far_over_the_card_limit"].subtasks[0]
        )
        bank_cfg = apps_cfg.apps.openbanking
        descriptor = apps_cfg.apps.onlineshop.card_descriptor
        assert purchase.merchant == " ".join(descriptor.format(order_id="").split())
        assert purchase.declined_type == bank_cfg.declined_type
        assert purchase.declined_description == bank_cfg.declined_description

    def test_the_composites_todo_states_the_headroom_after_the_purchase(
        self, tasks_cfg, apps_cfg
    ):
        composite = tasks_cfg["buy_the_console_table_and_log_the_charge"]
        purchase, todo = composite.subtasks
        card = next(
            a
            for a in apps_cfg.apps.openbanking.accounts
            if a.get("kind") == "credit_card"
        )
        expected = round(card.available_credit - purchase.unit_price * purchase.quantity, 2)
        assert f"{expected:.2f}" in todo.todo_name
