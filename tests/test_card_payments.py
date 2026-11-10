"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for `openbanking_app.authorize_card_purchase`, the one write path.

Separate from `test_openbanking.py` on purpose. That module holds the
read-only guarantees -- most importantly that `/openbanking_all` is
byte-identical across browsing -- behind a module-scoped client whose seeded
tables every test in it shares. A purchase mutates exactly those tables, so
these tests take a fresh per-test seed of their own rather than repointing the
module globals out from under it.

The shop side of the same wiring (the checkout form, what a shopper sees when
a card is declined) lives in `test_onlineshop.py::TestCheckout`.
"""

from pathlib import Path

import pytest
from hydra import compose, initialize

from open_apps.apps.openbanking_app import main as bank
from open_apps.utils import merge_plus_keys

# The card seeded in `config/apps/openbanking/content/default.yaml`.
CARD_ID = 2
PAN = "9024007155992043"
EXPIRY = "09/29"
CVV = "418"
HOLDER = "Cardinal Freight LLC"

DESCRIPTOR = "OPENAPPS SHOP ABCD1234"


def seed(tmp_path, overrides=None):
    """Seed the bank from a freshly composed config and hand back the module."""
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(
            config_name="config",
            overrides=[f"logs_dir={tmp_path}"] + list(overrides or []),
        )
    cfg = merge_plus_keys(cfg)
    Path(cfg.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    bank.set_environment(cfg.apps)
    return bank


@pytest.fixture
def ob(tmp_path):
    return seed(tmp_path)


def charge(amount, **overrides):
    """Authorize `amount` with the correct card unless told otherwise."""
    fields = {
        "number": PAN,
        "expiration": EXPIRY,
        "cvv": CVV,
        "holder": HOLDER,
        "amount": amount,
        "descriptor": DESCRIPTOR,
    }
    fields.update(overrides)
    return bank.authorize_card_purchase(**fields)


def card(ob):
    return ob.get_account(CARD_ID)


def ledger(ob):
    return ob.txns_for(CARD_ID)


class TestCardDetails:
    def test_the_seeded_card_is_chargeable(self, ob):
        assert charge(10.0).approved

    @pytest.mark.parametrize("field,value", [
        ("number", "9024007155990000"),
        ("expiration", "01/27"),
        ("cvv", "999"),
        ("holder", "Someone Else"),
    ])
    def test_a_wrong_field_declines(self, ob, field, value):
        result = charge(10.0, **{field: value})
        assert not result.approved
        assert result.code in {"unknown_card", "bad_details"}

    def test_a_decline_says_nothing_about_which_field_was_wrong(self, ob):
        """Otherwise the CVV is three digits of brute force away."""
        messages = {
            charge(10.0, expiration="01/27").message,
            charge(10.0, cvv="999").message,
            charge(10.0, holder="Someone Else").message,
            charge(10.0, number="9024007155990000").message,
        }
        assert len(messages) == 1

    @pytest.mark.parametrize("typed", ["9024 0071 5599 2043", "9024-0071-5599-2043"])
    def test_pan_punctuation_is_ignored(self, ob, typed):
        assert charge(10.0, number=typed).approved

    @pytest.mark.parametrize(
        "typed", ["09/29", "9/29", "09 / 2029", "9/2029", "0929", "092029"]
    )
    def test_expiry_is_accepted_in_the_forms_a_form_receives(self, ob, typed):
        assert charge(10.0, expiration=typed).approved

    @pytest.mark.parametrize("typed", ["13/29", "00/29", "09/029", "929", "09-29-01", "soon"])
    def test_an_expiry_that_is_not_a_date_declines(self, ob, typed):
        """Not a lucky digit match: "13/29" is no month at all."""
        assert not charge(10.0, expiration=typed).approved

    def test_holder_match_ignores_case_and_spacing(self, ob):
        assert charge(10.0, holder="  cardinal   freight llc ").approved

    def test_a_deposit_account_number_is_not_a_card(self, ob):
        """`find_card` only matches `kind == "credit_card"`."""
        deposit = next(a for a in ob.account_rows() if not a.is_card)
        assert ob.find_card(deposit.account_number) is None
        assert not charge(10.0, number=deposit.account_number).approved

    def test_a_declined_charge_writes_nothing(self, ob):
        """Detail mismatches are typos; fixing one must not cost the task."""
        before_rows = len(ledger(ob))
        before_credit = card(ob).available_credit
        charge(10.0, cvv="999")
        assert len(ledger(ob)) == before_rows
        assert card(ob).available_credit == before_credit


class TestExpiry:
    @pytest.mark.parametrize("typed,parsed", [
        ("09/29", (2029, 9)),
        ("9/29", (2029, 9)),
        ("09 / 2029", (2029, 9)),
        ("9/2029", (2029, 9)),
        ("0929", (2029, 9)),
        ("092029", (2029, 9)),
        ("12/30", (2030, 12)),
    ])
    def test_parse_expiry(self, typed, parsed):
        assert bank.parse_expiry(typed) == parsed

    @pytest.mark.parametrize("typed", ["", None, "13/29", "0/29", "1/2/29", "929", "abcd"])
    def test_parse_expiry_rejects_non_dates(self, typed):
        assert bank.parse_expiry(typed) is None

    def test_the_bank_date_is_pinned_by_config(self, ob):
        assert str(ob.as_of_date()) == "2026-08-26"

    def test_a_card_is_good_through_its_expiry_month(self, tmp_path):
        ob = seed(tmp_path, ["apps.openbanking.as_of_date=2029-09-30"])
        assert not ob.expiry_has_passed((2029, 9))
        assert charge(10.0).approved

    def test_an_expired_card_declines_and_writes_nothing(self, tmp_path):
        ob = seed(tmp_path, ["apps.openbanking.as_of_date=2029-10-01"])
        assert ob.expiry_has_passed((2029, 9))
        before_rows = len(ledger(ob))
        result = charge(10.0)
        assert not result.approved
        assert result.code == "expired"
        # Same vague message as every other detail decline.
        assert result.message == ob.cfg().charge_declined_message
        assert len(ledger(ob)) == before_rows

    def test_null_falls_back_to_the_wall_clock(self, tmp_path):
        from datetime import date

        ob = seed(tmp_path, ["apps.openbanking.as_of_date=null"])
        assert ob.as_of_date() == date.today()


class TestCreditLimit:
    def test_a_charge_inside_the_headroom_approves(self, ob):
        result = charge(round(card(ob).available_credit - 1, 2))
        assert result.approved and result.code == "approved"
        assert result.over_limit_by == 0.0

    def test_the_exact_headroom_approves_without_an_overage(self, ob):
        result = charge(card(ob).available_credit)
        assert result.approved and result.code == "approved"
        assert result.over_limit_by == 0.0
        assert card(ob).available_credit == 0.0

    def test_a_cent_past_the_limit_still_approves_inside_the_grace(self, ob):
        result = charge(round(card(ob).available_credit + 0.01, 2))
        assert result.approved and result.code == "over_limit_grace"
        assert result.over_limit_by == 0.01

    def test_the_full_ten_dollar_grace_approves(self, ob):
        result = charge(round(card(ob).available_credit + 10.00, 2))
        assert result.approved and result.code == "over_limit_grace"
        assert result.over_limit_by == 10.00
        assert card(ob).available_credit == -10.00

    def test_a_cent_past_the_grace_declines(self, ob):
        headroom = card(ob).available_credit
        owed = card(ob).present_balance
        result = charge(round(headroom + 10.01, 2))
        assert not result.approved
        assert result.code == "insufficient_credit"
        assert card(ob).available_credit == headroom
        assert card(ob).present_balance == owed

    def test_the_grace_is_configurable(self, tmp_path):
        """`overlimit_grace: 0` is a hard limit again."""
        ob = seed(tmp_path, ["apps.openbanking.overlimit_grace=0"])
        headroom = card(ob).available_credit
        assert not charge(round(headroom + 0.01, 2)).approved
        assert charge(headroom).approved

    def test_a_second_purchase_sees_the_first_one(self, ob):
        """The whole point of debiting: the credit check has to compound."""
        headroom = card(ob).available_credit
        assert charge(round(headroom - 5.00, 2)).approved
        # 5.00 of headroom left, plus 10.00 of grace.
        assert charge(15.00).approved
        assert not charge(0.02).approved

    def test_a_zero_or_negative_amount_is_refused(self, ob):
        assert not charge(0).approved
        assert not charge(-50.0).approved


class TestLedgerRows:
    def test_an_ordinary_purchase_posts_one_row(self, ob):
        before = len(ledger(ob))
        charge(120.00)
        rows = ledger(ob)
        assert len(rows) == before + 1
        assert rows[0].description == DESCRIPTOR
        assert rows[0].amount == -120.00

    def test_the_charge_row_uses_the_configured_type(self, ob):
        charge(120.00)
        assert ledger(ob)[0].type == ob.cfg().purchase_type

    def test_the_charge_posts_pending(self, ob):
        """No `datetime.now()` in the reward payload -- see the module docstring."""
        charge(120.00)
        row = ledger(ob)[0]
        assert row.date is None
        assert row.balance is None

    def test_the_new_row_sorts_above_the_seeded_ones(self, ob):
        seeded_top = ledger(ob)[0]
        charge(120.00)
        rows = ledger(ob)
        assert rows[0].description == DESCRIPTOR
        assert rows[1].description == seeded_top.description

    def test_an_overlimit_purchase_posts_a_second_row_naming_the_overage(self, ob):
        before = len(ledger(ob))
        charge(round(card(ob).available_credit + 7.19, 2))
        rows = ledger(ob)
        assert len(rows) == before + 2
        # Charge first, then the notice about it.
        assert rows[0].description == DESCRIPTOR
        notice = rows[1]
        assert notice.type == ob.cfg().overlimit_type
        assert "$7.19" in notice.description
        # The notice reports, it does not charge.
        assert notice.amount == 0.0

    def test_no_overlimit_row_when_the_charge_fits(self, ob):
        before = len(ledger(ob))
        charge(round(card(ob).available_credit, 2))
        rows = ledger(ob)
        assert len(rows) == before + 1
        assert rows[0].description == DESCRIPTOR

    def test_row_ids_stay_unique(self, ob):
        charge(50.0)
        charge(round(card(ob).available_credit + 5.0, 2))
        ids = [t.id for t in ob.transactions()]
        assert len(ids) == len(set(ids))

    def test_a_refusal_for_credit_posts_one_declined_row(self, ob):
        """So a refused checkout is distinguishable from one never attempted."""
        before = len(ledger(ob))
        attempted = round(card(ob).available_credit + 5000.0, 2)
        charge(attempted, merchant="OPENAPPS SHOP")
        rows = ledger(ob)
        assert len(rows) == before + 1
        row = rows[0]
        assert row.type == ob.cfg().declined_type
        assert row.amount == 0.0
        assert row.date is None and row.balance is None
        assert row.description == (
            f"DECLINED - OPENAPPS SHOP {ob.fmt_money(attempted)} - "
            "insufficient available credit"
        )

    def test_the_declined_row_names_the_attempt_not_the_shortfall(self, ob):
        charge(round(card(ob).available_credit + 5000.0, 2), merchant="OPENAPPS SHOP")
        assert "5,000.00" not in ledger(ob)[0].description

    def test_the_declined_row_falls_back_to_the_descriptor(self, ob):
        charge(round(card(ob).available_credit + 5000.0, 2))
        assert DESCRIPTOR in ledger(ob)[0].description

    def test_each_refusal_posts_its_own_row(self, ob):
        before = len(ledger(ob))
        too_much = round(card(ob).available_credit + 5000.0, 2)
        charge(too_much)
        charge(too_much)
        assert len(ledger(ob)) == before + 2

    def test_the_descriptor_carries_the_order_id(self, ob):
        charge(50.0, descriptor="OPENAPPS SHOP 7F3A1B22")
        assert "7F3A1B22" in ledger(ob)[0].description


class TestAccountFigures:
    def test_a_charge_moves_headroom_and_balance_together(self, ob):
        before = card(ob)
        headroom, owed = before.available_credit, before.present_balance
        charge(250.00)
        after = card(ob)
        assert after.available_credit == round(headroom - 250.00, 2)
        assert after.present_balance == round(owed - 250.00, 2)

    def test_the_credit_limit_itself_never_moves(self, ob):
        limit = card(ob).credit_limit
        charge(round(card(ob).available_credit + 9.0, 2))
        assert card(ob).credit_limit == limit

    def test_other_accounts_are_untouched(self, ob):
        others = {a.id: (a.available_balance, a.present_balance)
                  for a in ob.account_rows() if a.id != CARD_ID}
        charge(250.00)
        after = {a.id: (a.available_balance, a.present_balance)
                 for a in ob.account_rows() if a.id != CARD_ID}
        assert after == others


class TestRewardSurface:
    def test_the_payload_gains_only_the_new_rows(self, ob):
        before = {t.id for t in ob.transactions()}
        charge(round(card(ob).available_credit + 3.0, 2))
        after = {t.id for t in ob.transactions()}
        assert len(after - before) == 2
        assert before <= after

    def test_a_reset_restores_the_seeded_figures(self, tmp_path):
        ob = seed(tmp_path)
        seeded = card(ob).available_credit
        charge(500.00)
        assert card(ob).available_credit != seeded
        seed(tmp_path)
        assert card(ob).available_credit == seeded
        assert all(t.date is not None for t in ledger(ob))
