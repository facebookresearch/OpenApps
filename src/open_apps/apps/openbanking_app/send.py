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

from open_apps.apps.contacts_app import main as contacts_app
from open_apps.apps.openbanking_app import main as bank
from open_apps.apps.openbanking_app import statements
from open_apps.handles import format_handle, handle_kind, normalize_handle


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

