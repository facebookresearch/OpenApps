"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The Contacts app: the address book every other app resolves names through.

Names live here and nowhere else. The messenger keys its threads by handle and
asks :func:`contact_name` what to draw, so saving an unknown number as a
contact renames its thread, and renaming a contact renames it everywhere,
without either touching the messenger's own state. Payments and password
sharing pick recipients from the same entries.

Two tables rather than one: a person has any number of phones and emails, and
the lookup that matters most -- which contact owns this handle? -- is a query
on the handles alone. Every handle is stored normalised (``open_apps.handles``)
and may belong to one contact only.

The scoring surface is ``/contacts_all``: every contact with its handles,
sorted by id, byte-stable across reads like every other app's ``/<app>_all``.
"""
import json
from dataclasses import dataclass
from typing import Optional

from fasthtml.common import *

from open_apps.frontend import local_hdrs
from open_apps.handles import format_handle, normalize_handle


@dataclass
class Contact:
    id: int
    first_name: str
    last_name: str
    company: str
    note: str
    favorite: bool


@dataclass
class ContactHandle:
    id: int
    contact_id: int
    kind: str  # phone | email
    label: str  # mobile | work | home | other
    value: str  # normalised


app, rt = fast_app(default_hdrs=False, hdrs=local_hdrs())

db = None
contacts = None
handles = None


def _plain(node):
    """An OmegaConf node (or None) as plain Python containers."""
    try:
        from omegaconf import OmegaConf

        if OmegaConf.is_config(node):
            return OmegaConf.to_container(node, resolve=True)
    except ImportError:  # pragma: no cover - omegaconf is a hard dependency
        pass
    return node


def set_environment(config):
    """Create the tables and re-seed them from the Hydra config.

    Called again on reset, so the tables are cleared first rather than relying
    on the caller having dropped them.
    """
    global db, contacts, handles
    app.config = config
    db = database(config.contacts.database_path)
    contacts = db.create(Contact, name="contacts", pk="id")
    handles = db.create(ContactHandle, name="contact_handles", pk="id")
    for table in (handles, contacts):
        table.delete_where()
    _seed(_plain(getattr(config.contacts, "contacts", None)) or [])


def _seed(entries: list[dict]) -> None:
    owners: dict[str, str] = {}
    for raw in entries:
        person = contacts.insert(
            Contact(
                id=None,
                first_name=str(raw.get("first_name") or ""),
                last_name=str(raw.get("last_name") or ""),
                company=str(raw.get("company") or ""),
                note=str(raw.get("note") or ""),
                favorite=bool(raw.get("favorite", False)),
            )
        )
        for kind, key in (("phone", "phones"), ("email", "emails")):
            for entry in raw.get(key) or []:
                value = normalize_handle(entry.get("value"))
                if value is None:
                    raise ValueError(f"contacts seed: {entry.get('value')!r} is not a valid {kind}")
                # Fail the launch rather than merge two people: which contact a
                # handle resolves to decides what name a thread shows and who a
                # payment goes to.
                if value in owners:
                    raise ValueError(
                        f"contacts seed: {value} belongs to both {owners[value]!r} and "
                        f"{display_name(person)!r}"
                    )
                owners[value] = display_name(person)
                handles.insert(
                    ContactHandle(
                        id=None,
                        contact_id=person.id,
                        kind=kind,
                        label=str(entry.get("label") or "other"),
                        value=value,
                    )
                )


# ---------------------------------------------------------------------------
# Lookups other apps use.


def display_name(contact: Contact) -> str:
    """"First Last", else the company, else the contact's first handle."""
    full = " ".join(part for part in (contact.first_name, contact.last_name) if part)
    if full:
        return full
    if contact.company:
        return contact.company
    first = handles_for(contact.id)
    return format_handle(first[0].value) if first else ""


def all_contacts() -> list[Contact]:
    return sorted(contacts(), key=lambda c: c.id)


def handles_for(contact_id: int) -> list[ContactHandle]:
    return sorted(
        (h for h in handles() if h.contact_id == contact_id), key=lambda h: h.id
    )


def get_contact(contact_id: int) -> Optional[Contact]:
    return next((c for c in contacts() if c.id == contact_id), None)


def contact_for_handle(raw: str) -> Optional[Contact]:
    """The contact who owns ``raw``, in any spelling, or ``None``."""
    value = normalize_handle(raw)
    if value is None:
        return None
    owner = next((h for h in handles() if h.value == value), None)
    return get_contact(owner.contact_id) if owner else None


def contact_name(raw: str) -> str:
    """What to call whoever is at ``raw``: their contact name if saved, the
    formatted handle if not -- the way a phone labels an unknown number."""
    contact = contact_for_handle(raw)
    if contact is not None:
        return display_name(contact)
    value = normalize_handle(raw)
    return format_handle(value) if value else str(raw)


def find_by_name(query: str) -> list[Contact]:
    """Contacts whose display name or first name matches ``query``,
    case-insensitively. Several matches is a real outcome -- callers that need
    exactly one (task recipient resolution) must check."""
    wanted = " ".join(str(query).split()).casefold()
    if not wanted:
        return []
    return [
        c
        for c in all_contacts()
        if wanted in (display_name(c).casefold(), c.first_name.casefold())
    ]


# ---------------------------------------------------------------------------
# Scoring surface.


def contacts_payload() -> dict:
    out = []
    for contact in all_contacts():
        own = handles_for(contact.id)
        out.append(
            {
                "id": contact.id,
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "company": contact.company,
                "note": contact.note,
                "favorite": bool(contact.favorite),
                "phones": [{"label": h.label, "value": h.value} for h in own if h.kind == "phone"],
                "emails": [{"label": h.label, "value": h.value} for h in own if h.kind == "email"],
            }
        )
    return {"contacts": out}


@app.get("/contacts_all")
def get_all():
    """Used for rewards."""
    return Response(json.dumps(contacts_payload()), headers={"Content-Type": "application/json"})


def get_contacts_routes():
    return app.routes
