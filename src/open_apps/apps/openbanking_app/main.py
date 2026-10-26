"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Retail online-banking surface: an account index and a per-account transaction
ledger, modelled on a real bank dashboard.

Read-only from the browser. Nothing an agent can click in *this* app mutates
the seeded state, so ``/openbanking_all`` is byte-identical at episode start
and end unless a purchase happens. That is load-bearing: ``AppStateComparison``
builds every task's target by deep-copying the *initial* cross-app state, so an
app that drifted on its own (auto timestamps, nondeterministic row order) would
inject a spurious diff into every unrelated todo/calendar task's reward.

The one write path is ``authorize_card_purchase``: the online shop calls it to
charge a card at checkout (see ``openbanking_app.main`` imports in
``onlineshop_app``). It is deliberately the only one, it only fires on an
explicit checkout, and it posts *pending* rows -- ``date=None``,
``balance=None`` -- so the payload still gains nothing but the rows the
checkout actually added: the charge, or the declined authorization when the
card could not cover it. A task that involves a shop purchase therefore has to
expect a delta in the ``openbanking`` slice as well as ``online_shop``; a task
that does not involve one still sees a byte-identical bank.
"""

from fasthtml.common import *
from dataclasses import dataclass
from datetime import date
import json
import re
from typing import List, Optional
from urllib.parse import urlencode
from open_apps.apps.start_page.helper import create_logo_header
from open_apps.frontend import local_hdrs
from open_apps.ui.copy import COPY_SCRIPT, CopyButton, copy_styles
from open_apps.theme import theme_asset, theme_style


@dataclass
class Account:
    id: int
    name: str
    holder: str
    # "deposit" (checking/savings) or "credit_card". Selects which summary the
    # detail page renders; everything below `available_credit` is card-only and
    # stays None on a deposit account.
    kind: str
    # Stored in full; the page masks all but the last four digits until the
    # figure is clicked. Kept as strings, not ints, so leading zeros survive.
    # On a card this is the 16-digit PAN, printed in 4-4-4-4 groups.
    account_number: str
    # None on a card -- a card has no routing number, and the app omits the row
    # rather than rendering an empty one.
    routing_number: Optional[str]
    available_balance: float
    present_balance: float
    available_credit: float
    credit_limit: Optional[float] = None
    card_brand: Optional[str] = None
    card_expiration: Optional[str] = None
    # The card's security code. Masked on the card face until clicked, like
    # the PAN -- a checkout elsewhere in the environment needs it, so it has to
    # be reachable, but it should cost the agent the same extra click.
    card_cvv: Optional[str] = None
    statement_balance: Optional[float] = None
    statement_close_date: Optional[str] = None
    minimum_payment: Optional[float] = None
    payment_due_date: Optional[str] = None

    @property
    def is_card(self) -> bool:
        return self.kind == "credit_card"


@dataclass
class Transaction:
    id: int
    account_id: int
    # Ledger order as configured. Preserved explicitly because a statement is
    # ordered newest-first by posting, which the human-readable `date` string
    # ("Aug 25, 2026", "2026年8月24日") cannot be sorted on.
    position: int
    date: Optional[str]  # None => pending, rendered under `pending_label`
    description: str
    type: str
    amount: float
    balance: Optional[float]  # None => no posted balance yet (pending)


app, rt = fast_app(default_hdrs=False, hdrs=local_hdrs())
logo_title_container = None
accounts = None
transactions = None

# Static, theme-agnostic component styles. All colors/fonts are design tokens
# resolved per-request via `theme_style()`, so this block never needs rebuilding
# when the theme or app config changes.
#
# Only tokens from the shared contract in `config/apps/theme/default.yaml` --
# no app-specific ones -- so the bank restyles with every other app under any
# global theme.
styles = Style("""
    body {
        font-family: var(--font-family);
        font-size: var(--font-size-base);
        color: var(--color-fg);
        background-color: var(--color-bg);
        margin: 0;
    }
    a { color: var(--color-accent); text-decoration: none; }
    a:hover { text-decoration: underline; }

    /* --- Pico bridge ----------------------------------------------------
       Pico keeps its own palette in `--pico-*` properties and applies it
       through rules this app never overrides: `td`/`th` paint a cell
       background, `input`/`select` paint a field, `h1`-`h6` take a fixed
       heading colour, and `[role=button].contrast` paints a button. A token
       block cannot reach any of them -- those are real declarations, not
       fallbacks -- so under a dark theme the page came out with a white
       ledger, white filter controls and near-black headings.

       Two halves fix it. `page()` sets `data-theme` on the wrapper from the
       theme's `tone` asset, which flips Pico to the palette of the right
       polarity including the parts no variable reaches (the select chevron
       and the search field's clear affordance are `data:` SVGs baked per
       palette, and `color-scheme` is what makes the native dropdown render
       dark). Then the block below re-points the handful of Pico properties
       this app's markup actually touches at our own tokens, so the result is
       the bank's palette rather than Pico's blue-grey one.

       Scoped to `.ob-root`, not `:root`: these pages are mounted alongside
       every other app's, and Pico's defaults are the right answer everywhere
       else. Specificity ties with Pico's own `[data-theme=...]` selector and
       wins on source order, since this stylesheet follows the vendored one. */
    .ob-root {
        --pico-background-color: var(--color-bg);
        --pico-color: var(--color-fg);
        --pico-muted-color: var(--color-muted);
        --pico-muted-border-color: var(--color-border);
        --pico-h1-color: var(--color-fg);
        --pico-h2-color: var(--color-fg);
        --pico-h3-color: var(--color-fg);
        --pico-h4-color: var(--color-fg);
        --pico-h5-color: var(--color-fg);
        --pico-h6-color: var(--color-fg);
        --pico-primary: var(--color-primary);
        --pico-primary-background: var(--color-primary);
        --pico-primary-border: var(--color-primary);
        --pico-primary-hover-background: var(--color-primary-hover);
        --pico-primary-inverse: var(--color-on-primary);
        /* `.contrast` is what the "Return to List of Apps" button uses. Pico
           reads it as "the far end of the page's own range", which is exactly
           the fg/bg pair. */
        --pico-contrast-background: var(--color-fg);
        --pico-contrast-border: var(--color-fg);
        --pico-contrast-inverse: var(--color-bg);
        --pico-table-border-color: var(--color-border);
        /* The `article` inside the "open an account" dialog. Pico paints the
           body and its header/footer bands from these three. */
        --pico-card-background-color: var(--color-bg);
        --pico-card-sectioning-background-color: var(--color-surface);
        --pico-card-border-color: var(--color-border);
        --pico-form-element-background-color: var(--color-bg);
        --pico-form-element-active-background-color: var(--color-bg);
        --pico-form-element-border-color: var(--color-border);
        --pico-form-element-active-border-color: var(--color-primary);
        --pico-form-element-color: var(--color-fg);
        --pico-form-element-placeholder-color: var(--color-muted);
        --pico-form-element-focus-color: var(--color-primary);
    }

    .ob-masthead {
        display: flex;
        align-items: center;
        gap: 1rem;
        background-color: var(--color-primary);
        color: var(--color-on-primary);
        padding: 0.75rem 1.5rem;
    }
    /* create_logo_header sets `margin-bottom: 1rem` inline for the standalone
       page-header case; inside the masthead bar that leaves a gap under the
       wordmark. A stylesheet `!important` outranks a non-important inline
       declaration, so this wins without forking the shared helper. */
    .ob-masthead > div, .ob-masthead > a {
        margin-bottom: 0 !important;
        color: var(--color-on-primary) !important;
    }
    /* The wordmark is an `H1` inside that container, and Pico colours every
       heading explicitly, so it does not inherit the masthead foreground --
       it was rendering in body-text colour on the bar. It sits on the
       header background, not the page background, so it takes the
       on-primary token rather than `--color-fg` like the headings in the
       bridge above. */
    .ob-masthead h1 {
        color: var(--color-on-primary);
        margin-bottom: 0;
    }
    .ob-masthead-spacer { flex: 1 1 auto; }
    .ob-masthead-actions { display: flex; align-items: center; gap: 1rem; }
    /* Fill and text are the masthead's own pair, inverted -- not `--color-bg`
       over `--color-primary`, which reads fine in a light theme purely because
       the page is white there, and loses contrast under a dark one. */
    .ob-ghost-btn {
        border: 1px solid var(--color-on-primary);
        border-radius: var(--radius);
        padding: 0.4rem 0.9rem;
        font-weight: 600;
        background-color: var(--color-on-primary);
        color: var(--color-primary);
        cursor: pointer;
    }

    /* Pico supplies the overlay, the centring and the close glyph for a
       `dialog[open]`; only the phone number needs saying, since it is the
       reason the dialog exists. */
    .ob-dialog article { max-width: 30rem; }
    .ob-dialog-phone {
        font-size: 1.75rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        color: var(--color-accent);
        margin: 1rem 0;
    }

    .ob-page { padding: 1.5rem; }
    .ob-summary { background-color: var(--color-bg); padding: 1.5rem; }
    .ob-account-name { font-size: 1.05rem; margin: 0; font-weight: 600; }
    .ob-account-holder { color: var(--color-muted); margin: 0.25rem 0 1rem 0; }
    .ob-balance { font-size: 2.75rem; font-weight: 400; margin: 0; }
    /* Dotted underline marks the figures the real dashboard footnotes. */
    .ob-balance-label {
        border-bottom: 1px dotted var(--color-muted);
        display: inline-block;
        margin-bottom: 1.5rem;
    }
    .ob-summary-head { display: flex; justify-content: space-between; align-items: flex-start; }
    .ob-numbers { display: flex; flex-direction: column; gap: 0.75rem; text-align: right; }
    .ob-number-row { display: flex; flex-direction: column; align-items: flex-end; }
    /* The figure is the control, so it has to read as one: link-coloured, with
       the dotted underline this dashboard uses for click-to-disclose text. */
    .ob-number-toggle {
        background: none;
        border: none;
        padding: 0;
        margin: 0;
        width: auto;
        font-family: inherit;
        font-size: 1rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        color: var(--color-accent);
        border-bottom: 1px dotted var(--color-accent);
        border-radius: 0;
        cursor: pointer;
    }
    .ob-number-toggle:hover { text-decoration: none; opacity: 0.8; }
    .ob-number-line { display: flex; align-items: center; gap: 0.6rem; }

    /* The Copy pill's own rules come from open_apps.ui.copy (see page()). */
    .ob-figures { display: flex; gap: 4rem; flex-wrap: wrap; }
    .ob-figure-value { font-weight: 600; }
    .ob-figure-label { color: var(--color-muted); }

    .ob-band { background-color: var(--color-surface); padding: 1.5rem; }
    .ob-band-heading { margin: 0 0 1rem 0; font-size: 1.1rem; font-weight: 600; }
    .ob-card {
        background-color: var(--color-bg);
        border-radius: var(--radius);
        border: 1px solid var(--color-border);
        padding: 1rem 1.25rem;
    }
    .ob-controls {
        display: flex;
        align-items: center;
        gap: 1rem;
        flex-wrap: wrap;
        margin-bottom: 1rem;
    }
    .ob-controls label { margin: 0; }
    .ob-controls select, .ob-controls input { margin: 0; }
    .ob-controls select { min-width: 20rem; }

    .ob-table { width: 100%; border-collapse: collapse; }
    .ob-table th {
        text-align: left;
        border-bottom: 2px solid var(--color-fg);
        padding: 0.5rem 0.75rem;
        font-weight: 600;
    }
    .ob-table td {
        padding: 0.75rem;
        border-bottom: 1px solid var(--color-border);
        vertical-align: top;
    }
    .ob-num { text-align: right; white-space: nowrap; }
    .ob-credit { color: var(--color-accent); font-weight: 600; }
    .ob-debit { color: var(--color-fg); font-weight: 700; }
    .ob-pending td { color: var(--color-muted); }

    .ob-txn-card {
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        padding: 0.75rem 1rem;
        margin-bottom: 0.75rem;
    }
    .ob-txn-card-desc { margin-bottom: 0.5rem; }
    .ob-txn-card-meta {
        display: flex;
        gap: 1.5rem;
        flex-wrap: wrap;
        color: var(--color-muted);
        font-size: 0.9rem;
    }

    /* --- Credit card summary ------------------------------------------- */
    /* The card face reuses the masthead tokens rather than introducing a
       second pair, so a theme that restyles the header restyles the card with
       it and there is nothing extra to define. Both still carry the shared
       fallback, so the card keeps a readable fg/bg pair under `default`,
       `mono`, `solarized` and the rest. */
    .ob-card-layout {
        display: flex;
        gap: 2rem;
        flex-wrap: wrap;
        align-items: flex-start;
    }
    .ob-cardface {
        position: relative;
        flex: 0 0 auto;
        width: 21rem;
        min-height: 12.5rem;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
        padding: 1.1rem 1.25rem;
        border-radius: calc(var(--radius) * 1.5);
        background-color: var(--color-primary);
        color: var(--color-on-primary);
        border: 1px solid var(--color-border);
    }
    .ob-cardface-brand {
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }
    /* The disclosure control sits in the card's top-right corner. It has to
       stay legible on the card face, so it overrides .ob-number-toggle's
       link colouring rather than inheriting it. */
    .ob-cardface-toggle {
        position: absolute;
        top: 0.9rem;
        right: 1rem;
        background: none;
        border: 1px solid var(--color-on-primary);
        border-radius: var(--radius);
        padding: 0.15rem 0.5rem;
        margin: 0;
        width: auto;
        font-family: inherit;
        font-size: 0.75rem;
        font-weight: 600;
        line-height: 1.4;
        color: var(--color-on-primary);
        cursor: pointer;
    }
    .ob-cardface-toggle:hover { opacity: 0.75; }
    .ob-cardface-spacer { flex: 1 1 auto; }
    .ob-cardface-number-line {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
    }
    /* On the card the copy control takes the card's own ink, like the
       disclosure toggle in the corner, rather than the page's muted grey. */
    .ob-copy.ob-copy-on-card,
    .ob-copy.ob-copy-on-card:hover,
    .ob-copy.ob-copy-on-card[data-state] {
        color: var(--color-on-primary);
        border-color: color-mix(in srgb, var(--color-on-primary) 55%, transparent);
    }
    .ob-copy.ob-copy-on-card:hover { border-color: var(--color-on-primary); }
    .ob-copy.ob-copy-on-card { flex: none; font-size: 0.75rem; padding: 0.1rem 0.5rem 0.1rem 0.4rem; }
    /* Sized so the sixteen digits and the Copy pill share one line inside
       the 21rem card, masked or revealed. */
    .ob-cardface-number {
        font-family: var(--font-mono);
        font-size: 1.05rem;
        letter-spacing: 0.1em;
        white-space: nowrap;
    }
    .ob-cardface-foot {
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        gap: 1rem;
    }
    .ob-cardface-holder { text-transform: uppercase; letter-spacing: 0.05em; }
    .ob-cardface-expiry {
        font-size: var(--font-size-sm);
        opacity: 0.85;
        text-transform: uppercase;
    }
    /* Chip and network mark are inline SVG: a raster asset would need a
       per-theme light/dark variant, and a remote one would put a CDN in the
       request path that tests/test_no_egress.py forbids. `currentColor` makes
       both inherit the card's foreground token for free. */
    .ob-cardface-chip, .ob-cardface-mark { display: block; }

    .ob-pay-figures {
        flex: 1 1 18rem;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
        gap: 1.25rem 2rem;
    }
    .ob-pay-due .ob-figure-value { color: var(--color-danger); }
    .ob-pay-note { color: var(--color-muted); font-size: var(--font-size-sm); }

    .ob-see-more { display: block; text-align: center; margin-top: 1rem; font-weight: 600; }
    .ob-account-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid var(--color-border);
        padding: 1rem 0;
    }
    .ob-account-row:last-child { border-bottom: none; }
    /* The rows are links; keep them reading as rows, not as blue text. */
    a.ob-account-row, a.ob-account-row:hover { color: inherit; text-decoration: none; }
    a.ob-account-row:hover .ob-account-name-cell { text-decoration: underline; }
    .ob-empty { color: var(--color-muted); padding: 1rem 0; }
