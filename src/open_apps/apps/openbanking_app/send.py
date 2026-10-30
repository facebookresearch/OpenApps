"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Sending money: a Zelle-style payment from a deposit account to a person.

The bank's second write path, and the first one the user starts. The card
charge (``main.authorize_card_purchase``) is the shop's; this one is theirs.
A payee is a *handle* -- an email address or a phone number, the way Zelle
addresses people -- and Contacts is the address book: the form searches it,
a contact's name pays their handle, and a payment to someone new can file
them there ("Save to Contacts").

A send posts immediately, as a dated row on the bank's pinned ``as_of_date``
in the pack's own date spelling, and lowers the account's available and
present balances together. Unlike a card charge it is not pending: the money
has left, and a ledger that showed it as pending would invite a second send.

Nothing is written until every check passes -- account, amount, funds, payee,
and the name for a new contact. A refused send leaves both apps exactly as
they were, so an agent that mistypes on the way to the right payment still
scores: the reward sees only the payment it finally made.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

from fasthtml.common import (
    A, Button, Dd, Div, Dl, Dt, Fieldset, Form, H2, Input, Label, Legend, Option, P, Select,
    Small, Span, Style,
)
from starlette.responses import RedirectResponse

from open_apps.apps.contacts_app import main as contacts_app
from open_apps.apps.openbanking_app import main as bank
from open_apps.apps.openbanking_app import statements
from open_apps.handles import format_handle, handle_kind, normalize_handle
from open_apps.icons import Icon, icon


@dataclass
class SendResult:
    """Outcome of :func:`send_payment`.

    ``errors`` maps a form field (``from``, ``to``, ``amount``, ``save_name``)
    to the message shown beside it; empty when the payment went through.
    """

    ok: bool
    errors: dict = field(default_factory=dict)
    txn: Optional[bank.Transaction] = None
    contact_id: Optional[int] = None


@dataclass
class Payee:
    handle: str
    name: str
    # Whether the handle already belongs to a contact. Decides whether the
    # review step offers "Save to Contacts".
    saved: bool


def contacts_available() -> bool:
    """Whether this run serves Contacts. Sending works either way; the
    recipient search and "Save to Contacts" need it."""
    return contacts_app.contacts is not None


def parse_amount(raw) -> Optional[float]:
    """Dollars as people type them -- "1850", "1,850", "$1,850.00" -- or
    ``None``. Rounded to cents; the sign is kept so the caller can refuse it."""
    text = re.sub(r"[\s$,]", "", str(raw or ""))
    if not re.fullmatch(r"-?(\d+(\.\d*)?|\.\d+)", text):
        return None
    return round(float(text), 2)


def _contact_by_name(text: str):
    """The one contact ``text`` names: an exact display-name match wins, and
    only when none matches do first names count -- the rule tasks resolve
    names by (``tasks._match_contact``), so "Bob" is Bob and not "Bob Smith"."""
    wanted = " ".join(text.split()).casefold()
    people = contacts_app.find_by_name(text)
    exact = [c for c in people if contacts_app.display_name(c).casefold() == wanted]
    matches = exact or people
    return matches[0] if len(matches) == 1 else None


