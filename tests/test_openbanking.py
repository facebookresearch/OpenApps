"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for the OpenBanking app, its theme/layout/content variants, and the
cross-app task set anchored on it.

Three things are being protected here, in rising order of subtlety:

1. The app renders under every layout and stays free of external egress
   (the latter lives in ``test_no_egress.py``, which picks the routes up
   automatically).
2. ``/openbanking_all`` is *stable*. The app is read-only, so its slice of the
   cross-app state must be byte-identical on every fetch. If it ever drifts,
   it silently breaks every unrelated todo/calendar task, because
   ``AppStateComparison`` builds their targets by deep-copying the initial
   state and then requires the whole dict to match.
3. Every expected answer in ``config/tasks/openbanking.yaml`` is still the
   right answer for the seeded ledger. Those tasks deliberately do not quote
   their values in the goal -- the agent has to read them off the page -- so a
   seed edit would otherwise make them quietly unsolvable rather than failing
   loudly here.
"""

import contextlib
import copy
import io
import json
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import apsw
import pytest
from hydra import compose, initialize
from hydra.utils import instantiate
from omegaconf import OmegaConf
from starlette.testclient import TestClient

from open_apps import config_dir
from open_apps.apps.start_page.main import (
    app,
    initialize_routes_and_configure_task,
)
from open_apps.utils import merge_plus_keys

_STATES_DIR = Path(__file__).parent / "states"
_TASKS_PATH = config_dir() / "tasks" / "openbanking.yaml"
_APP_CONFIG_DIR = config_dir() / "apps" / "openbanking"
_THEME_DIR = config_dir() / "apps" / "theme"

CONTENT_VARIANTS = [
    "default",
    "german",
    "mandarin",
    "long_descriptions",
    "misleading_descriptions",
    "adversarial_descriptions",
]
LAYOUTS = ["default", "card_list"]

# The three accounts every content variant must agree on, and the figures the
# task set reads off them. Anything that appends extra accounts (the
# long/misleading/adversarial variants) must leave these untouched.
SEEDED_ACCOUNTS = [
    "BUS COMPLETE CHK (...5555)",
    "BUS SELECT SAVINGS (...8891)",
    "INK BUSINESS CARD (...2043)",
]


def _norm(s: str) -> str:
    """Match StringSimilarityOperator: lowercase, strip punctuation, collapse ws."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(s).lower())).strip()


def _compose(tmp_path, overrides=None):
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(
            config_name="config",
            overrides=[f"logs_dir={tmp_path}"] + list(overrides or []),
        )
    # `+accounts` appends are resolved by the launcher, not by Hydra, so a test
    # that skips the launcher has to apply them itself or it silently measures
    # the un-appended config.
    return merge_plus_keys(cfg)


def _load_initial() -> dict:
    with open(_STATES_DIR / "initial_state.json", encoding="utf-8") as f:
        return json.load(f)


def _check(task, initial, current) -> bool:
    """Run a completion check with the diff-printing suppressed."""
    with contextlib.redirect_stdout(io.StringIO()):
        return task.check_if_task_is_complete(initial, current)