""")


def set_environment(config):
    """Set environment variables for the openbanking app"""
    global app, logo_title_container, accounts, transactions
    app.config = config
    db = database(config.openbanking.database_path)
    accounts = db.create(Account, pk="id")
    transactions = db.create(Transaction, pk="id")

    # set_environment is called again on reset, so clear before re-seeding
    # rather than relying on the caller having dropped the tables. It also
    # matters now that a purchase writes here: a reset has to put the card's
    # figures back, not collide on the seeded primary keys.
    for table in (transactions, accounts):
        table.delete_where()

    print("Populating initial accounts and transactions from config")
    txn_id = 0
    for account_id, account_cfg in enumerate(config.openbanking.accounts):
        # `.get` on the card-only keys: the noise content variants append plain
        # checking accounts with `+accounts` and should not have to restate
        # every field a card happens to need.
        def opt_str(key):
            value = account_cfg.get(key, None)
            return None if value is None else str(value)

        def opt_float(key):
            value = account_cfg.get(key, None)
            return None if value is None else float(value)

        accounts.insert(
            Account(
                id=account_id,
                name=account_cfg.name,
                holder=account_cfg.holder,
                kind=account_cfg.get("kind", "deposit"),
                account_number=str(account_cfg.account_number),
                routing_number=opt_str("routing_number"),
                available_balance=float(account_cfg.available_balance),
                present_balance=float(account_cfg.present_balance),
                available_credit=float(account_cfg.available_credit),
                credit_limit=opt_float("credit_limit"),
                card_brand=opt_str("card_brand"),
                card_expiration=opt_str("card_expiration"),
                card_cvv=opt_str("card_cvv"),
                statement_balance=opt_float("statement_balance"),
                statement_close_date=opt_str("statement_close_date"),
                minimum_payment=opt_float("minimum_payment"),
                payment_due_date=opt_str("payment_due_date"),
            )
        )
        for position, txn_cfg in enumerate(account_cfg.transactions):
            balance = txn_cfg.balance
            transactions.insert(
                Transaction(
                    id=txn_id,
                    account_id=account_id,
                    position=position,
                    date=txn_cfg.date,
                    description=txn_cfg.description,
                    type=txn_cfg.type,
                    amount=float(txn_cfg.amount),
                    balance=None if balance is None else float(balance),
                )
            )
            txn_id += 1

    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.openbanking,
        base_url="/openbanking",
        current_file_path=__file__,
    )


def openbanking_theme():
    """The active theme's `:root` token block, resolved per-request so live
    `reconfigure` theme swaps take effect."""
    return theme_style(app.config, "openbanking")


def pico_theme() -> str:
    """Pico's palette polarity for the active theme: ``"dark"`` or ``"light"``.

    Read per-request for the same reason as the token block. Only ``dark`` is
    matched explicitly -- ``mono`` and any tone a future theme invents are
    light-on-paper, and Pico has no third palette to offer them.
    """
    tone = theme_asset(app.config, "openbanking", "tone", "light")
    return "dark" if str(tone) == "dark" else "light"


def cfg():
    """The app's own config node."""
    return app.config.openbanking


