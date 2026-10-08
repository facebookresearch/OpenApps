"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Retail online-banking surface: an account index and a per-account transaction
ledger, modelled on a real bank dashboard.

Deliberately read-only. Nothing an agent can click mutates the seeded state,
so ``/openbanking_all`` is byte-identical at episode start and end. That is
load-bearing: ``AppStateComparison`` builds every task's target by deep-copying
the *initial* cross-app state, so an app that drifted on its own (auto
timestamps, nondeterministic row order) would inject a spurious diff into every
unrelated todo/calendar task's reward. Scorable work therefore lands in another
app -- the bank supplies the information-retrieval half of a ``CompositeTask``
and the delta shows up in todo/calendar/messenger.
"""

from fasthtml.common import *
from dataclasses import dataclass
import json
from typing import List, Optional
from urllib.parse import urlencode
from src.open_apps.apps.start_page.helper import create_logo_header
from src.open_apps.frontend import local_hdrs
from src.open_apps.theme import theme_asset, theme_style


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
# The banking-specific tokens (--color-header-bg and friends) are only defined
# by the `openbanking*` themes, so every use here carries a fallback to a token
# from the shared 15-token contract. That keeps the app legible under `default`,
# `dark`, `mono`, `challenging_font`, `solarized`, `material` and `bootstrap`
# instead of collapsing to unstyled text.
styles = Style("""
    body {
        font-family: var(--font-family);
        font-size: var(--font-size-base);
        color: var(--color-fg);
        background-color: var(--color-bg);
        margin: 0;
    }
    a { color: var(--color-link, var(--color-accent)); text-decoration: none; }
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
        background-color: var(--color-header-bg, var(--color-primary));
        color: var(--color-header-fg, var(--color-on-primary));
        padding: 0.75rem 1.5rem;
    }
    /* create_logo_header sets `margin-bottom: 1rem` inline for the standalone
       page-header case; inside the masthead bar that leaves a gap under the
       wordmark. A stylesheet `!important` outranks a non-important inline
       declaration, so this wins without forking the shared helper. */
    .ob-masthead > div, .ob-masthead > a {
        margin-bottom: 0 !important;
        color: var(--color-header-fg, var(--color-on-primary)) !important;
    }
    /* The wordmark is an `H1` inside that container, and Pico colours every
       heading explicitly, so it does not inherit the masthead foreground --
       it was rendering in body-text colour on the navy bar. It sits on the
       header background, not the page background, so it takes the header
       token rather than `--color-fg` like the headings in the bridge above. */
    .ob-masthead h1 {
        color: var(--color-header-fg, var(--color-on-primary));
        margin-bottom: 0;
    }
    .ob-masthead-spacer { flex: 1 1 auto; }
    .ob-masthead-actions { display: flex; align-items: center; gap: 1rem; }
    /* Fill and text are the masthead's own pair, inverted -- not `--color-bg`
       over `--color-header-bg`, which happens to read as white-on-navy in the
       light theme purely because the page is white there, and collapses to
       near-black on near-black under a dark one. */
    .ob-ghost-btn {
        border: 1px solid var(--color-header-fg, var(--color-on-primary));
        border-radius: var(--radius);
        padding: 0.4rem 0.9rem;
        font-weight: 600;
        background-color: var(--color-header-fg, var(--color-on-primary));
        color: var(--color-header-bg, var(--color-primary));
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
        color: var(--color-link, var(--color-accent));
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
        color: var(--color-link, var(--color-accent));
        border-bottom: 1px dotted var(--color-link, var(--color-accent));
        border-radius: 0;
        cursor: pointer;
    }
    .ob-number-toggle:hover { text-decoration: none; opacity: 0.8; }
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
        border-bottom: 2px solid var(--color-rule, var(--color-fg));
        padding: 0.5rem 0.75rem;
        font-weight: 600;
    }
    .ob-table td {
        padding: 0.75rem;
        border-bottom: 1px solid var(--color-border);
        vertical-align: top;
    }
    .ob-num { text-align: right; white-space: nowrap; }
    .ob-credit { color: var(--color-credit, var(--color-accent)); font-weight: 600; }
    .ob-debit { color: var(--color-debit, var(--color-fg)); font-weight: 700; }
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
        background-color: var(--color-header-bg, var(--color-primary));
        color: var(--color-header-fg, var(--color-on-primary));
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
        border: 1px solid var(--color-header-fg, var(--color-on-primary));
        border-radius: var(--radius);
        padding: 0.15rem 0.5rem;
        margin: 0;
        width: auto;
        font-family: inherit;
        font-size: 0.75rem;
        font-weight: 600;
        line-height: 1.4;
        color: var(--color-header-fg, var(--color-on-primary));
        cursor: pointer;
    }
    .ob-cardface-toggle:hover { opacity: 0.75; }
    .ob-cardface-spacer { flex: 1 1 auto; }
    .ob-cardface-number {
        font-family: var(--font-mono);
        font-size: 1.15rem;
        letter-spacing: 0.12em;
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
    .ob-empty { color: var(--color-muted); padding: 1rem 0; }
""")


def set_environment(config):
    """Set environment variables for the openbanking app"""
    global app, logo_title_container, accounts, transactions
    app.config = config
    db = database(config.openbanking.database_path)
    accounts = db.create(Account, pk="id")
    transactions = db.create(Transaction, pk="id")

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
        id=f"ob-number-{kind}",
        cls="ob-number-row",
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
            group_card_number(account.account_number, revealed),
            cls="ob-cardface-number",
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
    rows = [
        Div(
            Div(
                Div(account.name, style="font-weight: 600;"),
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
            hx_get=f"/openbanking/accounts/{account.id}",
            hx_push_url="true",
            hx_target="body",
            style="cursor: pointer;",
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