_TASK_CFG = OmegaConf.load(_TASKS_PATH)
_TASK_KEYS = list(_TASK_CFG.keys())


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # A dedicated temp dir rather than the shared ``getbasetemp()``: the apps
    # seed their tables with fixed primary keys at startup, so re-initializing
    # over another module's database collides on insert.
    logs_dir = tmp_path_factory.mktemp("openbanking")
    cfg = _compose(logs_dir)
    Path(cfg.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Config shape: the app is theme+layout native
# ---------------------------------------------------------------------------
class TestConfigShape:
    def test_has_no_appearance_group(self):
        """The migrated shape is theme (shared tokens) + layout (structure).

        A new app must not reintroduce `appearance/`, which conflates the two
        and has to be unpicked again later.
        """
        assert not (_APP_CONFIG_DIR / "appearance").exists()
        assert (_APP_CONFIG_DIR / "layout").is_dir()
        assert (_APP_CONFIG_DIR / "content").is_dir()

    def test_theme_defaults_to_inheriting_the_global_group(self, tmp_path):
        cfg = _compose(tmp_path)
        assert cfg.apps.openbanking.theme is None

    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_layout_variants_compose(self, tmp_path, layout):
        cfg = _compose(tmp_path, [f"apps/openbanking/layout={layout}"])
        assert cfg.apps.openbanking.layout == layout

    @pytest.mark.parametrize("variant", CONTENT_VARIANTS)
    def test_content_variants_compose(self, tmp_path, variant):
        cfg = _compose(tmp_path, [f"apps/openbanking/content={variant}"])
        accounts = cfg.apps.openbanking.accounts
        assert len(accounts) >= len(SEEDED_ACCOUNTS)
        for account in accounts:
            assert account.transactions

    def test_database_path_is_under_databases_dir(self, tmp_path):
        cfg = _compose(tmp_path)
        assert cfg.apps.openbanking.database_path.startswith(str(cfg.databases_dir))


class TestContentInvariants:
    """The figures a task reads must not move between content variants.

    Translating a label is fine; translating an amount would mean the same
    task needs a different answer in German, which defeats the point of
    measuring the same task across variations.
    """

    def _figures(self, cfg):
        by_name = {}
        for account in cfg.apps.openbanking.accounts:
            by_name[account.name] = {
                "kind": account.get("kind", "deposit"),
                "account_number": account.account_number,
                "routing_number": account.routing_number,
                "available_balance": account.available_balance,
                "present_balance": account.present_balance,
                "available_credit": account.available_credit,
                # Card figures. Numeric, so they hold across variants for the
                # same reason the balances do. The two *date* strings are
                # deliberately excluded -- they are translated like the
                # transaction dates, so the payment-due task scores on a
                # calendar event rather than on the string the agent read.
                "credit_limit": account.get("credit_limit", None),
                "statement_balance": account.get("statement_balance", None),
                "minimum_payment": account.get("minimum_payment", None),
                "card_expiration": account.get("card_expiration", None),
                "amounts": [t.amount for t in account.transactions],
                "balances": [t.balance for t in account.transactions],
            }
        return by_name

    @pytest.mark.parametrize("variant", CONTENT_VARIANTS)
    def test_seeded_account_figures_are_identical(self, tmp_path, variant):
        base = self._figures(_compose(tmp_path, ["apps/openbanking/content=default"]))
        other = self._figures(
            _compose(tmp_path, [f"apps/openbanking/content={variant}"])
        )
        for name in SEEDED_ACCOUNTS:
            assert name in other, f"{variant} renamed or dropped {name!r}"
            assert other[name] == base[name], f"{variant} changed figures on {name!r}"

    @pytest.mark.parametrize(
        "variant",
        ["long_descriptions", "misleading_descriptions", "adversarial_descriptions"],
    )
    def test_noise_variants_append_rather_than_replace(self, tmp_path, variant):
        cfg = _compose(tmp_path, [f"apps/openbanking/content={variant}"])
        names = [a.name for a in cfg.apps.openbanking.accounts]
        assert names[: len(SEEDED_ACCOUNTS)] == SEEDED_ACCOUNTS
        assert len(names) > len(SEEDED_ACCOUNTS), "the `+accounts` append did not apply"

    def test_adversarial_variant_injects_the_shared_message(self, tmp_path):
        cfg = _compose(tmp_path, ["apps/openbanking/content=adversarial_descriptions"])
        message = _norm(cfg.apps.adversarial_message)
        blob = _norm(" ".join(a.name for a in cfg.apps.openbanking.accounts))
        assert message in blob


class TestTheme:
    """The banking themes are ordinary members of the shared theme group."""

    BANKING_EXTRAS = [
        "color-header-bg",
        "color-header-fg",
        "color-link",
        "color-credit",
        "color-debit",
        "color-rule",
    ]

    @pytest.mark.parametrize("name", ["openbanking", "openbanking_dark"])
    def test_theme_defines_the_shared_contract_and_the_extras(self, name):
        from open_apps.theme import load_theme

        default_tokens = set(load_theme("default")["tokens"])
        theme = load_theme(name)
        tokens = theme["tokens"]
        assert theme["name"] == name
        assert default_tokens <= set(tokens), "must cover the shared token contract"
        for extra in self.BANKING_EXTRAS:
            assert extra in tokens

    def test_light_and_dark_declare_the_same_tokens(self):
        from open_apps.theme import load_theme

        assert set(load_theme("openbanking")["tokens"]) == set(
            load_theme("openbanking_dark")["tokens"]
        )

    @pytest.mark.parametrize("name", ["openbanking", "openbanking_dark"])
    def test_theme_ships_no_webfont_import(self, name):
        """An `@import` would put a CDN back in the request path."""
        from open_apps.theme import load_theme

        assert not load_theme(name)["import_url"]

    @pytest.mark.parametrize("name", ["openbanking", "openbanking_dark"])
    def test_theme_renders_into_a_root_block(self, name):
        from open_apps.theme import load_theme, render_theme_tokens

        css = str(render_theme_tokens(load_theme(name)))
        assert "--color-header-bg" in css
        assert ":root" in css


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
class TestRendering:
    def test_account_index_lists_every_account(self, client):
        html = client.get("/openbanking").text
        for name in SEEDED_ACCOUNTS:
            assert name in html

    def test_account_detail_shows_the_summary_figures(self, client):
        html = client.get("/openbanking/accounts/0").text
        assert "$6,102.80" in html  # available balance
        assert "-$1,078.81" in html  # largest card charge, signed
        assert "COOL APPS INC" in html
        assert "Available balance" in html

    def test_pending_transaction_has_no_posted_balance(self, client):
        html = client.get("/openbanking/accounts/0").text
        assert "Pending" in html
        assert "—" in html

    def test_unknown_account_does_not_500(self, client):
        assert client.get("/openbanking/accounts/999").status_code == 200

    def test_theme_tokens_are_emitted_per_request(self, client):
        html = client.get("/openbanking/accounts/0").text
        assert ":root" in html
        assert "--color-fg" in html

    def test_banking_tokens_have_fallbacks_for_other_themes(self, client):
        """Under a theme that never heard of the bank, the app must still style.

        The default theme defines none of the banking extras, so every use has
        to carry a fallback or the masthead renders unpainted.
        """
        html = client.get("/openbanking/accounts/0").text
        assert "var(--color-header-bg, var(--color-primary))" in html

    def test_card_list_layout_renders_the_same_data(self, client):
        """`layout` is read per-request, so it can be swapped like `reconfigure` does.

        Probes the emitted *markup*, not the bare class name: the component
        stylesheet is static and mentions every class under either layout.
        The class probe is written without a leading tag because FastHTML
        emits `id` before `class` when a component sets both.
        """
        table_markup = "<table"
        card_markup = 'class="ob-txn-card"'
        original = app.config.openbanking.layout
        try:
            app.config.openbanking.layout = "card_list"
            html = client.get("/openbanking/accounts/0").text
            assert card_markup in html
            assert table_markup not in html
            assert "COOL APPS INC" in html
        finally:
            app.config.openbanking.layout = original
        html = client.get("/openbanking/accounts/0").text
        assert table_markup in html
        assert card_markup not in html


class TestBalancesComeFromSqlite:
    """Every figure on screen is read out of SQLite, not out of the Hydra config.

    The config is only a *seed*: ``set_environment`` inserts it once and the
    render path goes through the ``account`` / ``transaction`` tables from then
    on. That distinction is invisible while the two agree, which is exactly why
    it needs a test -- a refactor that closes over ``config.openbanking.accounts``
    at import time would still pass every other test in this file, and would then
    silently ignore any mutation an agent or a task setup made to the database.

    Each test writes through a *separate* connection to the same file and asserts
    the change shows up in the very next request, which also pins that nothing is
    cached in module state between requests.

    The separate connection is `apsw`, not the stdlib `sqlite3` module, and that
    is not incidental. fastlite talks to SQLite through apsw, which links its own
    copy of the SQLite library; `sqlite3` links the system one. Two SQLite builds
    in one process, both holding a WAL database open, do not share the WAL index
    coherently -- the apsw side silently keeps serving a stale snapshot, so a test
    written with `sqlite3` fails on its *second* write and looks exactly like the
    caching bug this class exists to catch. Out-of-process writers (the `sqlite3`
    CLI, a GUI client) are fine; see `docs/inspecting_databases.md`.
    """

    def _db(self):
        return apsw.Connection(app.config.openbanking.database_path)

    @contextlib.contextmanager
    def _patched(self, table: str, column: str, where: str, value: float):
        """Set one column to `value` for the duration of the block, then put back
        whatever was there before.

        The old value is *read back* rather than restated as a literal. The
        `client` fixture is module-scoped, so a restore that misses by a cent
        leaves the seed corrupted for every later test in the file -- and the
        failure surfaces somewhere else entirely, in whichever test happens to
        assert on the figure that got clobbered.

        `list(...)` wraps every statement because apsw cursors are lazy: an
        undrained one leaves the statement live on the connection.
        """
        con = self._db()
        select = f'SELECT {column} FROM "{table}" WHERE {where}'
        try:
            ((old,),) = list(con.execute(select))
            list(con.execute(f'UPDATE "{table}" SET {column} = ? WHERE {where}', (value,)))
            yield
        finally:
            list(con.execute(f'UPDATE "{table}" SET {column} = ? WHERE {where}', (old,)))
            con.close()

    def test_database_file_exists_and_is_seeded(self, client):
        con = self._db()
        try:
            names = {r[0] for r in con.execute("SELECT name FROM account")}
            ((txn_count,),) = list(con.execute('SELECT COUNT(*) FROM "transaction"'))
        finally:
            con.close()
        assert set(SEEDED_ACCOUNTS) <= names
        assert txn_count > 0

    def test_available_balance_is_read_from_the_account_table(self, client):
        assert "$6,102.80" in client.get("/openbanking/accounts/0").text
        with self._patched("account", "available_balance", "id = 0", 12345.67):
            assert "$12,345.67" in client.get("/openbanking/accounts/0").text
        # The restore has to land too, or the rest of the file scores against a
        # ledger that no longer matches the seed.
        assert "$6,102.80" in client.get("/openbanking/accounts/0").text

    def test_index_balances_are_read_from_the_account_table(self, client):
        """The account list is a second render path over the same rows."""
        with self._patched("account", "available_balance", "id = 0", 12345.67):
            assert "$12,345.67" in client.get("/openbanking").text

    def test_card_statement_balance_is_read_from_the_account_table(self, client):
        """Cards render `statement_balance`/`present_balance`, not `available_balance`."""
        with self._patched("account", "statement_balance", "id = 2", 9876.54):
            assert "$9,876.54" in client.get("/openbanking/accounts/2").text

    def test_ledger_balances_are_read_from_the_transaction_table(self, client):
        where = "account_id = 0 AND position = 1"
        with self._patched("transaction", "balance", where, 4242.42):
            assert "$4,242.42" in client.get("/openbanking/accounts/0").text

    def test_reward_state_is_read_from_the_account_table(self, client):
        """`/openbanking_all` feeds task scoring, so it must see the DB too."""
        with self._patched("account", "available_balance", "id = 0", 12345.67):
            state = client.get("/openbanking_all").json()
        balances = {a["id"]: a["available_balance"] for a in state["accounts"]}
        assert balances[0] == 12345.67


class TestCreditCardSummary:
    """A card renders a different summary from a deposit account.

    Account 2 is the card. The point of the split is that a cardholder acts on
    what is owed and when, not on a balance -- and a card's `available_balance`
    is 0.00, so the deposit hero would actively mislead.
    """

    CARD = "/openbanking/accounts/2"
    CHECKING = "/openbanking/accounts/0"

    def test_card_renders_the_graphic(self, client):
        html = client.get(self.CARD).text
        assert 'id="ob-cardface"' in html
        assert "Meridian" in html
        assert "Expires 09/29" in html
        assert "CARDINAL FREIGHT LLC" in html.upper()

    def test_deposit_account_has_no_card_graphic(self, client):
        assert 'id="ob-cardface"' not in client.get(self.CHECKING).text

    def test_card_number_is_grouped_and_masked_by_default(self, client):
        html = client.get(self.CARD).text
        assert "•••• •••• •••• 2043" in html
        assert "9024007155992043" not in html

    def test_revealing_the_card_number_groups_it_too(self, client):
        html = client.get(f"{self.CARD}/numbers?account=1").text
        assert "9024 0071 5599 2043" in html

    def test_detail_route_accepts_the_reveal_flag(self, client):
        assert "9024 0071 5599 2043" in client.get(f"{self.CARD}?account=1").text

    def test_card_offers_no_routing_disclosure(self, client):
        """No routing number exists on a card, so no row should claim one."""
        html = client.get(self.CARD).text
        assert "Routing number" not in html
        assert "123456789" not in html

    def test_payment_figures_are_present(self, client):
        html = client.get(self.CARD).text
        for label in (
            "Statement balance",
            "Minimum payment due",
            "Payment due date",
            "Available to spend",
            "Credit limit",
        ):
            assert label in html, label
        assert "$872.19" in html  # statement balance
        assert "$35.00" in html  # minimum payment
        assert "Sep 15, 2026" in html  # due date
        assert "$8,715.81" in html  # available to spend
        assert "$10,000.00" in html  # credit limit

    def test_amount_owed_is_shown_unsigned(self, client):
        """`present_balance` is -1284.19; a cardholder owes 1,284.19.

        Scoped to the summary: the ledger's running-balance column below it is
        genuinely negative on a card, and should stay that way.
        """
        summary = client.get(self.CARD).text.split('class="ob-band"', 1)[0]
        assert "$1,284.19" in summary
        assert "-$1,284.19" not in summary

    def test_index_shows_what_is_owed_not_a_zero_balance(self, client):
        """The card's `available_balance` is 0.00 and would read as empty."""
        html = client.get("/openbanking").text
        assert "$1,284.19" in html

    def test_statement_balance_differs_from_the_current_balance(self, client):
        """Otherwise "read the statement balance" has two right answers."""
        accounts = client.get("/openbanking_all").json()["accounts"]
        card = next(a for a in accounts if a["kind"] == "credit_card")
        assert card["statement_balance"] != abs(card["present_balance"])

    def test_the_seeded_cycle_is_arithmetically_coherent(self, client):
        accounts = client.get("/openbanking_all").json()["accounts"]
        card = next(a for a in accounts if a["kind"] == "credit_card")
        assert card["credit_limit"] == pytest.approx(
            abs(card["present_balance"]) + card["available_credit"]
        )
        # The one charge posted after the statement closed.
        assert abs(card["present_balance"]) - card[
            "statement_balance"
        ] == pytest.approx(412.00)

    def test_card_number_is_not_a_plausible_live_pan(self, client):
        """Two safeguards: an unassigned network prefix, and a failing Luhn."""
        accounts = client.get("/openbanking_all").json()["accounts"]
        card = next(a for a in accounts if a["kind"] == "credit_card")
        pan = card["account_number"]
        assert len(pan) == 16 and pan.isdigit()
        assert pan[0] not in "23456", "leading digit is an assigned network range"
        total = 0
        for index, digit in enumerate(reversed([int(d) for d in pan])):
            if index % 2:
                digit *= 2
                if digit > 9:
                    digit -= 9
            total += digit
        assert total % 10 != 0, "a Luhn-valid number could be mistaken for live"

    def test_revealing_the_card_does_not_mutate_state(self, client):
        before = client.get("/openbanking_all").text
        client.get(f"{self.CARD}/numbers?account=1")
        client.get(f"{self.CARD}?account=1")
        assert client.get("/openbanking_all").text == before

    def test_card_graphic_survives_a_theme_without_banking_tokens(self, client):
        """The face uses the masthead pair, which always carries a fallback."""
        from open_apps.apps.openbanking_app.main import styles

        css = str(styles)
        face = css.split(".ob-cardface {", 1)[1].split("}", 1)[0]
        assert "var(--color-header-bg, var(--color-primary))" in face
        assert "var(--color-header-fg, var(--color-on-primary))" in face


class TestAccountNumbers:
    """The click-to-reveal account and routing figures.

    Disclosure is server-rendered and stateless -- the visibility of each
    figure travels in the query string rather than in any stored state -- so
    the app stays read-only no matter what the agent opens.
    """

    NUMBERS = "/openbanking/accounts/0/numbers"

    def test_mask_hides_all_but_the_last_four(self):
        from open_apps.apps.openbanking_app.main import mask_number

        assert mask_number("447109255555") == "••••••••5555"
        assert mask_number("123456789") == "•••••6789"

    def test_mask_leaves_short_values_alone(self):
        from open_apps.apps.openbanking_app.main import mask_number

        assert mask_number("5555") == "5555"
        assert mask_number("55") == "55"

    def test_both_figures_are_masked_by_default(self, client):
        html = client.get("/openbanking/accounts/0").text
        assert "Account number" in html
        assert "Routing number" in html
        assert "••••••••5555" in html
        assert "•••••6789" in html
        assert "447109255555" not in html
        assert "123456789" not in html

    def test_revealing_the_account_number_leaves_routing_masked(self, client):
        html = client.get(f"{self.NUMBERS}?account=1&routing=0").text
        assert "447109255555" in html
        assert "123456789" not in html
        assert "•••••6789" in html

    def test_revealing_the_routing_number_leaves_account_masked(self, client):
        html = client.get(f"{self.NUMBERS}?account=0&routing=1").text
        assert "123456789" in html
        assert "447109255555" not in html
        assert "••••••••5555" in html

    def test_both_can_be_revealed_together(self, client):
        html = client.get(f"{self.NUMBERS}?account=1&routing=1").text
        assert "447109255555" in html
        assert "123456789" in html

    def test_a_revealed_figure_offers_to_hide_itself_again(self, client):
        """Clicking a revealed number must collapse it, not re-reveal it."""
        html = client.get(f"{self.NUMBERS}?account=1&routing=0").text
        # The account row is open, so its own control has to request account=0
        # while preserving routing=0.
        assert "account=0&amp;routing=0" in html or "account=0&routing=0" in html

    def test_revealing_one_preserves_the_others_open_state(self, client):
        """The routing control, with the account row already open, must keep
        account=1 in its own request or the swap would silently re-hide it."""
        html = client.get(f"{self.NUMBERS}?account=1&routing=0").text
        assert "account=1&amp;routing=1" in html or "account=1&routing=1" in html

    def test_unknown_account_does_not_500(self, client):
        assert client.get("/openbanking/accounts/999/numbers").status_code == 200

    def test_detail_route_accepts_the_same_flags(self, client):
        """A direct link to a revealed state renders it, so the partial and the
        full page cannot disagree."""
        html = client.get("/openbanking/accounts/0?account=1&routing=1").text
        assert "447109255555" in html
        assert "123456789" in html

    def test_revealing_does_not_mutate_state(self, client):
        before = client.get("/openbanking_all").json()
        for query in [
            "account=1&routing=0",
            "account=0&routing=1",
            "account=1&routing=1",
        ]:
            assert client.get(f"{self.NUMBERS}?{query}").status_code == 200
        assert client.get("/openbanking_all").json() == before


class TestMasthead:
    """The bar carries no control that swallows a click.

    Inert chrome is worse than absent chrome for an agent: a hamburger or a
    magnifier that does nothing costs a step and teaches nothing, so the bar
    holds the wordmark and the one CTA that has an answer.
    """

    OPEN_ACCOUNT = "/openbanking/open-account"

    @pytest.mark.parametrize("glyph", ["☰", "⌕", "◉"])
    def test_the_inert_glyphs_are_gone(self, client, glyph):
        assert glyph not in client.get("/openbanking").text

    def test_the_dead_sign_out_caption_is_gone(self, client):
        html = client.get("/openbanking").text
        assert "Sign out" not in html
        assert "ob-signout" not in html

    @pytest.mark.parametrize(
        "route", ["/openbanking", "/openbanking/accounts/0", "/openbanking/accounts/2"]
    )
    def test_the_cta_and_its_swap_target_are_on_every_page(self, client, route):
        """The CTA sits in the bar on every page, so the placeholder it swaps
        has to exist on every page too."""
        html = client.get(route).text
        assert "Open an account" in html
        assert 'id="ob-dialog"' in html

    def test_the_cta_opens_the_dialog(self, client):
        html = client.get(self.OPEN_ACCOUNT).text
        assert "<dialog" in html
        assert "open" in html
        assert "1-800-555-5555" in html
        assert "Open a new account" in html

    def test_the_dialog_is_absent_until_asked_for(self, client):
        assert "<dialog" not in client.get("/openbanking").text

    def test_the_dialog_can_be_dismissed(self, client):
        """Close swaps back to the empty placeholder, so it is the same route
        rather than a second endpoint."""
        html = client.get(f"{self.OPEN_ACCOUNT}?show=0").text
        assert "<dialog" not in html
        assert 'id="ob-dialog"' in html

    @pytest.mark.parametrize("variant", ["german", "mandarin"])
    def test_the_phone_number_is_the_same_in_every_language(self, tmp_path, variant):
        """It is a figure, like the amounts: a task that reads it must not need
        a different answer per content variant."""
        cfg = _compose(tmp_path, [f"apps/openbanking/content={variant}"])
        assert cfg.apps.openbanking.open_account_phone == "1-800-555-5555"


class TestAccountIdentity:
    """Holders and numbers distinguish the three accounts from one another."""

    @pytest.fixture(scope="class")
    def accounts(self, tmp_path_factory):
        cfg = _compose(tmp_path_factory.mktemp("identity"))
        return list(cfg.apps.openbanking.accounts)

    def test_each_account_belongs_to_a_different_company(self, accounts):
        holders = [a.holder for a in accounts]
        assert len(set(holders)) == len(holders), holders

    def test_account_numbers_are_distinct(self, accounts):
        numbers = [a.account_number for a in accounts]
        assert len(set(numbers)) == len(numbers)

    def test_account_number_tail_matches_the_display_name(self, accounts):
        """Revealing a number should confirm the name, not contradict it."""
        for account in accounts:
            shown = re.search(r"\(\.\.\.(\d+)\)", account.name)
            assert shown, f"{account.name!r} has no (...NNNN) suffix"
            assert account.account_number.endswith(shown.group(1))

    def test_routing_number_is_shared_and_well_formed(self, accounts):
        """One bank, one routing number -- across the deposit accounts only.

        A credit card has no routing number, which is the whole reason `kind`
        exists: the app omits the row rather than rendering it blank.
        """
        deposits = [a for a in accounts if a.get("kind", "deposit") == "deposit"]
        assert deposits, "the fixture should still hold deposit accounts"
        routing = {a.routing_number for a in deposits}
        assert len(routing) == 1
        value = routing.pop()
        assert value.isdigit() and len(value) == 9

    def test_cards_have_no_routing_number(self, accounts):
        cards = [a for a in accounts if a.get("kind", "deposit") == "credit_card"]
        assert cards, "the seed should still hold a card"
        for card in cards:
            assert card.routing_number is None

    def test_numbers_are_strings_so_leading_zeros_survive(self, accounts):
        for account in accounts:
            assert isinstance(account.account_number, str)
            if account.routing_number is not None:
                assert isinstance(account.routing_number, str)


class TestFiltering:
    LEDGER = "/openbanking/accounts/0/ledger"

    def test_deposits_only(self, client):
        html = client.get(f"{self.LEDGER}?showing=1&q=").text
        assert "VENMO" in html  # +632.67
        assert "COOL APPS" not in html  # -1078.81

    def test_withdrawals_only(self, client):
        html = client.get(f"{self.LEDGER}?showing=2&q=").text
        assert "COOL APPS" in html
        assert "VENMO" not in html

    def test_search_matches_description(self, client):
        html = client.get(f"{self.LEDGER}?showing=0&q=biztool").text
        assert "BIZTOOL" in html
        assert "COOL APPS" not in html

    def test_search_matches_type(self, client):
        html = client.get(f"{self.LEDGER}?showing=0&q=ach").text
        assert "VENMO" in html

    def test_search_with_no_match_is_empty_not_broken(self, client):
        response = client.get(f"{self.LEDGER}?showing=0&q=zzzznotathing")
        assert response.status_code == 200
        assert "COOL APPS" not in response.text


class TestLedgerHistory:
    """Filtering pushes history; reloading what it pushed must render the page.

    Regression: the controls used to push the `/ledger` partial's own URL, so a
    reload after filtering showed a bare, unstyled fragment.
    """

    LEDGER = "/openbanking/accounts/0/ledger?showing=1&q=venmo&expand=0"
    HX = {"HX-Request": "true"}

    def test_the_swap_pushes_the_account_page_not_the_partial(self, client):
        response = client.get(self.LEDGER, headers=self.HX)
        assert response.headers["HX-Push-Url"] == (
            "/openbanking/accounts/0?showing=1&q=venmo&expand=0"
        )
        assert "ob-root" not in response.text

    def test_the_pushed_url_renders_the_filtered_page(self, client):
        pushed = client.get(self.LEDGER, headers=self.HX).headers["HX-Push-Url"]
        html = client.get(pushed).text
        assert "ob-root" in html
        assert "VENMO" in html
        assert "COOL APPS" not in html

    def test_a_direct_hit_on_the_partial_gets_the_full_page(self, client):
        """Old history entries and bookmarks still point at `/ledger`."""
        html = client.get(self.LEDGER).text
        assert "ob-root" in html
        assert "VENMO" in html
        assert "COOL APPS" not in html


class TestSeeMoreActivity:
    """The ledger is truncated, and the toggle is the only thing that opens it.

    Account 0 seeds seven postings against a `visible_transactions` of four, so
    the last three -- PAYROLL PARTNERS, the remote-capture deposit and PRINT
    SHOP 44 -- are behind the toggle. `COOL APPS INC` is the fourth and stays
    above the cut on purpose: `add_todo_to_review_largest_card_charge` reads it
    off the first screen.
    """

    DETAIL = "/openbanking/accounts/0"
    LEDGER = "/openbanking/accounts/0/ledger"
    HIDDEN = "PRINT SHOP 44"
    VISIBLE = "COOL APPS INC"

    def test_the_tail_of_the_ledger_is_hidden(self, client):
        html = client.get(self.DETAIL).text
        assert self.VISIBLE in html
        assert self.HIDDEN not in html
        assert "See more activity" in html

    def test_expanding_reveals_it(self, client):
        html = client.get(f"{self.DETAIL}?expand=1").text
        assert self.HIDDEN in html
        assert "See less activity" in html

    def test_the_partial_honours_the_flag_too(self, client):
        assert self.HIDDEN not in client.get(f"{self.LEDGER}?expand=0").text
        assert self.HIDDEN in client.get(f"{self.LEDGER}?expand=1").text

    def test_a_result_set_that_fits_has_no_toggle(self, client):
        """Searching down to three rows is not a truncated ledger."""
        html = client.get(f"{self.LEDGER}?showing=0&q=transfer").text
        assert "See more activity" not in html
        assert "See less activity" not in html

    def test_the_toggle_carries_the_live_filter(self, client):
        """Expanding after typing must not throw the search away, so the
        control includes the two inputs rather than baking in their values."""
        html = client.get(self.DETAIL).text
        assert 'hx-include="#showing, #ob-search"' in html

    def test_the_toggle_is_a_real_link_as_well(self, client):
        """It has to survive without htmx: the detail route reads `expand`."""
        html = client.get(self.DETAIL).text
        assert "/openbanking/accounts/0?showing=0&amp;q=&amp;expand=1" in html

    def test_truncation_applies_under_card_list_too(self, client):
        original = app.config.openbanking.layout
        try:
            app.config.openbanking.layout = "card_list"
            assert self.HIDDEN not in client.get(self.DETAIL).text
            assert self.HIDDEN in client.get(f"{self.DETAIL}?expand=1").text
        finally:
            app.config.openbanking.layout = original

    def test_zero_disables_truncation(self, client):
        original = app.config.openbanking.visible_transactions
        try:
            app.config.openbanking.visible_transactions = 0
            html = client.get(self.DETAIL).text
            assert self.HIDDEN in html
            assert "See more activity" not in html
        finally:
            app.config.openbanking.visible_transactions = original

    def test_expanding_does_not_mutate_state(self, client):
        before = client.get("/openbanking_all").json()
        client.get(f"{self.DETAIL}?expand=1")
        client.get(f"{self.LEDGER}?expand=1")
        assert client.get("/openbanking_all").json() == before

    def test_every_seeded_account_has_something_behind_the_toggle(self, client):
        """The point of the change: each account, not just the busiest one."""
        for account_id in range(len(SEEDED_ACCOUNTS)):
            html = client.get(f"/openbanking/accounts/{account_id}").text
            assert "See more activity" in html, f"account {account_id} is not truncated"


class TestPicoBridge:
    """Pico's own palette has to follow the theme, not sit under it.

    Pico paints table cells, form fields and headings through `--pico-*`
    properties that a design token cannot reach, which is what left the dark
    theme with a white ledger and unreadable titles.
    """

    def test_the_wrapper_carries_the_palette_polarity(self, client):
        from open_apps.apps.openbanking_app.main import pico_theme

        original = app.config.openbanking.theme
        try:
            app.config.openbanking.theme = "openbanking_dark"
            assert pico_theme() == "dark"
            assert 'data-theme="dark"' in client.get("/openbanking").text
            app.config.openbanking.theme = "openbanking"
            assert pico_theme() == "light"
            assert 'data-theme="light"' in client.get("/openbanking").text
        finally:
            app.config.openbanking.theme = original

    def test_a_theme_without_a_tone_is_treated_as_light(self, client):
        from open_apps.apps.openbanking_app.main import pico_theme

        original = app.config.openbanking.theme
        try:
            app.config.openbanking.theme = "mono"
            assert pico_theme() == "light"
        finally:
            app.config.openbanking.theme = original

    @pytest.mark.parametrize(
        "prop",
        [
            "--pico-color",
            "--pico-background-color",
            "--pico-h2-color",
            "--pico-table-border-color",
            "--pico-form-element-background-color",
            "--pico-form-element-color",
            "--pico-contrast-background",
        ],
    )
    def test_the_properties_the_markup_touches_are_bridged(self, client, prop):
        assert f"{prop}: var(--color-" in client.get("/openbanking").text

    def test_the_bridge_is_scoped_to_this_app(self, client):
        """These pages are mounted alongside every other app's; Pico's defaults
        are the right answer everywhere else."""
        html = client.get("/openbanking").text
        assert ".ob-root {" in html
        assert ":root {\n        --pico-" not in html

    def test_the_masthead_wordmark_takes_the_header_foreground(self, client):
        """Pico colours every heading explicitly, so the H1 in the masthead does
        not inherit the bar's foreground -- it has to be told."""
        html = client.get("/openbanking").text
        assert ".ob-masthead h1" in html

    @pytest.mark.parametrize("name", ["openbanking", "openbanking_dark"])
    def test_both_banking_themes_declare_a_tone(self, name):
        from open_apps.theme import load_theme

        assert load_theme(name)["assets"]["tone"] in {"light", "dark"}


# ---------------------------------------------------------------------------
# Reward-state stability -- the part that can break other apps
# ---------------------------------------------------------------------------
class TestRewardState:
    def test_state_shape(self, client):
        state = client.get("/openbanking_all").json()
        assert set(state) == {"accounts", "transactions"}
        assert len(state["accounts"]) == len(SEEDED_ACCOUNTS)
        assert state["transactions"]

    def test_state_is_byte_stable_across_fetches(self, client):
        first = client.get("/openbanking_all").text
        for _ in range(3):
            assert client.get("/openbanking_all").text == first

    def test_reading_the_app_does_not_mutate_state(self, client):
        """The whole read-only premise, asserted rather than assumed."""
        before = client.get("/openbanking_all").json()
        for route in [
            "/openbanking",
            "/openbanking/accounts/0",
            "/openbanking/accounts/1",
            "/openbanking/accounts/2",
            "/openbanking/accounts/0?showing=2",
            "/openbanking/accounts/0/ledger?showing=1&q=venmo",
            "/openbanking/open-account",
        ]:
            assert client.get(route).status_code == 200
        assert client.get("/openbanking_all").json() == before

    def test_identical_states_produce_no_diff(self, client):
        """A stable app slice must never contribute a diff to another task."""
        from open_apps.tasks.tasks import AppStateComparison

        state = {
            "todo": [],
            "calendar": [],
            "map": [],
            "messenger": [],
            "openbanking": client.get("/openbanking_all").json(),
        }
        assert AppStateComparison(copy.deepcopy(state), copy.deepcopy(state)).compare()


# ---------------------------------------------------------------------------
# Task set
# ---------------------------------------------------------------------------
class TestTaskSet:
    @pytest.mark.parametrize("key", _TASK_KEYS)
    def test_task_instantiates(self, key):
        assert instantiate(_TASK_CFG[key]) is not None

    @pytest.mark.parametrize(
        "key", [k for k in _TASK_KEYS if k != "navigate_to_openbanking"]
    )
    def test_target_passes_and_initial_fails(self, key):
        """Self-consistency: the task's own target state satisfies its check,
        and the untouched initial state does not."""
        task = instantiate(_TASK_CFG[key])
        initial = _load_initial()
        target = task.get_target_state(copy.deepcopy(initial))
        assert _check(task, copy.deepcopy(initial), target)
        assert not _check(task, copy.deepcopy(initial), copy.deepcopy(initial))

    def test_navigation_task_scores_off_the_url(self):
        task = instantiate(_TASK_CFG["navigate_to_openbanking"])
        initial = _load_initial()
        assert _check(task, initial, dict(initial, _url="http://x/openbanking"))
        assert _check(
            task, initial, dict(initial, _url="http://x/openbanking/accounts/0")
        )
        assert not _check(task, initial, dict(initial, _url="http://x/"))
        assert not _check(task, initial, dict(initial, _url="http://x/todo"))

    @pytest.mark.parametrize("key", _TASK_KEYS)
    def test_goal_names_the_app(self, key):
        """These tasks are only meaningful if the goal sends the agent to the
        bank first -- the value it needs is not in the goal text."""
        goal = _norm(instantiate(_TASK_CFG[key]).goal)
        assert "openbanking" in goal


class TestTaskAnswersMatchTheSeed:
    """Guards the expected values against a seed edit.

    Each test re-derives the answer from the seeded accounts the way an agent
    would have to, and asserts the task set still agrees. Without these,
    changing an amount in the seed leaves the tasks syntactically valid but
    unsolvable. Parametrized over the content variants that append accounts,
    because the expected answers are shared across variants -- an appended
    account that outranks a seeded one breaks a task in that variant only.
    german/mandarin are skipped: they translate type labels and dates, which
    these English-keyed derivations read, and their figures are already pinned
    to default by ``test_seeded_account_figures_are_identical``.
    """

    @pytest.fixture(
        scope="class",
        params=[v for v in CONTENT_VARIANTS if v not in ("german", "mandarin")],
    )
    def accounts(self, request, tmp_path_factory):
        cfg = _compose(
            tmp_path_factory.mktemp("seed"),
            [f"apps/openbanking/content={request.param}"],
        )
        return {a.name: a for a in cfg.apps.openbanking.accounts}

    def _expected(self, key, field="todo_name", index=None):
        node = _TASK_CFG[key]
        if index is not None:
            node = node.subtasks[index]
        return node[field]

    def test_largest_card_charge_in_checking(self, accounts):
        checking = accounts["BUS COMPLETE CHK (...5555)"]
        largest = min(t.amount for t in checking.transactions if t.type == "Card")
        expected = self._expected("add_todo_to_review_largest_card_charge")
        assert _norm(expected) == _norm(f"Review charge {abs(largest):.2f}")

    def test_savings_available_balance(self, accounts):
        savings = accounts["BUS SELECT SAVINGS (...8891)"]
        expected = self._expected("message_bob_the_savings_balance", field="message")
        assert _norm(expected) == _norm(f"{savings.available_balance:.2f}")

    def test_business_card_has_exactly_two_card_charges(self, accounts):
        card = accounts["INK BUSINESS CARD (...2043)"]
        charges = [t.amount for t in card.transactions if t.type == "Card"]
        assert len(charges) == 2, "the goal says 'exactly two'"
        expected = {
            _norm(self._expected("log_business_card_charges_as_todos", index=i))
            for i in (0, 1)
        }
        assert expected == {_norm(f"Card {abs(a):.2f}") for a in charges}

    def test_exactly_one_pending_transaction_and_its_amount(self, accounts):
        checking = accounts["BUS COMPLETE CHK (...5555)"]
        pending = [t for t in checking.transactions if t.date is None]
        assert len(pending) == 1, (
            "the goal says 'one transaction that is still pending'"
        )
        expected = self._expected("note_pending_transfer_in_calendar_and_todo", index=1)
        assert _norm(expected) == _norm(f"Pending {pending[0].amount:.2f}")

    def test_highest_available_balance_account(self, accounts):
        richest = max(accounts.values(), key=lambda a: a.available_balance)
        message = self._expected(
            "reconcile_the_largest_account", field="message", index=0
        )
        todo = self._expected("reconcile_the_largest_account", index=1)
        assert _norm(message) == _norm(richest.name)
        assert _norm(todo) == _norm(f"Reconcile {richest.name}")

    def test_the_richest_account_is_unambiguous(self, accounts):
        """A tie would make the task unscoreable."""
        balances = sorted(
            (a.available_balance for a in accounts.values()), reverse=True
        )
        assert balances[0] > balances[1]

    # --- Credit card ------------------------------------------------------

    @pytest.fixture(scope="class")
    def card(self, accounts):
        return accounts["INK BUSINESS CARD (...2043)"]

    def test_card_minimum_payment(self, card):
        expected = self._expected("add_todo_for_the_card_minimum_payment")
        assert _norm(expected) == _norm(f"Pay minimum {card.minimum_payment:.2f}")

    def test_card_statement_balance_and_headroom(self, card):
        message = self._expected(
            "report_the_card_statement_balance_and_headroom", field="message", index=0
        )
        todo = self._expected("report_the_card_statement_balance_and_headroom", index=1)
        assert _norm(message) == _norm(f"{card.statement_balance:.2f}")
        assert _norm(todo) == _norm(f"Headroom {card.available_credit:.2f}")

    def test_card_payment_due_date_matches_the_seed(self, card):
        """The goal is scored as a calendar date, so the seeded *string* and the
        task's ISO date have to agree -- nothing else ties them together."""
        date = str(
            self._expected("schedule_the_card_payment_due_date", field="date", index=0)
        )
        parsed = datetime.strptime(card.payment_due_date, "%b %d, %Y").date()
        assert date == parsed.isoformat()

    def test_the_card_distractors_are_actually_distinct(self, card):
        """Each payment figure must be unique on the page, or an agent that
        grabbed the wrong one would still score."""
        figures = [
            card.statement_balance,
            card.minimum_payment,
            abs(card.present_balance),
            card.available_credit,
            card.credit_limit,
        ]
        assert len(set(figures)) == len(figures), figures


# ---------------------------------------------------------------------------
# The transaction generator CLI
# ---------------------------------------------------------------------------
class TestTransactionGenerator:
    """``openbanking-gen-txns`` has to produce seed data, not plausible noise.

    Two properties carry the weight. The running balance must still reconcile
    row to row, or the ledger stops being worth reading; and the same seed must
    produce byte-identical output, because the app seeds its tables from this
    config and ``/openbanking_all`` has to be byte-stable across runs.
    """

    @pytest.fixture(scope="class")
    def gen(self):
        from open_apps.apps.openbanking_app import generate_transactions

        return generate_transactions

    @pytest.fixture(scope="class")
    def default_accounts(self, gen):
        return gen.load_accounts(gen.content_path("default"))

    def _rows(self, gen, account, **kwargs):
        options = dict(count=3, seed=0, max_amount=500.0, types=None, start=None)
        options.update(kwargs)
        return gen.generate(account, **options)

    @pytest.mark.parametrize("name", SEEDED_ACCOUNTS)
    def test_generated_rows_chain_off_the_existing_ledger(
        self, gen, default_accounts, name
    ):
        account = gen.find_account(default_accounts, name)
        extended = dict(account)
        extended["transactions"] = list(account["transactions"]) + self._rows(
            gen, account
        )
        gen.check_chain(extended)  # raises if any row fails to reconcile

    def test_the_same_seed_gives_the_same_rows(self, gen, default_accounts):
        account = gen.find_account(default_accounts, SEEDED_ACCOUNTS[0])
        assert self._rows(gen, account, seed=7) == self._rows(gen, account, seed=7)

    def test_a_different_seed_gives_different_rows(self, gen, default_accounts):
        account = gen.find_account(default_accounts, SEEDED_ACCOUNTS[0])
        assert self._rows(gen, account, seed=7) != self._rows(gen, account, seed=8)

    def test_dates_run_backwards_from_the_oldest_posting(self, gen, default_accounts):
        account = gen.find_account(default_accounts, SEEDED_ACCOUNTS[0])
        oldest = gen.parse_date(account["transactions"][-1]["date"])
        dates = [row["when"] for row in self._rows(gen, account, count=5)]
        assert dates == sorted(dates, reverse=True)
        assert dates[0] < oldest

    def test_a_row_with_no_readable_date_is_an_error_not_today(
        self, gen, default_accounts
    ):
        """Today's date would sort a new row above the ones it belongs under,
        and would make the same command emit different YAML tomorrow."""
        account = dict(gen.find_account(default_accounts, SEEDED_ACCOUNTS[0]))
        account["transactions"] = [
            {**txn, "date": "whenever"} for txn in account["transactions"]
        ]
        with pytest.raises(SystemExit, match="--start-date"):
            self._rows(gen, account)

    def test_amounts_stay_under_the_ceiling(self, gen, default_accounts):
        """The ceiling is how a caller keeps clear of a figure a task reads."""
        account = gen.find_account(default_accounts, SEEDED_ACCOUNTS[0])
        rows = self._rows(gen, account, count=25, max_amount=100.0)
        assert all(abs(row["amount"]) <= 100.0 for row in rows)

    def test_types_can_be_restricted(self, gen, default_accounts):
        """`log_business_card_charges_as_todos` says the card has exactly two
        Card transactions, so a caller has to be able to exclude that type."""
        card = gen.find_account(default_accounts, SEEDED_ACCOUNTS[2])
        rows = self._rows(gen, card, count=20, types=["Fee", "Interest"])
        assert {row["type"] for row in rows} <= {"Fee", "Interest"}

    def test_a_card_and_a_deposit_account_draw_from_different_wording(
        self, gen, default_accounts
    ):
        card = gen.find_account(default_accounts, SEEDED_ACCOUNTS[2])
        checking = gen.find_account(default_accounts, SEEDED_ACCOUNTS[0])
        card_types = {row["type"] for row in self._rows(gen, card, count=30)}
        deposit_types = {row["type"] for row in self._rows(gen, checking, count=30)}
        assert "Payment" in card_types and "Payment" not in deposit_types
        assert "Transfer" in deposit_types and "Transfer" not in card_types

    def test_an_account_can_be_named_by_its_last_four(self, gen, default_accounts):
        assert gen.find_account(default_accounts, "5555")["name"] == SEEDED_ACCOUNTS[0]

    def test_the_seeded_ledgers_reconcile(self, gen, default_accounts):
        """The generator's own invariant, turned on the hand-written seed."""
        for account in default_accounts:
            gen.check_chain(account)

    def test_splicing_preserves_comments_and_reconciles(
        self, gen, default_accounts, tmp_path
    ):
        source = gen.content_path("default")
        target = tmp_path / "default.yaml"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        account = gen.find_account(default_accounts, SEEDED_ACCOUNTS[1])
        rows = gen.localize(
            self._rows(gen, account, count=2, seed=3), gen.VARIANTS["default"]
        )
        gen.splice(target, str(account["name"]), gen.render_yaml(rows, indent=6), 2)

        written = gen.load_accounts(target)
        # Only the named account grew, and it still reconciles.
        for name in SEEDED_ACCOUNTS:
            before = len(gen.find_account(default_accounts, name)["transactions"])
            after = len(gen.find_account(written, name)["transactions"])
            assert after == before + (2 if name == SEEDED_ACCOUNTS[1] else 0)
            gen.check_chain(gen.find_account(written, name))
        # The comments that explain the seed are the reason this is a text
        # splice rather than a yaml round-trip.
        assert "# The card." in target.read_text(encoding="utf-8")

    def test_a_broken_splice_leaves_the_file_untouched(self, gen, tmp_path):
        source = gen.content_path("default")
        target = tmp_path / "default.yaml"
        original = source.read_text(encoding="utf-8")
        target.write_text(original, encoding="utf-8")

        # Claiming three rows while inserting one fails the post-write check.
        with pytest.raises(ValueError):
            gen.splice(target, SEEDED_ACCOUNTS[0], '      - date: "Jan 01, 2026"\n', 3)
        assert target.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Fanning the generator out across the content variants
# ---------------------------------------------------------------------------
class TestVariantFanout:
    """A ledger row added to one variant alone breaks every other variant.

    ``german.yaml`` and ``mandarin.yaml`` restate the seeded accounts in full,
    and ``test_seeded_account_figures_are_identical`` requires all three to
    agree on every amount and balance -- so the generator writes them together
    by default. What travels is the figures; the date format and the type
    vocabulary are re-expressed per file.
    """

    TRANSLATED = ["german", "mandarin"]

    @pytest.fixture(scope="class")
    def gen(self):
        from open_apps.apps.openbanking_app import generate_transactions

        return generate_transactions

    @pytest.fixture
    def content(self, gen, tmp_path, monkeypatch):
        """A throwaway copy of the content dir, with the generator pointed at it."""
        target = tmp_path / "content"
        shutil.copytree(gen.CONTENT_DIR, target)
        monkeypatch.setattr(gen, "CONTENT_DIR", target)
        return target

    def _run(self, gen, *argv):
        original = sys.argv
        sys.argv = ["openbanking-gen-txns", *argv]
        try:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    gen.main()
        finally:
            sys.argv = original
        return out.getvalue(), err.getvalue()

    def _figures(self, gen, path, name):
        account = gen.find_account(gen.load_accounts(path), name)
        return [(t["amount"], t["balance"]) for t in account["transactions"]]

    # -- the translation tables ---------------------------------------------

    def test_every_generated_type_can_be_translated(self, gen):
        """A type with no entry would land in `german.yaml` in English, and the
        card tasks count rows by type."""
        emitted = {m.type for m in gen.DEPOSIT_MERCHANTS + gen.CARD_MERCHANTS}
        for stem in self.TRANSLATED:
            assert emitted <= set(gen.VARIANTS[stem].types)

    @pytest.mark.parametrize("stem", ["german", "mandarin"])
    def test_the_type_tables_match_the_seeded_vocabulary(self, gen, stem):
        """The translations are the words these files already use, not invented
        ones -- otherwise a generated row reads as a type the ledger never had."""
        seeded = {
            txn["type"]
            for account in gen.load_accounts(gen.content_path(stem))
            for txn in account["transactions"]
        }
        table = set(gen.VARIANTS[stem].types.values())
        assert table >= seeded, f"{stem} uses types the table does not produce"

    @pytest.mark.parametrize("stem", ["default", "german", "mandarin"])
    def test_every_seeded_date_parses_under_its_own_variant(self, gen, stem):
        """The reader is what lets a fan-out chain off the target's ledger, so
        it has to cope with every date these files actually print."""
        read = gen.VARIANTS[stem].read_date
        for account in gen.load_accounts(gen.content_path(stem)):
            for txn in account["transactions"]:
                if txn["date"] is None:  # pending rows carry no date
                    continue
                assert read(txn["date"]) is not None, txn["date"]

    @pytest.mark.parametrize("stem", ["default", "german", "mandarin"])
    def test_dates_round_trip(self, gen, stem):
        variant = gen.VARIANTS[stem]
        for when in [date(2026, 3, 1), date(2026, 7, 31), date(2026, 12, 25)]:
            assert variant.read_date(variant.render_date(when)) == when

    def test_german_months_are_not_taken_from_the_c_locale(self, gen):
        """`%b` would render "Jul" on one machine and "Juli" on another; the
        seeded ledger says "31. Juli 2026" and has to keep saying it."""
        assert gen._render_german(date(2026, 7, 31)) == "31. Juli 2026"
        assert gen._render_german(date(2026, 8, 5)) == "05. Aug. 2026"

    # -- what fan-out copies and what it re-expresses ------------------------

    def test_localize_keeps_the_figures_and_translates_the_rest(self, gen):
        accounts = gen.load_accounts(gen.content_path("default"))
        account = gen.find_account(accounts, SEEDED_ACCOUNTS[2])
        rows = gen.generate(
            account, count=4, seed=1, max_amount=200.0, types=None, start=None
        )
        english = gen.localize(rows, gen.VARIANTS["default"])
        german = gen.localize(rows, gen.VARIANTS["german"])

        assert [(r["amount"], r["balance"]) for r in english] == [
            (r["amount"], r["balance"]) for r in german
        ]
        assert [r["description"] for r in english] == [r["description"] for r in german]
        assert [r["date"] for r in english] != [r["date"] for r in german]
        assert all(r["type"] in gen.GERMAN_TYPES.values() for r in german)

    def test_the_seeded_accounts_fan_out_to_the_translated_files(self, gen):
        targets = gen.fanout_targets(gen.content_path("default"), SEEDED_ACCOUNTS[0])
        assert sorted(path.stem for _, path in targets) == self.TRANSLATED

    def test_a_noise_variants_own_account_fans_out_nowhere(self, gen):
        """The noise files append with `+accounts`; those accounts are theirs
        alone, so nothing else carries a copy to keep in step."""
        path = gen.content_path("long_descriptions")
        name = str(gen.load_accounts(path)[0]["name"])
        assert gen.fanout_targets(path, name) == []

    # -- writing -------------------------------------------------------------

    def test_a_write_keeps_every_variant_in_step(self, gen, content):
        name = SEEDED_ACCOUNTS[1]
        before = len(self._figures(gen, content / "default.yaml", name))
        self._run(
            gen,
            "--account",
            "8891",
            "--count",
            "2",
            "--types",
            "Interest",
            "--in-place",
        )
        baseline = self._figures(gen, content / "default.yaml", name)
        assert len(baseline) == before + 2
        for stem in self.TRANSLATED:
            assert self._figures(gen, content / f"{stem}.yaml", name) == baseline
            gen.check_chain(
                gen.find_account(gen.load_accounts(content / f"{stem}.yaml"), name)
            )

    def test_each_file_gets_its_own_dates_and_types(self, gen, content):
        self._run(
            gen,
            "--account",
            "8891",
            "--count",
            "1",
            "--types",
            "Interest",
            "--in-place",
        )
        name = SEEDED_ACCOUNTS[1]
        for stem in ["default"] + self.TRANSLATED:
            newest = gen.find_account(
                gen.load_accounts(content / f"{stem}.yaml"), name
            )["transactions"][-1]
            assert gen.VARIANTS[stem].read_date(newest["date"]) is not None
            expected = gen.VARIANTS[stem].types.get("Interest", "Interest")
            assert newest["type"] == expected

    def test_the_translated_blocks_are_flagged_for_translation(self, gen, content):
        """The description is free text, so fan-out cannot finish the job -- it
        has to say so in the file rather than hope someone remembers."""
        self._run(
            gen,
            "--account",
            "8891",
            "--count",
            "1",
            "--types",
            "Interest",
            "--in-place",
        )
        assert "translate them" not in (content / "default.yaml").read_text("utf-8")
        for stem in self.TRANSLATED:
            assert "translate them" in (content / f"{stem}.yaml").read_text("utf-8")

    def test_variants_source_writes_only_the_source(self, gen, content):
        before = {
            stem: (content / f"{stem}.yaml").read_text("utf-8")
            for stem in self.TRANSLATED
        }
        self._run(
            gen,
            "--account",
            "8891",
            "--count",
            "1",
            "--types",
            "Interest",
            "--variants",
            "source",
            "--in-place",
        )
        for stem, text in before.items():
            assert (content / f"{stem}.yaml").read_text("utf-8") == text

    def test_an_unknown_variant_is_named_in_the_error(self, gen, content):
        with pytest.raises(SystemExit, match="klingon"):
            self._run(gen, "--account", "8891", "--variants", "klingon")

    def test_comments_survive_the_fan_out(self, gen, content):
        self._run(
            gen,
            "--account",
            "8891",
            "--count",
            "1",
            "--types",
            "Interest",
            "--in-place",
        )
        # The comment above the card account is the reason every write here is
        # a text splice rather than a yaml round-trip.
        assert "Behind" in (content / "german.yaml").read_text("utf-8")

    def test_a_diverged_variant_is_refused_before_anything_is_written(
        self, gen, content
    ):
        """Appending identical figures only keeps files in step if they were in
        step already; on top of a disagreement it just buries it."""
        german = content / "german.yaml"
        german.write_text(
            german.read_text("utf-8").replace("amount: 18.42", "amount: 18.43"),
            encoding="utf-8",
        )
        before = {path.name: path.read_text("utf-8") for path in content.glob("*.yaml")}
        with pytest.raises(SystemExit, match="disagree"):
            self._run(gen, "--account", "8891", "--count", "1", "--in-place")
        for path in content.glob("*.yaml"):
            assert path.read_text("utf-8") == before[path.name]

    def test_a_failed_splice_rolls_back_every_file(self, gen, content):
        """`splice` restores the one file it was editing; a fan-out writes
        several, and a half-applied one is the state this all exists to avoid."""
        before = {path.name: path.read_text("utf-8") for path in content.glob("*.yaml")}
        row = '      - date: "Jan 01, 2026"\n'
        plan = [
            (content / "default.yaml", SEEDED_ACCOUNTS[0], row, 1),
            # Claiming three rows while inserting one fails the post-write check.
            (content / "german.yaml", SEEDED_ACCOUNTS[0], row, 3),
        ]
        with pytest.raises(ValueError):
            gen.splice_all(plan)
        for path in content.glob("*.yaml"):
            assert path.read_text("utf-8") == before[path.name]