def current_layout():
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return getattr(config.openbanking, "layout", "default")


def visible_transaction_count() -> int:
    """How many ledger rows show before "See more activity"; ``0`` = all.

    A `layout` knob rather than a content one: it changes how much of the same
    ledger is on screen at once, which is structure, not wording. Defaulted
    here as well so a config that predates the key still renders.
    """
    config = getattr(app, "config", None)
    if config is None:
        return 4
    return int(getattr(config.openbanking, "visible_transactions", 4))


def mask_number(value: str, visible: int = 4) -> str:
    """Hide all but the trailing ``visible`` digits behind bullets.

    Bullets rather than asterisks to match what the real dashboard prints, and
    one bullet per hidden digit rather than a fixed-width run so the masked
    figure still reveals its length -- an account number and a routing number
    are told apart that way before either is clicked.
    """
    value = str(value)
    if len(value) <= visible:
        return value
    return "•" * (len(value) - visible) + value[-visible:]


def fmt_money(value: Optional[float]) -> str:
    """Render a signed currency figure, or an em dash when there is none.

    Uses an ASCII hyphen-minus rather than the typographic minus the real
    dashboard prints: agents read these figures back out of the accessibility
    tree and into task answers, and U+2212 would not round-trip through a
    string comparison against a plainly-typed goal.
    """
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def group_card_number(value: str, revealed: bool) -> str:
    """A PAN in 4-4-4-4 groups, masked to its last four until revealed.

    Grouped even while masked, because that grouping is most of what makes the
    figure read as a card number rather than as an account number -- the whole
    point of giving the card its own summary.
    """
    digits = str(value)
    shown = digits if revealed else mask_number(digits)
    return " ".join(shown[i : i + 4] for i in range(0, len(shown), 4))