def resolve_payee(raw) -> Optional[Payee]:
    """Who ``raw`` means: a handle in any spelling, or a contact's name.

    A name pays the contact's first email, else their first phone -- what a
    payment app does with a contact who has both.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    handle = normalize_handle(text)
    if handle is not None:
        if contacts_available():
            owner = contacts_app.contact_for_handle(handle)
            if owner is not None:
                return Payee(handle, contacts_app.display_name(owner), True)
        return Payee(handle, format_handle(handle), False)
    if not contacts_available():
        return None
    contact = _contact_by_name(text)
    if contact is None:
        return None
    own = contacts_app.handles_for(contact.id)
    pick = next((h for h in own if h.kind == "email"), None) or next(iter(own), None)
    if pick is None:
        return None
    return Payee(pick.value, contacts_app.display_name(contact), True)


def _deposit_account(from_id) -> Optional[bank.Account]:
    try:
        account = bank.get_account(int(from_id))
    except (TypeError, ValueError):
        return None
    return account if account is not None and not account.is_card else None


def _new_contact_values(save_as: dict, payee: Payee) -> dict:
    """``save_as`` plus the payee's handle, in ``contacts_app.parse_form``'s shape."""
    kind = handle_kind(payee.handle)
    entry = [("email" if kind == "email" else "mobile", payee.handle)]
    return {
        "first_name": str(save_as.get("first_name") or "").strip(),
        "last_name": str(save_as.get("last_name") or "").strip(),
        "company": "",
        "note": "",
        "favorite": False,
        "phones": entry if kind == "phone" else [],
        "emails": entry if kind == "email" else [],
    }


def check_payment(from_id, to, amount, save_as: Optional[dict] = None) -> tuple[dict, Optional[Payee], Optional[float]]:
    """Every check a send makes, with nothing written: ``(errors, payee, amount)``.

    The review step runs this too, so a payment that would be refused is
    refused on the form rather than after the user has confirmed it.
    """
    errors: dict = {}
    account = _deposit_account(from_id)
    if account is None:
        errors["from"] = bank._label("send_error_account", "Choose a checking or savings account.")

    value = parse_amount(amount)
    if value is None or value <= 0:
        errors["amount"] = bank._label("send_error_amount", "Enter an amount greater than $0.00.")
    elif account is not None and value > round(account.available_balance, 2):
        errors["amount"] = bank._label(
            "send_error_funds", "Insufficient funds: {account} has {available} available."
        ).format(account=account.name, available=bank.fmt_money(account.available_balance))

    payee = resolve_payee(to)
    if payee is None:
        errors["to"] = bank._label(
            "send_error_recipient", "Enter a contact's name, an email address or a US phone number."
        )
    elif save_as is not None and contacts_available():
        values = _new_contact_values(save_as, payee)
        if not (values["first_name"] or values["last_name"]):
            errors["save_name"] = bank._label("send_error_name", "Add a name to save this contact.")
        elif payee.saved:
            errors["save_name"] = bank._label(
                "send_error_saved", "{handle} is already saved as {name}."
            ).format(handle=format_handle(payee.handle), name=payee.name)
    return errors, payee, value


def send_payment(from_id, to, amount, memo: str = "", save_as: Optional[dict] = None) -> SendResult:
    """Send ``amount`` from deposit account ``from_id`` to ``to``.

    ``to`` is a handle in any spelling or a contact's name. ``save_as``
    (``{"first_name", "last_name"}``) files a new payee in Contacts in the same
    step; it is ignored when the run has no Contacts.
    """
    errors, payee, value = check_payment(from_id, to, amount, save_as)
    if errors:
        return SendResult(False, errors)

    account = _deposit_account(from_id)
    contact_id = None
    name = payee.name
    if save_as is not None and contacts_available():
        person, contact_errors = contacts_app.add_contact(_new_contact_values(save_as, payee))
        if contact_errors:  # check_payment ran the same rules; this is a race, not a typo
            return SendResult(False, {"save_name": contact_errors.get("_form", "")})
        contact_id = person.id
        name = contacts_app.display_name(person)

    c = bank.cfg()
    balance = round(account.present_balance - value, 2)
    row = bank.Transaction(
        id=None,
        account_id=account.id,
        position=0,
        date=statements.format_posting_date(
            bank.as_of_date(), str(getattr(c, "posting_date_style", None) or "en")
        ),
        description=bank._label("send_description", "Zelle to {name}").format(name=name),
        type=bank._label("send_type", "Zelle"),
        amount=-value,
        balance=balance,
        counterparty=payee.handle,
        memo=str(memo or "").strip() or None,
    )
    bank._post(account.id, [row])
    account.present_balance = balance
    account.available_balance = round(account.available_balance - value, 2)
    bank.accounts.update(account)
    return SendResult(True, {}, row, contact_id)


# ---------------------------------------------------------------------------
# Pages.
#
# Form -> review -> confirm, as a bank does it. Both steps a person looks at
# are GETs: the window chrome is spliced into full-page GETs only, and the
# review writes nothing, so it is safe to reload or deep-link. The one write,
# Confirm, is a POST that answers with a 303 -- to the account on success, or
# back to the review URL on a refusal, where the same checks run again and
# put the reason beside the field it is about.

L = bank._label
REVIEW_URL = "/openbanking/send/review"

send_styles = Style("""
    .ob-send { max-width: 34rem; }
    .ob-send label { font-weight: 600; margin-bottom: 0.3rem; }
    .ob-send-field { margin-bottom: 1.1rem; position: relative; }
    .ob-send-field input, .ob-send-field select { margin-bottom: 0; }
    .ob-send-error { display: block; color: var(--color-danger); margin-top: 0.3rem; }
    .ob-send-matches:empty { display: none; }
    .ob-send-matches {
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        margin-top: 0.3rem;
        overflow: hidden;
    }
    .ob-send-match {
        display: flex;
        justify-content: space-between;
        gap: 1rem;
        width: 100%;
        margin: 0;
        padding: 0.55rem 0.8rem;
        border: 0;
        border-bottom: 1px solid var(--color-border);
        border-radius: 0;
        background: var(--color-bg);
        color: var(--color-fg);
        text-align: left;
        font-weight: 600;
    }
    .ob-send-match:last-child { border-bottom: 0; }
    .ob-send-match:hover, .ob-send-match:focus-visible { background: var(--color-surface); }
    .ob-send-match-handle { color: var(--color-muted); font-weight: 400; }
    .ob-send-none { padding: 0.55rem 0.8rem; color: var(--color-muted); margin: 0; }
    .ob-send-actions { display: flex; align-items: center; gap: 1.25rem; margin-top: 1.5rem; }
    .ob-send-actions button { width: auto; margin: 0; }
    .ob-send-summary { display: grid; grid-template-columns: max-content 1fr; gap: 0.6rem 1.5rem; margin: 0 0 1.25rem 0; }
    .ob-send-summary dt { color: var(--color-muted); font-weight: 400; }
    .ob-send-summary dd { margin: 0; font-weight: 600; }
    .ob-send-amount { font-size: 1.75rem; }
    .ob-send-handle { display: block; color: var(--color-muted); font-weight: 400; }
    /* The rule is the fieldset's own top border with the legend dropped below
       it: a legend sits *in* the border, which drew the line through it. */
    .ob-send-save { border: 0; border-top: 1px solid var(--color-border); padding: 1rem 0 0 0; margin: 0; }
    .ob-send-save legend { float: left; width: 100%; padding: 0; margin-bottom: 0.75rem; font-weight: 600; }
    .ob-send-save legend + * { clear: both; }
    .ob-send-names { display: flex; gap: 0.75rem; }
    .ob-send-names > div { flex: 1 1 0; }