# Inline SVG so the card needs no raster asset (which would want a per-theme
# light/dark variant) and no remote fetch (which `tests/test_no_egress.py`
# forbids). Both marks are stroked/filled in `currentColor`, so they inherit
# the card face's foreground token under every theme.
_CHIP_SVG = NotStr(
    '<svg class="ob-cardface-chip" width="38" height="29" viewBox="0 0 38 29" '
    'fill="none" aria-hidden="true" focusable="false">'
    '<rect x="0.75" y="0.75" width="36.5" height="27.5" rx="4.5" '
    'fill="currentColor" fill-opacity="0.22" stroke="currentColor" '
    'stroke-opacity="0.7" stroke-width="1.5"/>'
    '<path d="M0.75 9.5H11M0.75 19.5H11M27 9.5H37.25M27 19.5H37.25M11 0.75V28.25'
    'M27 0.75V28.25M11 14.5H27" stroke="currentColor" stroke-opacity="0.7" '
    'stroke-width="1.5"/>'
    "</svg>"
)

# An invented two-disc network mark. Deliberately not any real network's logo.
_NETWORK_MARK_SVG = NotStr(
    '<svg class="ob-cardface-mark" width="46" height="29" viewBox="0 0 46 29" '
    'fill="none" aria-hidden="true" focusable="false">'
    '<circle cx="15" cy="14.5" r="13" fill="currentColor" fill-opacity="0.85"/>'
    '<circle cx="31" cy="14.5" r="13" fill="currentColor" fill-opacity="0.45"/>'
    "</svg>"
)


def account_rows() -> List[Account]:
    return sorted(accounts(), key=lambda a: a.id)


def txns_for(account_id: int) -> List[Transaction]:
    return sorted(
        (t for t in transactions() if t.account_id == account_id),
        key=lambda t: t.position,
    )


def get_account(account_id: int) -> Optional[Account]:
    for account in account_rows():
        if account.id == account_id:
            return account
    return None


# ---------------------------------------------------------------------------
# Payments -- the app's only write path.
#
# Called by the online shop's checkout, never from this app's own UI. See the
# module docstring for why that exception is narrow on purpose. The posted rows
# are pending (`date=None`, `balance=None`): a real issuer shows a card charge
# that way until the statement cuts, and it keeps `datetime.now()` -- the one
# thing guaranteed to differ between two runs -- out of `/openbanking_all`.


@dataclass
class ChargeResult:
    """Outcome of an authorization attempt.

    `code` is for the caller to branch on, `message` is for the shopper. The
    shop renders `message` verbatim, so it says what went wrong without ever
    naming the figure the agent was supposed to look up: a declined card does
    not tell you the balance.
    """

    approved: bool
    code: str
    message: str
    account_id: Optional[int] = None
    card_last4: str = ""
    # How far past `available_credit` the charge went. Non-zero only on an
    # approval inside the grace band.
    over_limit_by: float = 0.0


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _norm_holder(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def parse_expiry(value) -> Optional[tuple]:
    """Card expiry as ``(year, month)``, or None when it is not a real date.

    Accepts what a checkout form realistically receives -- "09/29", "9/29",
    "09 / 2029", "0929", "092029" -- so a correct card is not declined over
    punctuation. Rejects anything that is not a month 1-12 with a two- or
    four-digit year, so "13/29" cannot slip through as a lucky digit match.
    """
    parts = re.findall(r"\d+", str(value or ""))
    if len(parts) == 1 and len(parts[0]) in (4, 6):  # MMYY / MMYYYY, unseparated
        parts = [parts[0][:2], parts[0][2:]]
    if len(parts) != 2:
        return None
    month, year = parts
    if len(month) > 2 or len(year) not in (2, 4) or not 1 <= int(month) <= 12:
        return None
    return (int(year) + (2000 if len(year) == 2 else 0), int(month))


def as_of_date() -> date:
    """The bank's "today", which decides whether a card has expired.

    Pinned in config (`as_of_date`) rather than read off the wall clock so a
    seeded card does not quietly expire under a task years from now and turn
    every purchase on it into a decline. Null falls back to the real date.
    """
    value = getattr(cfg(), "as_of_date", None)
    return date.fromisoformat(str(value)) if value else date.today()


def expiry_has_passed(expiry: tuple) -> bool:
    """Whether a parsed ``(year, month)`` is before the bank's current month.

    A card is good through the last day of its expiry month, so 08/26 still
    works on 2026-08-31 and stops on 2026-09-01.
    """
    today = as_of_date()
    return expiry < (today.year, today.month)


def overlimit_grace() -> float:
    """Dollars a charge may exceed `available_credit` by and still authorize."""
    return float(getattr(cfg(), "overlimit_grace", 0.0) or 0.0)


def find_card(number) -> Optional[Account]:
    """The card account with this PAN, ignoring spacing, or None."""
    wanted = _digits(number)
    if not wanted:
        return None
    for account in account_rows():
        if account.is_card and _digits(account.account_number) == wanted:
            return account
    return None


def _next_txn_id() -> int:
    ids = [t.id for t in transactions()]
    return max(ids) + 1 if ids else 0


def _next_position(account_id: int) -> int:
    """Position for a row that should sort above every existing one.

    `txns_for` orders ascending and treats 0 as most-recent, so a new posting
    goes *below* zero rather than renumbering the seeded rows. Shifting every
    existing row up by one on each purchase would rewrite the whole ledger in
    `/openbanking_all` and drown the two rows that actually changed.
    """
    positions = [t.position for t in transactions() if t.account_id == account_id]
    return min(positions) - 1 if positions else 0


def _post(account_id: int, rows: List[Transaction]) -> None:
    """Insert `rows` above the account's existing ledger, first row on top."""
    base = _next_position(account_id)
    next_id = _next_txn_id()
    for offset, row in enumerate(reversed(rows)):
        row.position = base - offset
    for row in rows:
        row.id = next_id
        next_id += 1
        transactions.insert(row)


def authorize_card_purchase(
    number: str,
    expiration: str,
    cvv: str,
    holder: str,
    amount: float,
    descriptor: str,
    merchant: Optional[str] = None,
) -> ChargeResult:
    """Authorize `amount` against a seeded credit card and post it.

    Every detail has to match the card on file: PAN, expiry, CVV and cardholder
    name, and the card must not have expired. A mismatch declines without
    saying *which* field was wrong beyond the coarse reason -- enumerating the
    failure would let an agent brute-force the CVV three digits at a time.

    Approves when `amount` fits inside `available_credit + overlimit_grace()`.
    A charge that lands in the grace band still goes through, and posts a second
    row naming the overage so the ledger reports it.

    A charge past the grace band is refused, but not silently: it posts one
    zero-amount row recording the declined authorization, as a real card's
    activity feed does. Without it a refused checkout left both apps exactly as
    they were before the attempt, so a task could not tell "tried to pay and
    was refused" from "never tried". That row names `merchant` rather than
    `descriptor`, because a descriptor carries an order id and a refused
    checkout never creates the order it would point to. `merchant` defaults to
    `descriptor` for callers that have no order id to leave out.

    Detail mismatches still write nothing. Those are typos, and a ledger row
    per typo would fail every purchase task whose agent mistyped the CVV once
    before getting it right.
    """
    c = cfg()
    if accounts is None or transactions is None:
        return ChargeResult(False, "unavailable", c.charge_unavailable_message)

    account = find_card(number)
    if account is None:
        return ChargeResult(False, "unknown_card", c.charge_declined_message)

    last4 = _digits(account.account_number)[-4:]
    on_file = parse_expiry(account.card_expiration)
    if on_file is None or parse_expiry(expiration) != on_file:
        return ChargeResult(False, "bad_details", c.charge_declined_message, account.id, last4)
    if expiry_has_passed(on_file):
        return ChargeResult(False, "expired", c.charge_declined_message, account.id, last4)
    if _digits(cvv) != _digits(account.card_cvv):
        return ChargeResult(False, "bad_details", c.charge_declined_message, account.id, last4)
    if _norm_holder(holder) != _norm_holder(account.holder):
        return ChargeResult(False, "bad_details", c.charge_declined_message, account.id, last4)

    amount = round(float(amount), 2)
    if amount <= 0:
        return ChargeResult(False, "bad_amount", c.charge_declined_message, account.id, last4)

    headroom = round(account.available_credit, 2)
    over_by = round(amount - headroom, 2)
    if over_by > round(overlimit_grace(), 2):
        # No money moves and the balances stay put; the row is the record that
        # the attempt happened. The attempted amount is in the text, the
        # shortfall is not -- the decline still does not tell you the balance.
        _post(account.id, [
            Transaction(
                id=None,
                account_id=account.id,
                position=0,
                date=None,
                description=c.declined_description.format(
                    merchant=merchant if merchant is not None else descriptor,
                    amount=fmt_money(amount),
                ),
                type=c.declined_type,
                amount=0.0,
                balance=None,
            )
        ])
        return ChargeResult(
            False, "insufficient_credit", c.charge_insufficient_message, account.id, last4
        )

    # Approved. The charge first, then -- only if it broke the limit -- the
    # notice, so the ledger reads top-down as "this happened, and here is what
    # was unusual about it".
    rows = [
        Transaction(
            id=None,
            account_id=account.id,
            position=0,
            date=None,
            description=descriptor,
            type=c.purchase_type,
            amount=-amount,
            balance=None,
        )
    ]
    if over_by > 0:
        rows.append(
            Transaction(
                id=None,
                account_id=account.id,
                position=0,
                date=None,
                description=c.overlimit_description.format(amount=fmt_money(over_by)),
                type=c.overlimit_type,
                amount=0.0,
                balance=None,
            )
        )

    _post(account.id, rows)

    # A card's `present_balance` is what is owed, carried negative, so a charge
    # pushes it further down while eating the same amount of headroom.
    account.present_balance = round(account.present_balance - amount, 2)
    account.available_credit = round(headroom - amount, 2)
    accounts.update(account)

    return ChargeResult(
        approved=True,
        code="over_limit_grace" if over_by > 0 else "approved",
        message=c.charge_approved_message,
        account_id=account.id,
        card_last4=last4,
        over_limit_by=max(over_by, 0.0),
    )


# ---------------------------------------------------------------------------
# Filtering.
#
# The "Showing" options are configurable strings (and translated in the
# german/mandarin variants), so the predicate is keyed off the option's
# *index* in `showing_options`, not its text.


def filter_txns(
    txns: List[Transaction], showing_index: int, query: str
) -> List[Transaction]:
    if showing_index == 1:  # deposits / money in
        txns = [t for t in txns if t.amount > 0]
    elif showing_index == 2:  # withdrawals / money out
        txns = [t for t in txns if t.amount < 0]
    if query:
        needle = query.strip().lower()
        txns = [
            t
            for t in txns
            if needle in t.description.lower() or needle in t.type.lower()
        ]
    return txns


# ---------------------------------------------------------------------------
# Components.


def masthead():
    """The bar. Every control in it does something.

    It used to also carry a hamburger, a magnifier, a help and a profile glyph
    and a "Sign out" caption, all inert. Chrome that looks clickable and isn't
    is worse than absent here: an agent that tries one learns nothing from the
    non-response and burns a step, so the bar now holds only the wordmark and
    the one CTA that has an answer.
    """
    c = cfg()
    actions = [
        Button(
            label,
            cls="ob-ghost-btn",
            type="button",
            hx_get="/openbanking/open-account",
            hx_target="#ob-dialog",
            hx_swap="outerHTML",
        )
        for label in c.nav_actions
    ]
    return Div(
        logo_title_container,
        Span(cls="ob-masthead-spacer"),
        Div(*actions, cls="ob-masthead-actions"),
        cls="ob-masthead",
    )


def open_account_dialog(shown: bool = False):
    """The CTA's answer: a modal pointing the caller at the phone line.

    Rendered by a GET partial and swapped into a placeholder, exactly like the
    number disclosures -- opening or closing it writes nothing, so the app
    stays read-only and ``/openbanking_all`` stays byte-identical.
    """
    if not shown:
        return Div(id="ob-dialog")
    c = cfg()
    dismiss = {
        "hx_get": "/openbanking/open-account?show=0",
        "hx_target": "#ob-dialog",
        "hx_swap": "outerHTML",
    }
    return Div(
        Dialog(
            Article(
                Header(
                    Button(
                        type="button", rel="prev", aria_label=c.close_label, **dismiss
                    ),
                    H3(c.open_account_heading),
                ),
                P(c.open_account_body),
                # The number is the point of the dialog, so it is the largest
                # thing in it rather than a run of body text.
                Div(c.open_account_phone, cls="ob-dialog-phone"),
                Footer(
                    Button(c.close_label, cls="secondary", type="button", **dismiss)
                ),
            ),
            open=True,
            cls="ob-dialog",
        ),
        id="ob-dialog",
    )


def figure(value: str, label: str):
    return Div(
        Div(value, cls="ob-figure-value"),
        Div(label, cls="ob-figure-label"),
    )


def number_row(account: Account, kind: str, revealed: bool, other_revealed: bool):
    """One label + click-to-reveal figure.

    The figure is the control -- clicking it toggles between the masked and
    full value. Each row carries the *other* row's current state in its query
    string so a swap of the shared container does not silently re-hide the
    row the agent already opened.
    """
    c = cfg()
    label = c.account_number_label if kind == "account" else c.routing_number_label
    value = account.account_number if kind == "account" else account.routing_number
    flags = {
        "account": other_revealed if kind == "routing" else not revealed,
        "routing": other_revealed if kind == "account" else not revealed,
    }
    query = f"account={int(flags['account'])}&routing={int(flags['routing'])}"
    return Div(
        Div(label, cls="ob-figure-label"),
        Div(
            Button(
                value if revealed else mask_number(value),
                type="button",
                cls="ob-number-toggle",
                title=c.hide_number_label if revealed else c.show_number_label,
                aria_expanded="true" if revealed else "false",
                hx_get=f"/openbanking/accounts/{account.id}/numbers?{query}",
                hx_target="#ob-numbers",
                hx_swap="outerHTML",
            ),
            copy_button(account, kind, label),
            cls="ob-number-line",
        ),
        id=f"ob-number-{kind}",
        cls="ob-number-row",
    )


def copy_button(account: Account, kind: str, label: str, cls: str = ""):
    """An icon-and-text "Copy" control for one of the account's numbers.

    It copies the *full* number whether or not the figure beside it is
    revealed -- that is what a bank's copy button does, and it is the useful
    half of the pair: reading a masked number back is a two-step disclosure,
    pasting it into the shop's checkout is one click.

    The number is not in the markup. Putting it in a ``data-`` attribute would
    hand it to any agent observing the HTML and quietly void the click-to-
    reveal contract above, so the click fetches it from
    ``/openbanking/accounts/{id}/copy/{kind}`` instead -- a read, like the
    reveal, and just as invisible to ``/openbanking_all``.
    """
    c = cfg()
    return CopyButton(
        label,
        url=f"/openbanking/accounts/{account.id}/copy/{kind}",
        copy_label=c.copy_label,
        copied_label=c.copied_label,
        failed_label=c.copy_failed_label,
        prefix="ob-copy",
        testid=f"copy-{kind}-number",
        cls=cls,
    )


def number_panel(
    account: Account, show_account: bool = False, show_routing: bool = False
):
    """The two stacked, independently revealable figures."""
    return Div(
        number_row(account, "account", show_account, show_routing),
        number_row(account, "routing", show_routing, show_account),
        id="ob-numbers",
        cls="ob-numbers",
    )


def card_face(account: Account, revealed: bool = False):
    """The card graphic, with the PAN disclosure in its top-right corner.

    Same disclosure contract as the deposit account's figures: the state lives
    in the query string, nothing is written back, so revealing a number leaves
    ``/openbanking_all`` byte-identical.
    """
    c = cfg()
    return Div(
        Button(
            c.hide_number_label if revealed else c.show_number_label,
            type="button",
            cls="ob-cardface-toggle",
            aria_expanded="true" if revealed else "false",
            aria_label=c.card_number_label,
            hx_get=(
                f"/openbanking/accounts/{account.id}/numbers"
                f"?account={int(not revealed)}"
            ),
            hx_target="#ob-cardface",
            hx_swap="outerHTML",
        ),
        Div(account.card_brand or "", cls="ob-cardface-brand"),
        _CHIP_SVG,
        Span(cls="ob-cardface-spacer"),
        Div(
            Div(
                group_card_number(account.account_number, revealed),
                cls="ob-cardface-number",
            ),
            copy_button(account, "card", c.card_number_label, cls="ob-copy-on-card"),
            cls="ob-cardface-number-line",
        ),
        Div(
            Div(
                Div(account.holder, cls="ob-cardface-holder"),
                Div(
                    f"{c.card_expires_label} {account.card_expiration}"
                    if account.card_expiration
                    else "",
                    cls="ob-cardface-expiry",
                ),
                # Rides the same disclosure as the PAN -- one click turns the
                # card over, which is also how you read a real one. A checkout
                # needs the CVV, so it has to be reachable somewhere.
                Div(
                    f"{c.card_cvv_label} "
                    f"{account.card_cvv if revealed else '•' * len(account.card_cvv)}"
                    if account.card_cvv
                    else "",
                    cls="ob-cardface-expiry",
                ),
            ),
            _NETWORK_MARK_SVG,
            cls="ob-cardface-foot",
        ),
        id="ob-cardface",
        cls="ob-cardface",
    )


def card_summary(account: Account, show_account: bool = False):
    """A card's summary: the graphic, then the figures a cardholder acts on.

    Ordered by what gets acted on rather than by size -- what is owed, the
    least that can be paid, and by when -- with the spending headroom and the
    limit after them. A deposit account's balance hero would be actively
    misleading here: `available_balance` on a card is 0.00.
    """
    c = cfg()
    figures = [
        figure(fmt_money(account.statement_balance), c.statement_balance_label),
        figure(fmt_money(account.minimum_payment), c.minimum_payment_label),
        Div(
            Div(account.payment_due_date or "—", cls="ob-figure-value"),
            Div(c.payment_due_label, cls="ob-figure-label"),
            cls="ob-pay-due",
        ),
        figure(fmt_money(abs(account.present_balance)), c.current_balance_label),
        figure(fmt_money(account.available_credit), c.available_to_spend_label),
        figure(fmt_money(account.credit_limit), c.credit_limit_label),
    ]
    return Div(
        # No holder subtitle here: the card face already prints it, and the
        # deposit summary's name/holder pair would just repeat it two lines up.
        H2(account.name, cls="ob-account-name"),
        Div(
            card_face(account, show_account),
            Div(*figures, cls="ob-pay-figures"),
            cls="ob-card-layout",
        ),
        P(
            f"{c.statement_close_label}: {account.statement_close_date}"
            if account.statement_close_date
            else "",
            cls="ob-pay-note",
        ),
        cls="ob-summary",
    )


def account_summary(
    account: Account, show_account: bool = False, show_routing: bool = False
):
    if account.is_card:
        return card_summary(account, show_account)
    c = cfg()
    return Div(
        Div(
            Div(
                H2(account.name, cls="ob-account-name"),
                P(account.holder, cls="ob-account-holder"),
            ),
            number_panel(account, show_account, show_routing),
            cls="ob-summary-head",
        ),
        P(fmt_money(account.available_balance), cls="ob-balance"),
        Div(c.available_balance_label, cls="ob-balance-label"),
        Div(
            figure(fmt_money(account.present_balance), c.present_balance_label),
            figure(fmt_money(account.available_credit), c.available_credit_label),
            figure(
                fmt_money(account.present_balance + account.available_credit),
                c.available_plus_credit_label,
            ),
            cls="ob-figures",
        ),
        cls="ob-summary",
    )


def amount_cell(txn: Transaction):
    cls = "ob-num ob-debit" if txn.amount < 0 else "ob-num ob-credit"
    return Td(fmt_money(txn.amount), cls=cls)


def txn_table(txns: List[Transaction]):
    c = cfg()
    labels = c.column_labels
    head = Thead(
        Tr(
            Th(labels.date),
            Th(labels.description),
            Th(labels.type),
            Th(labels.amount, cls="ob-num"),
            Th(labels.balance, cls="ob-num"),
        )
    )
    rows = []
    for txn in txns:
        rows.append(
            Tr(
                Td(txn.date if txn.date else c.pending_label),
                Td(txn.description),
                Td(txn.type),
                amount_cell(txn),
                Td(fmt_money(txn.balance), cls="ob-num"),
                cls="ob-pending" if txn.date is None else None,
                id=f"txn-{txn.id}",
            )
        )
    return Table(head, Tbody(*rows), cls="ob-table")


def txn_cards(txns: List[Transaction]):
    c = cfg()
    labels = c.column_labels
    cards = []
    for txn in txns:
        cards.append(
            Div(
                Div(txn.description, cls="ob-txn-card-desc"),
                Div(
                    Span(f"{labels.date}: {txn.date if txn.date else c.pending_label}"),
                    Span(f"{labels.type}: {txn.type}"),
                    Span(f"{labels.amount}: {fmt_money(txn.amount)}"),
                    Span(f"{labels.balance}: {fmt_money(txn.balance)}"),
                    cls="ob-txn-card-meta",
                ),
                cls="ob-txn-card",
                id=f"txn-{txn.id}",
            )
        )
    return Div(*cards)


def see_more_toggle(account: Account, showing_index: int, query: str, expanded: bool):
    """The "See more activity" / "See less" control under a truncated ledger.

    Same disclosure contract as the account-number figures: the open/closed
    state lives in the query string and nothing is written back, so expanding
    the ledger leaves ``/openbanking_all`` byte-identical.

    A real ``href`` as well as the htmx swap, because this one is worth
    deep-linking and has to keep working without htmx -- the account page
    reads the same ``expand`` flag the partial does.
    """
    c = cfg()
    flag = int(not expanded)
    params = urlencode({"showing": showing_index, "q": query, "expand": flag})
    return A(
        c.see_less_label if expanded else c.see_more_label,
        href=f"/openbanking/accounts/{account.id}?{params}",
        cls="ob-see-more",
        aria_expanded="true" if expanded else "false",
        hx_get=f"/openbanking/accounts/{account.id}/ledger?expand={flag}",
        # The live filter and search box rather than the values baked into the
        # href, so expanding after typing keeps what was typed.
        hx_include="#showing, #ob-search",
        hx_target="#ob-ledger",
        hx_swap="outerHTML",
        hx_push_url="true",
    )


def ledger(account: Account, showing_index: int, query: str, expanded: bool = False):
    """The swappable part of the transactions panel.

    Shows the newest :func:`visible_transaction_count` postings and hides the
    rest behind the toggle, which is what a real statement view does -- and
    what makes "find the largest charge" a task about navigating the ledger
    rather than about reading one screenshot.

    Changing the filter or the search term re-runs the query collapsed: the
    two controls do not carry the flag, so a new result set starts at its
    first page. Only the toggle sets it.
    """
    c = cfg()
    matches = filter_txns(txns_for(account.id), showing_index, query)
    limit = visible_transaction_count()
    truncatable = 0 < limit < len(matches)
    txns = matches if expanded or not truncatable else matches[:limit]

    if not txns:
        body = Div(c.no_results_label, cls="ob-empty")
    elif current_layout() == "card_list":
        body = txn_cards(txns)
    else:
        body = txn_table(txns)
    return Div(
        body,
        see_more_toggle(account, showing_index, query, expanded)
        if truncatable
        else None,
        id="ob-ledger",
    )


def transactions_panel(
    account: Account, showing_index: int, query: str, expanded: bool = False
):
    c = cfg()
    target = f"/openbanking/accounts/{account.id}/ledger"
    options = [
        Option(label, value=str(i), selected=(i == showing_index))
        for i, label in enumerate(c.showing_options)
    ]
    controls = Div(
        Label(c.showing_label, fr="showing"),
        Select(
            *options,
            id="showing",
            name="showing",
            hx_get=target,
            hx_target="#ob-ledger",
            hx_swap="outerHTML",
            hx_include="#ob-search",
            hx_push_url="true",
        ),
        Input(
            id="ob-search",
            name="q",
            type="search",
            value=query,
            placeholder=c.search_placeholder,
            aria_label=c.search_label,
            hx_get=target,
            hx_target="#ob-ledger",
            hx_swap="outerHTML",
            hx_include="#showing",
            hx_trigger="search, keyup changed delay:300ms",
            hx_push_url="true",
        ),
        cls="ob-controls",
    )
    return Div(
        H2(c.transactions_heading, cls="ob-band-heading"),
        Div(
            controls,
            ledger(account, showing_index, query, expanded),
            cls="ob-card",
        ),
        cls="ob-band",
    )


def page(*content):
    return Div(
        openbanking_theme(),
        styles,
        masthead(),
        *content,
        # Empty until the masthead CTA is clicked; the swap target has to exist
        # on every page because the CTA sits in the bar on every page.
        open_account_dialog(),
        Style(copy_styles("ob-copy")),
        COPY_SCRIPT,
        cls="ob-root",
        data_theme=pico_theme(),
    )


# ---------------------------------------------------------------------------
# Routes.


@rt("/openbanking")
def openbanking_index():
    c = cfg()
    # A card's `available_balance` is 0.00, so listing it under "Available
    # balance" would tell the agent nothing and imply the card is empty. Cards
    # show what is owed instead.
    # Each row is a plain link, not an htmx swap. It used to `hx-get` the
    # account page into `body`, and the window chrome is spliced in only on
    # full-page loads (open_apps.chrome_middleware passes htmx requests
    # through), so opening an account silently dropped the title bar and the
    # dock for the rest of the visit. A link is also what a bank's account
    # list is: middle-clickable, and announced as one in the a11y tree.
    rows = [
        A(
            Div(
                Div(account.name, cls="ob-account-name-cell", style="font-weight: 600;"),
                Div(account.holder, cls="ob-figure-label"),
            ),
            Div(
                Div(
                    fmt_money(
                        abs(account.present_balance)
                        if account.is_card
                        else account.available_balance
                    ),
                    cls="ob-num ob-figure-value",
                ),
                Div(
                    c.current_balance_label
                    if account.is_card
                    else c.available_balance_label,
                    cls="ob-num ob-figure-label",
                ),
            ),
            cls="ob-account-row",
            id=f"account-{account.id}",
            href=f"/openbanking/accounts/{account.id}",
        )
        for account in account_rows()
    ]
    return page(
        Div(
            H2(c.accounts_heading, cls="ob-band-heading"),
            Div(*rows, cls="ob-card")
            if rows
            else Div(c.no_results_label, cls="ob-empty"),
            A(
                "Return to List of Apps",
                href="/",
                role="button",
                cls="contrast",
                style="margin-top: 1rem;",
            ),
            cls="ob-page",
        )
    )


@rt("/openbanking/accounts/{account_id}")
def openbanking_account_detail(
    account_id: int,
    showing: int = 0,
    q: str = "",
    account: int = 0,
    routing: int = 0,
    expand: int = 0,
):
    acct = get_account(account_id)
    if acct is None:
        return page(Div(cfg().no_results_label, cls="ob-page"))
    return page(
        account_summary(acct, bool(account), bool(routing)),
        transactions_panel(acct, showing, q, bool(expand)),
        Div(
            A(cfg().back_to_accounts_label, href="/openbanking"),
            " · ",
            A("Return to List of Apps", href="/"),
            cls="ob-page",
        ),
    )


@rt("/openbanking/accounts/{account_id}/ledger")
def openbanking_account_ledger(
    htmx: HtmxHeaders,
    account_id: int,
    showing: int = 0,
    q: str = "",
    expand: int = 0,
):
    """htmx partial: the filtered/searched ledger only.

    The filter, search box and toggle all push history, but what lands in the
    address bar must be the account page, not this fragment -- otherwise a
    reload renders a bare, unstyled ledger. So the swap pushes the detail URL
    (which reads the same params), and a non-htmx hit on this route -- an old
    history entry or a bookmark -- gets the full page.
    """
    if not htmx.request:
        return openbanking_account_detail(account_id, showing, q, expand=expand)
    account = get_account(account_id)
    if account is None:
        return Div(cfg().no_results_label, cls="ob-empty", id="ob-ledger")
    params = urlencode({"showing": showing, "q": q, "expand": expand})
    return (
        ledger(account, showing, q, bool(expand)),
        HtmxResponseHeaders(push_url=f"/openbanking/accounts/{account_id}?{params}"),
    )


@rt("/openbanking/accounts/{account_id}/numbers")
def openbanking_account_numbers(account_id: int, account: int = 0, routing: int = 0):
    """htmx partial: the account/routing figures at their requested visibility.

    Disclosure only -- reveals nothing that is not already seeded and writes
    nothing back, so the app stays read-only and ``/openbanking_all`` stays
    byte-stable regardless of what the agent has clicked open.
    """
    acct = get_account(account_id)
    if acct is None:
        return Div(cfg().no_results_label, cls="ob-empty", id="ob-numbers")
    if acct.is_card:
        # A card has one number, not two, and it lives on the graphic -- so the
        # swap replaces the card face and the `routing` flag is meaningless.
        return card_face(acct, bool(account))
    return number_panel(acct, bool(account), bool(routing))


@rt("/openbanking/accounts/{account_id}/copy/{kind}")
def openbanking_copy_number(account_id: int, kind: str):
    """The full number a Copy button puts on the clipboard, as plain text.

    Digits only, no grouping, so it pastes cleanly into a form field. A read
    with no side effect, like the reveal partial: ``/openbanking_all`` stays
    byte-identical however often it is hit. Unknown accounts and kinds 404
    rather than return an empty string that would paste as nothing.
    """
    acct = get_account(account_id)
    if acct is None:
        return Response("", status_code=404)
    value = {
        "account": acct.account_number,
        "card": acct.account_number if acct.is_card else None,
        "routing": None if acct.is_card else acct.routing_number,
    }.get(kind)
    if not value:
        return Response("", status_code=404)
    return Response(str(value), media_type="text/plain")


@rt("/openbanking/open-account")
def openbanking_open_account(show: int = 1):
    """htmx partial: the "open an account" dialog, open or dismissed.

    Both states come from the same route so the close button is a swap back to
    the empty placeholder rather than a second endpoint.
    """
    return open_account_dialog(bool(show))


@app.get("/openbanking_all")
def get_all():
    """Used for rewards.

    Sorted by primary key on both sub-lists so the payload is byte-stable
    across calls -- see the module docstring on why drift here would corrupt
    every other app's reward.
    """
    payload = {
        "accounts": [account.__dict__ for account in account_rows()],
        "transactions": [
            txn.__dict__ for txn in sorted(transactions(), key=lambda t: t.id)
        ],
    }
    return Response(json.dumps(payload), headers={"Content-Type": "application/json"})


def get_openbanking_routes():
    return app.routes


if __name__ == "__main__":
    print("Warning: Running openbanking app in standalone mode")
    app.routes = get_openbanking_routes()
    serve()