""")

# Fills the recipient field from a search match and closes the list. Inline
# and tiny on purpose: the match list is an htmx partial, and a click on a
# match must not navigate or post.
_PICK = (
    "var f=document.getElementById('ob-send-to');f.value=this.dataset.handle;"
    "document.getElementById('ob-send-matches').innerHTML='';"
    "document.getElementById('ob-send-amount').focus();"
)


def _deposit_accounts() -> list:
    return [a for a in bank.account_rows() if not a.is_card]


def _field(name: str, label: str, control, errors: dict, *extra):
    message = errors.get(name)
    return Div(
        Label(label, fr=f"ob-send-{name}"),
        control,
        *extra,
        Small(message, cls="ob-send-error", id=f"ob-send-{name}-error") if message else None,
        cls="ob-send-field",
    )


def _input(name: str, value, errors: dict, **attrs):
    if errors.get(name):
        attrs.update(aria_invalid="true", aria_describedby=f"ob-send-{name}-error")
    return Input(name=name, id=f"ob-send-{name}", value=str(value or ""), **attrs)


def _query(values: dict) -> str:
    return urlencode({k: values.get(k, "") for k in ("from", "to", "amount", "memo")})


def _account_link(from_id) -> str:
    account = _deposit_account(from_id)
    return f"/openbanking/accounts/{account.id}" if account else "/openbanking"


def send_form(values: dict, errors: dict | None = None):
    errors = errors or {}
    from_id = str(values.get("from", ""))
    to_attrs = {}
    if contacts_available():
        to_attrs = dict(
            hx_get="/openbanking/send/recipients",
            hx_trigger="input changed delay:200ms, search",
            hx_target="#ob-send-matches",
            hx_swap="innerHTML",
        )
    return Div(
        Form(
            H2(L("send_heading", "Send money"), cls="ob-band-heading"),
            _field(
                "from",
                L("send_from_label", "From"),
                Select(
                    *[
                        Option(a.name, value=str(a.id), selected=str(a.id) == from_id)
                        for a in _deposit_accounts()
                    ],
                    name="from",
                    id="ob-send-from",
                ),
                errors,
            ),
            _field(
                "to",
                L("send_to_label", "To"),
                _input(
                    "to", values.get("to"), errors, type="search", autocomplete="off",
                    placeholder=L("send_to_placeholder", "Name, email or mobile number"),
                    **to_attrs,
                ),
                errors,
                Div(id="ob-send-matches", cls="ob-send-matches", role="listbox"),
            ),
            _field(
                "amount",
                L("send_amount_label", "Amount"),
                _input("amount", values.get("amount"), errors, inputmode="decimal", placeholder="0.00"),
                errors,
            ),
            _field(
                "memo",
                L("send_memo_label", "Memo (optional)"),
                _input("memo", values.get("memo"), errors, maxlength="140"),
                errors,
            ),
            Div(
                Button(L("send_review_button", "Review"), type="submit"),
                A(L("send_cancel_label", "Cancel"), href=_account_link(from_id)),
                cls="ob-send-actions",
            ),
            method="get",
            action=REVIEW_URL,
            id="ob-send-form",
        ),
        cls="ob-card ob-send",
    )


def recipient_matches(query: str):
    """The search list under the recipient field: one row per handle of each
    contact whose name or handle matches, emails first."""
    q = str(query or "").strip()
    if not q or not contacts_available():
        return ""
    rows = []
    for contact in contacts_app.all_contacts():
        if not contacts_app.matches(contact, q):
            continue
        name = contacts_app.display_name(contact)
        own = sorted(contacts_app.handles_for(contact.id), key=lambda h: h.kind != "email")
        for h in own:
            rows.append(
                Button(
                    Span(name),
                    Span(format_handle(h.value), cls="ob-send-match-handle"),
                    type="button",
                    cls="ob-send-match",
                    role="option",
                    data_handle=h.value,
                    onclick=_PICK,
                )
            )
    if not rows:
        return P(
            L("send_no_matches", "No contacts match. Enter an email address or mobile number."),
            cls="ob-send-none",
        )
    return tuple(rows)


def review_card(values: dict, payee: Payee, amount: float, errors: dict):
    account = _deposit_account(values.get("from"))
    memo = str(values.get("memo") or "").strip()
    hidden = [
        Input(type="hidden", name="from", value=str(account.id)),
        Input(type="hidden", name="to", value=payee.handle),
        Input(type="hidden", name="amount", value=f"{amount:.2f}"),
        Input(type="hidden", name="memo", value=memo),
    ]
    save = None
    if not payee.saved and contacts_available():
        save = Fieldset(
            Legend(L("send_new_payee_note", "{handle} isn't in your contacts.").format(
                handle=format_handle(payee.handle))),
            Label(
                Input(type="checkbox", name="save", value="1", id="ob-send-save",
                      checked=bool(values.get("save"))),
                L("send_save_label", "Save to Contacts"),
            ),
            Div(
                _field("first_name", L("send_first_name_label", "First name"),
                       _input("first_name", values.get("first_name"), errors, autocomplete="off"), {}),
                _field("last_name", L("send_last_name_label", "Last name"),
                       _input("last_name", values.get("last_name"), errors, autocomplete="off"), {}),
                cls="ob-send-names",
            ),
            Small(errors["save_name"], cls="ob-send-error", id="ob-send-save_name-error")
            if errors.get("save_name") else None,
            cls="ob-send-save",
        )
    summary = [
        Dt(L("send_to_label", "To")),
        Dd(payee.name, Span(format_handle(payee.handle), cls="ob-send-handle")
           if payee.name != format_handle(payee.handle) else None),
        Dt(L("send_amount_label", "Amount")),
        Dd(bank.fmt_money(amount), cls="ob-send-amount"),
        Dt(L("send_from_label", "From")),
        Dd(account.name),
    ]
    if memo:
        summary += [Dt(L("send_memo_summary_label", "Memo")), Dd(memo)]
    return Div(
        Form(
            H2(L("send_review_heading", "Review payment"), cls="ob-band-heading"),
            Dl(*summary, cls="ob-send-summary"),
            *hidden,
            save,
            Div(
                Button(L("send_confirm_button", "Send {amount}").format(amount=bank.fmt_money(amount)),
                       type="submit"),
                A(L("send_edit_label", "Edit"), href=f"/openbanking/send?{_query(values)}"),
                cls="ob-send-actions",
            ),
            method="post",
            action="/openbanking/send/confirm",
            id="ob-send-review",
        ),
        cls="ob-card ob-send",
    )


def _page(card):
    return bank.page(send_styles, Div(card, cls="ob-band"))


def sent_notice(account: bank.Account, txn_id: int):
    """"Sent $X to Name" over the account a payment just left, or nothing when
    ``txn_id`` is not a payment from this account."""
    row = next(
        (t for t in bank.txns_for(account.id) if t.id == txn_id and t.counterparty), None
    )
    if row is None:
        return None
    if contacts_available():
        name = contacts_app.contact_name(row.counterparty)
    else:
        name = format_handle(row.counterparty)
    return Div(
        Span(icon(Icon.CHECK, size=16), cls="ob-toast-icon"),
        Span(L("send_sent_message", "Sent {amount} to {name}").format(
            amount=bank.fmt_money(-row.amount), name=name)),
        cls="ob-toast",
        role="status",
        id="ob-sent-toast",
    )


def send_link(account: bank.Account):
    return A(
        L("send_link_label", "Send money"),
        href=f"/openbanking/send?from={account.id}",
        cls="ob-action ob-send-link",
        id="ob-send-link",
    )


def _save_as(values: dict) -> Optional[dict]:
    if not values.get("save"):
        return None
    return {"first_name": values.get("first_name", ""), "last_name": values.get("last_name", "")}


@bank.rt("/openbanking/send")
def send_page(req):
    values = dict(req.query_params)
    values.setdefault("from", str(_deposit_accounts()[0].id) if _deposit_accounts() else "")
    return _page(send_form(values))


@bank.rt("/openbanking/send/recipients")
def send_recipients(to: str = ""):
    return recipient_matches(to)


@bank.rt(REVIEW_URL)
def send_review(req):
    values = dict(req.query_params)
    errors, payee, amount = check_payment(values.get("from"), values.get("to"),
                                          values.get("amount"), _save_as(values))
    # A name problem belongs on the review, where the name is typed; anything
    # else means the payment itself has to change, which is the form's job.
    if set(errors) - {"save_name"}:
        return _page(send_form(values, errors))
    return _page(review_card(values, payee, amount, errors))


@bank.app.post("/openbanking/send/confirm")
async def send_confirm(req):
    values = {k: str(v) for k, v in (await req.form()).items()}
    result = send_payment(values.get("from"), values.get("to"), values.get("amount"),
                          values.get("memo", ""), _save_as(values))
    if not result.ok:
        params = {k: values.get(k, "") for k in
                  ("from", "to", "amount", "memo", "save", "first_name", "last_name")}
        return RedirectResponse(f"{REVIEW_URL}?{urlencode(params)}", status_code=303)
    return RedirectResponse(
        f"/openbanking/accounts/{result.txn.account_id}?sent={result.txn.id}", status_code=303
    )
