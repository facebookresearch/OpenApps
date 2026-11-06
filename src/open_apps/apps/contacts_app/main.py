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
from urllib.parse import quote

from fasthtml.common import *

from open_apps import device
from open_apps.app_icons import app_glyph
from open_apps.apps.start_page.helper import create_logo_header
from open_apps.frontend import local_hdrs
from open_apps.handles import format_handle, handle_kind, normalize_handle
from open_apps.theme import theme_asset, theme_style
from open_apps.ui.copy import COPY_SCRIPT, CopyButton, copy_styles


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
logo_title_container = None


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
    global db, contacts, handles, logo_title_container
    app.config = config
    db = database(config.contacts.database_path)
    contacts = db.create(Contact, name="contacts", pk="id")
    handles = db.create(ContactHandle, name="contact_handles", pk="id")
    for table in (handles, contacts):
        table.delete_where()
    _seed(_plain(getattr(config.contacts, "contacts", None)) or [])
    start_page_apps = getattr(getattr(config, "start_page", None), "apps", None)
    if start_page_apps is not None and "contacts" in start_page_apps:
        logo_title_container = create_logo_header(
            app_config=start_page_apps.contacts,
            base_url="/contacts",
            current_file_path=__file__,
        )


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


# ---------------------------------------------------------------------------
# Interface.
#
# Modelled on the macOS / iOS address book: a list beside the selected card,
# and on the card a column of quiet lowercase labels ("mobile", "home") beside
# their values. That label column is the one distinctive move; everything else
# is hairlines and the shared theme tokens, so `dark` and `mono` restyle it
# like every other app and no colour here is the app's own.
#
# Navigation is plain links and form posts -- full page loads -- never an htmx
# swap into <body>: the window chrome is spliced in only on full-page GETs, and
# OpenBanking lost its title bar and dock exactly that way. htmx is used only
# for in-place pieces that live inside the page: the live search results and
# an extra phone/email row in the edit form.


def cfg():
    """The app's own config node."""
    return app.config.contacts


def current_layout() -> str:
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return str(getattr(config.contacts, "layout", "default"))


def single_pane() -> bool:
    """One pane at a time: the ``list_detail`` layout, and always on a phone,
    where a list beside a card does not fit in 390px."""
    return current_layout() == "list_detail" or device.form_factor(app.config) == "phone"


def contacts_theme():
    """The active theme's token block, resolved per request so a live theme
    swap takes effect."""
    return theme_style(app.config, "contacts")


def pico_theme() -> str:
    """Pico's palette polarity for the active theme (see OpenBanking)."""
    tone = theme_asset(app.config, "contacts", "tone", "light")
    return "dark" if str(tone) == "dark" else "light"


def sort_name(contact: Contact) -> str:
    return display_name(contact).casefold()


def section_letter(contact: Contact) -> str:
    first = sort_name(contact)[:1].upper()
    return first if first.isalpha() and first.isascii() else "#"


def initials(contact: Contact) -> str:
    """Two letters for the avatar: first + last name, else the first two words
    of the company, else the first character of whatever is shown."""
    if contact.first_name or contact.last_name:
        parts = [contact.first_name, contact.last_name]
    elif contact.company:
        parts = contact.company.split()[:2]
    else:
        parts = [display_name(contact)]
    letters = "".join(p[:1] for p in parts if p and p[:1].isalnum())
    return letters.upper()[:2] or "?"


def primary_message_handle(contact: Contact) -> Optional[str]:
    """Where "Message" goes: the first phone, else the first email, that has a
    conversation -- the rule ``tasks.resolve_thread`` scores by. The messenger
    only opens existing threads, so a handle without one would land on its 404;
    ``None`` disables the button instead."""
    # Imported here: the messenger imports this module for names.
    from open_apps.apps.messenger_app.main import _get_thread

    own = handles_for(contact.id)
    for kind in ("phone", "email"):
        for h in own:
            if h.kind == kind and _get_thread(h.value) is not None:
                return h.value
    return None


def matches(contact: Contact, query: str) -> bool:
    """Search over names, company, note and every handle. A query that is all
    digits also matches phone numbers digit-for-digit, so "0100002" finds Bob
    however the number was typed."""
    q = " ".join(query.split()).casefold()
    if not q:
        return True
    haystack = [display_name(contact), contact.first_name, contact.last_name, contact.company]
    if any(q in field.casefold() for field in haystack if field):
        return True
    digits = "".join(ch for ch in q if ch.isdigit())
    for h in handles_for(contact.id):
        if q in h.value or q in format_handle(h.value).casefold():
            return True
        if digits and len(digits) >= 3 and digits in "".join(ch for ch in h.value if ch.isdigit()):
            return True
    return False


# --- Small parts ------------------------------------------------------------

_ENVELOPE_SVG = NotStr(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="11 11 42 42" width="22" height="22" '
    'fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true" focusable="false">'
    '<rect x="14" y="19" width="36" height="26" rx="5"/><path d="M15.5 21.5 32 34l16.5-12.5"/></svg>'
)
_PLUS_SVG = NotStr(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14"/></svg>'
)
_BACK_SVG = NotStr(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m14.5 5.5-6.5 6.5 6.5 6.5"/></svg>'
)


def avatar(contact: Contact, size: str = "lg"):
    return Div(initials(contact), cls=f"ct-avatar is-{size}", aria_hidden="true")


def new_contact_link():
    c = cfg()
    return A(
        _PLUS_SVG, Span(c.new_contact_label),
        href="/contacts/new", cls="ct-btn is-primary", data_testid="new-contact",
    )


def back_link():
    return A(_BACK_SVG, Span(cfg().back_label), href="/contacts", cls="ct-back", data_testid="back-to-contacts")


# --- List -------------------------------------------------------------------


def contact_row(contact: Contact, selected_id: Optional[int], prefix: str):
    """One list row. A real anchor, so it is a link in the accessibility tree
    and a full navigation when followed. Favorites appear twice (their own
    section and their letter), so the id is namespaced by section."""
    name = display_name(contact)
    secondary = contact.company if contact.company and contact.company != name else None
    return A(
        Span(name, cls="ct-row-name"),
        Span(secondary, cls="ct-row-sub") if secondary else None,
        href=f"/contacts/{contact.id}",
        cls="ct-row",
        id=f"{prefix}-{contact.id}",
        data_testid=f"{prefix}-{contact.id}",
        aria_current="page" if contact.id == selected_id else None,
    )


def contact_list(query: str = "", selected_id: Optional[int] = None):
    """The swappable list: favorites, then A-Z sections, then the index strip.
    Searching drops the favorites and the strip -- results are short."""
    c = cfg()
    people = sorted((p for p in all_contacts() if matches(p, query)), key=sort_name)
    if not people:
        message = c.no_results.format(query=query) if query.strip() else c.no_contacts
        return Div(P(message, cls="ct-empty"), id="ct-list", cls="ct-list")

    blocks = []
    favorites = [p for p in people if p.favorite]
    if favorites and not query.strip():
        blocks.append(
            Section(
                H3(c.favorites_heading, cls="ct-section-heading"),
                *[contact_row(p, selected_id, "favorite-row") for p in favorites],
                cls="ct-section is-favorites",
                aria_label=c.favorites_heading,
            )
        )
    letters: list[str] = []
    for person in people:
        letter = section_letter(person)
        if letter not in letters:
            letters.append(letter)
    for letter in letters:
        blocks.append(
            Section(
                H3(letter, cls="ct-section-heading"),
                *[contact_row(p, selected_id, "contact-row") for p in people if section_letter(p) == letter],
                cls="ct-section",
                id=f"ct-section-{letter}",
                aria_label=letter,
            )
        )
    index = None
    if not query.strip():
        # Every letter is shown, as on a phone, but only the ones with people
        # under them are links: a dead link is a step an agent wastes.
        index = Nav(
            *[
                A(letter, href=f"#ct-section-{letter}") if letter in letters
                else Span(letter, aria_hidden="true")
                for letter in [*"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "#"]
            ],
            cls="ct-index",
            aria_label=c.index_label,
        )
    return Div(Div(*blocks, cls="ct-sections"), index, id="ct-list", cls="ct-list")


def list_pane(query: str = "", selected_id: Optional[int] = None):
    c = cfg()
    return Div(
        Form(
            Input(
                type="search",
                name="q",
                value=query,
                id="ct-search",
                placeholder=c.search_placeholder,
                aria_label=c.search_label,
                autocomplete="off",
                hx_get="/contacts/search",
                hx_trigger="input changed delay:200ms, search",
                hx_target="#ct-list",
                hx_swap="outerHTML",
                hx_include="#ct-selected",
            ),
            method="get",
            action="/contacts",
            role="search",
            cls="ct-search",
        ),
        # Outside the form: Pico styles a role=search form as an input group,
        # and a trailing hidden input would square off the field's right end.
        Input(type="hidden", name="selected", value=str(selected_id or ""), id="ct-selected"),
        contact_list(query, selected_id),
        cls="ct-listpane",
        aria_label=c.list_label,
    )


# --- Detail -----------------------------------------------------------------


def field_row(contact: Contact, h: ContactHandle):
    c = cfg()
    label = c.handle_labels.get(h.label, h.label) if c.get("handle_labels") else h.label
    return Div(
        Span(label, cls="ct-field-label"),
        Div(
            Span(format_handle(h.value), cls="ct-field-value"),
            CopyButton(
                f"{label} {h.kind}",
                url=f"/contacts/{contact.id}/copy/{h.id}",
                copy_label=c.copy_label,
                copied_label=c.copied_label,
                failed_label=c.copy_failed_label,
                prefix="ct-copy",
                testid=f"copy-handle-{h.id}",
            ),
            cls="ct-field-body",
        ),
        cls="ct-field",
        id=f"ct-field-{h.id}",
    )


def action_button(glyph, label: str, href: Optional[str] = None, hint: Optional[str] = None, testid: str = ""):
    """A round action with its caption under it. Unavailable actions are real,
    disabled buttons inside a titled wrapper, so the reason shows on hover
    (a disabled button fires no hover events of its own) and reaches the
    accessibility tree through aria-describedby."""
    body = (Span(glyph, cls="ct-action-icon"), Span(label, cls="ct-action-label"))
    if href:
        return A(*body, href=href, cls="ct-action", data_testid=testid)
    hint_id = f"{testid}-hint"
    return Span(
        Button(*body, type="button", disabled=True, cls="ct-action", data_testid=testid,
               aria_describedby=hint_id),
        Span(hint or "", id=hint_id, cls="sr-only"),
        title=hint,
        cls="ct-action-wrap",
    )


def detail_card(contact: Contact):
    c = cfg()
    name = display_name(contact)
    own = handles_for(contact.id)
    phones = [h for h in own if h.kind == "phone"]
    emails = [h for h in own if h.kind == "email"]
    target = primary_message_handle(contact)
    actions = Div(
        action_button(
            app_glyph("messages", size=22), c.message_label,
            href=f"/messages/{quote(target, safe='')}/" if target else None,
            hint=None if target else c.no_conversation_hint, testid="action-message",
        ),
        action_button(_ENVELOPE_SVG, c.email_action_label, hint=c.coming_soon, testid="action-email"),
        action_button(app_glyph("wallet", size=22), c.send_money_label, hint=c.coming_soon, testid="action-send-money"),
        cls="ct-actions",
    )
    groups = []
    if phones:
        groups.append(Div(*[field_row(contact, h) for h in phones], cls="ct-group", data_kind="phone"))
    if emails:
        groups.append(Div(*[field_row(contact, h) for h in emails], cls="ct-group", data_kind="email"))
    if contact.note:
        groups.append(
            Div(
                Div(Span(c.note_label, cls="ct-field-label"), Div(P(contact.note, cls="ct-note"), cls="ct-field-body"),
                    cls="ct-field"),
                cls="ct-group", data_kind="note",
            )
        )
    return Article(
        back_link() if single_pane() else None,
        Header(
            avatar(contact),
            H2(name, cls="ct-name", data_testid="contact-name"),
            P(contact.company, cls="ct-company") if contact.company and contact.company != name else None,
            cls="ct-hero",
        ),
        actions,
        Div(*groups, cls="ct-groups"),
        Footer(
            A(c.edit_label, href=f"/contacts/{contact.id}/edit", cls="ct-btn", data_testid="edit-contact"),
            cls="ct-detail-foot",
        ),
        cls="ct-detail",
        id="ct-detail",
        data_contact_id=str(contact.id),
    )


# --- Form -------------------------------------------------------------------


def label_select(kind: str, index: int, chosen: str):
    c = cfg()
    names = c.handle_labels
    options = ["mobile", "work", "home", "other"] if kind == "phone" else ["home", "work", "other"]
    return Select(
        *[Option(names.get(o, o), value=o, selected=(o == chosen)) for o in options],
        name=f"{kind}_label_{index}",
        aria_label=f"{c.phone_label if kind == 'phone' else c.email_label} {index + 1}",
        cls="ct-label-select",
    )


def handle_input_row(kind: str, index: int, label: str = "", value: str = "", error: str = ""):
    c = cfg()
    default_label = "mobile" if kind == "phone" else "home"
    caption = c.phone_label if kind == "phone" else c.email_label
    error_id = f"{kind}-error-{index}"
    return Div(
        label_select(kind, index, label or default_label),
        Input(
            type="tel" if kind == "phone" else "email",
            name=f"{kind}_value_{index}",
            value=value,
            aria_label=f"{caption} {index + 1}",
            aria_invalid="true" if error else None,
            aria_describedby=error_id if error else None,
            autocomplete="off",
            cls="ct-handle-input",
        ),
        Small(error, id=error_id, cls="ct-error") if error else None,
        cls="ct-handle-row",
        data_kind=kind,
    )


def add_row_button(kind: str, next_index: int):
    c = cfg()
    return Button(
        _PLUS_SVG, Span(c.add_phone_label if kind == "phone" else c.add_email_label),
        type="button",
        id=f"ct-add-{kind}",
        cls="ct-add-row",
        hx_get=f"/contacts/field-row?kind={kind}&index={next_index}",
        hx_target=f"#ct-{kind}-rows",
        hx_swap="beforeend",
        data_testid=f"add-{kind}",
    )


def contact_form(values: dict, errors: dict, contact_id: Optional[int] = None):
    """Create and edit share one form. ``values`` is what to show (from the
    record, from query parameters, or what was just submitted), ``errors``
    maps field keys to messages and ``"_form"`` to a summary."""
    c = cfg()
    editing = contact_id is not None
    phones = values.get("phones") or []
    emails = values.get("emails") or []
    # A kind with nothing saved still shows one blank row, so the first
    # number never needs the "Add phone" round trip.
    phone_rows = phones or [("mobile", "")]
    email_rows = emails or [("home", "")]
    summary = errors.get("_form")
    form = Form(
        P(summary, cls="ct-error-summary", role="alert") if summary else None,
        Div(
            Label(c.first_name_label, Input(name="first_name", value=values.get("first_name", ""), autocomplete="off")),
            Label(c.last_name_label, Input(name="last_name", value=values.get("last_name", ""), autocomplete="off")),
            Label(c.company_label, Input(name="company", value=values.get("company", ""), autocomplete="off")),
            cls="ct-form-names",
        ),
        Fieldset(
            Legend(c.phone_label),
            Div(*[handle_input_row("phone", i, lab, val, errors.get(f"phone_{i}", ""))
                  for i, (lab, val) in enumerate(phone_rows)], id="ct-phone-rows"),
            add_row_button("phone", len(phone_rows)),
            cls="ct-form-group",
        ),
        Fieldset(
            Legend(c.email_label),
            Div(*[handle_input_row("email", i, lab, val, errors.get(f"email_{i}", ""))
                  for i, (lab, val) in enumerate(email_rows)], id="ct-email-rows"),
            add_row_button("email", len(email_rows)),
            cls="ct-form-group",
        ),
        Label(c.note_label, Textarea(values.get("note", ""), name="note", rows="3"), cls="ct-form-note"),
        Label(
            Input(type="checkbox", name="favorite", value="1", checked=bool(values.get("favorite"))),
            c.favorite_label,
            cls="ct-form-check",
        ),
        Div(
            A(c.cancel_label, href=f"/contacts/{contact_id}" if editing else "/contacts", cls="ct-btn",
              data_testid="cancel-edit"),
            Button(c.done_label if editing else c.create_label, type="submit", cls="ct-btn is-primary",
                   data_testid="save-contact"),
            cls="ct-form-foot",
        ),
        method="post",
        action=f"/contacts/{contact_id}" if editing else "/contacts",
        # Submitted through htmx so a refusal swaps the form in place: the
        # chrome is only spliced into GETs, so a re-rendered POST response
        # would come back without its title bar and dock. A save answers
        # with HX-Redirect -- a real navigation -- and method/action remain
        # the no-script fallback.
        hx_post=f"/contacts/{contact_id}" if editing else "/contacts",
        hx_target="#ct-detail",
        hx_swap="outerHTML",
        cls="ct-form",
        # Server-side validation only, so every refusal reads the same in
        # every browser and lands in the page an agent observes, rather than
        # as a native bubble outside the accessibility tree.
        novalidate=True,
        id="ct-form",
    )
    delete = None
    if editing:
        delete = Div(
            Button(
                c.delete_label,
                type="button",
                cls="ct-btn is-danger",
                onclick="document.getElementById('ct-delete-dialog').showModal()",
                data_testid="delete-contact",
            ),
            Dialog(
                Article(
                    P(c.delete_confirm.format(name=values.get("_name", "")), cls="ct-dialog-text"),
                    Footer(
                        Form(Button(c.cancel_label, cls="ct-btn", data_testid="delete-cancel"), method="dialog"),
                        Form(Button(c.delete_confirm_button, type="submit", cls="ct-btn is-danger",
                                    data_testid="delete-confirm"),
                             method="post", action=f"/contacts/{contact_id}/delete"),
                    ),
                ),
                id="ct-delete-dialog",
                cls="ct-dialog",
            ),
            cls="ct-delete",
        )
    return Article(
        back_link() if single_pane() else None,
        H2(c.edit_contact_heading if editing else c.new_contact_heading, cls="ct-form-heading"),
        form,
        delete,
        cls="ct-detail is-form",
        id="ct-detail",
    )


def values_from(contact: Contact) -> dict:
    own = handles_for(contact.id)
    return {
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "company": contact.company,
        "note": contact.note,
        "favorite": bool(contact.favorite),
        "phones": [(h.label, format_handle(h.value)) for h in own if h.kind == "phone"],
        "emails": [(h.label, h.value) for h in own if h.kind == "email"],
        "_name": display_name(contact),
    }


def parse_form(form) -> dict:
    """Submitted fields -> the shape ``contact_form`` renders. Blank handle
    rows are dropped; rows keep their submitted order."""
    def rows(kind):
        indices = sorted(
            int(key.rsplit("_", 1)[1]) for key in form.keys()
            if key.startswith(f"{kind}_value_") and key.rsplit("_", 1)[1].isdigit()
        )
        out = []
        for i in indices:
            value = str(form.get(f"{kind}_value_{i}", "")).strip()
            if value:
                out.append((str(form.get(f"{kind}_label_{i}", "") or "other"), value))
        return out

    return {
        "first_name": str(form.get("first_name", "")).strip(),
        "last_name": str(form.get("last_name", "")).strip(),
        "company": str(form.get("company", "")).strip(),
        "note": str(form.get("note", "")).strip(),
        "favorite": bool(form.get("favorite")),
        "phones": rows("phone"),
        "emails": rows("email"),
    }


def validate(values: dict, contact_id: Optional[int] = None) -> tuple[dict, list]:
    """Errors keyed like the form, plus the normalised handles to store as
    ``(kind, label, value)``. A handle another contact already owns is refused
    and names the owner -- which contact owns a handle decides the name its
    thread shows and who a payment reaches."""
    c = cfg()
    errors: dict = {}
    clean: list = []
    seen: set = set()
    for kind in ("phone", "email"):
        for i, (label, raw) in enumerate(values[f"{kind}s"]):
            value = normalize_handle(raw)
            if value is None or handle_kind(value) != kind:
                errors[f"{kind}_{i}"] = c.error_invalid_phone if kind == "phone" else c.error_invalid_email
                continue
            if value in seen:
                errors[f"{kind}_{i}"] = c.error_repeated.format(handle=format_handle(value))
                continue
            owner = contact_for_handle(value)
            if owner is not None and owner.id != contact_id:
                errors[f"{kind}_{i}"] = c.error_duplicate.format(
                    handle=format_handle(value), name=display_name(owner)
                )
                continue
            seen.add(value)
            label = label if label in ("mobile", "work", "home", "other") else "other"
            clean.append((kind, label, value))
    if not (values["first_name"] or values["last_name"] or values["company"] or values["phones"] or values["emails"]):
        errors["_form"] = c.error_empty
    elif errors:
        errors["_form"] = next(iter(v for k, v in errors.items() if k != "_form"))
    return errors, clean


def _store_handles(contact_id: int, clean: list) -> None:
    for kind, label, value in clean:
        handles.insert(ContactHandle(id=None, contact_id=contact_id, kind=kind, label=label, value=value))


# --- Page frame ---------------------------------------------------------------

styles = Style("""
    .ct-root {
        --pico-background-color: var(--color-bg);
        --pico-color: var(--color-fg);
        --pico-muted-color: var(--color-muted);
        --pico-muted-border-color: var(--color-border);
        --pico-h1-color: var(--color-fg);
        --pico-h2-color: var(--color-fg);
        --pico-h3-color: var(--color-fg);
        --pico-primary: var(--color-primary);
        --pico-primary-background: var(--color-primary);
        --pico-primary-border: var(--color-primary);
        --pico-primary-hover-background: var(--color-primary-hover);
        --pico-primary-inverse: var(--color-on-primary);
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
        font-family: var(--font-family);
        color: var(--color-fg);
        max-width: 1120px;
        margin: 0 auto;
        padding: calc(var(--space) * 2) calc(var(--space) * 3)
                 calc(var(--space) * 2 + var(--oa-dock-space, 0px));
    }
    /* One pane at desktop width reads as a column, not a 1100px-wide list. */
    .ct-root[data-single="true"]:not(.is-phone) { max-width: 760px; }
    html, body { background-color: var(--color-bg); }
    .ct-root a { text-decoration: none; }
    .ct-root .sr-only {
        position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
        overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
    }

    .ct-top { display: flex; align-items: center; justify-content: space-between; gap: var(--space); }
    .ct-top > div, .ct-top > a:first-child { margin-bottom: 0 !important; }
    .ct-top h1 { color: var(--color-fg); }

    /* Buttons. Links styled as buttons stay links in the tree. */
    .ct-btn {
        display: inline-flex; align-items: center; gap: calc(var(--space) * 0.5);
        width: auto; margin: 0;
        padding: calc(var(--space) * 0.6) calc(var(--space) * 1.4);
        border: 1px solid var(--color-border); border-radius: var(--radius);
        background: transparent; color: var(--color-fg);
        font: inherit; font-size: var(--font-size-sm); font-weight: 600; line-height: 1.2;
        cursor: pointer;
    }
    .ct-btn:hover { border-color: var(--color-muted); }
    .ct-btn.is-primary { background: var(--color-primary); border-color: var(--color-primary); color: var(--color-on-primary); }
    .ct-btn.is-primary:hover { background: var(--color-primary-hover); border-color: var(--color-primary-hover); }
    .ct-btn.is-danger { color: var(--color-danger); border-color: color-mix(in srgb, var(--color-danger) 45%, transparent); }
    .ct-btn.is-danger[type="submit"] { background: var(--color-danger); color: var(--color-btn-fg); border-color: var(--color-danger); }
    .ct-btn:focus { box-shadow: none; }
    .ct-btn:focus-visible, .ct-row:focus-visible, .ct-action:focus-visible, .ct-back:focus-visible {
        outline: 2px solid var(--color-accent); outline-offset: 2px;
    }

    /* The window: one bordered frame, list beside card, hairline between. */
    .ct-window {
        display: grid;
        grid-template-columns: minmax(240px, 300px) 1fr;
        margin-top: calc(var(--space) * 1.5);
        border: 1px solid var(--color-border);
        border-radius: calc(var(--radius) * 1.5);
        overflow: hidden;
        height: calc(100vh - 7.5rem - var(--oa-titlebar-h, 0px) - var(--oa-dock-space, 0px));
        min-height: 26rem;
        background: var(--color-bg);
    }
    .ct-window.is-single { grid-template-columns: 1fr; height: auto; min-height: 0; }

    .ct-listpane {
        display: flex; flex-direction: column; min-height: 0;
        background: var(--color-surface);
        border-right: 1px solid var(--color-border);
    }
    .ct-window.is-single .ct-listpane { border-right: 0; }
    .ct-search { display: block; border-radius: 0; margin: 0; padding: var(--space); border-bottom: 1px solid var(--color-border); }
    .ct-search input[type="search"] { box-sizing: border-box; width: 100%; margin: 0; height: auto; padding-top: calc(var(--space) * 0.5); padding-bottom: calc(var(--space) * 0.5); }
    .ct-list { position: relative; flex: 1; min-height: 0; display: flex; }
    .ct-sections { flex: 1; overflow-y: auto; padding-bottom: var(--space); }
    .ct-section-heading {
        position: sticky; top: 0; z-index: 1;
        margin: 0; padding: calc(var(--space) * 0.35) calc(var(--space) * 1.25);
        background: var(--color-surface);
        border-bottom: 1px solid var(--color-border);
        color: var(--color-muted);
        font-size: var(--font-size-sm); font-weight: 700;
    }
    .ct-row {
        display: flex; flex-direction: column;
        padding: calc(var(--space) * 0.55) calc(var(--space) * 1.25);
        color: var(--color-fg);
        border-left: 3px solid transparent;
    }
    .ct-row:hover { background: color-mix(in srgb, var(--color-fg) 5%, transparent); }
    .ct-row[aria-current="page"] {
        background: color-mix(in srgb, var(--color-accent) 14%, transparent);
        border-left-color: var(--color-accent);
    }
    .ct-row-name { font-weight: 500; }
    .ct-row-sub { color: var(--color-muted); font-size: var(--font-size-sm); }
    .ct-index {
        display: flex; flex-direction: column; justify-content: center;
        padding: var(--space) calc(var(--space) * 0.35);
        font-size: 0.68rem; font-weight: 700; line-height: 1.25; text-align: center;
    }
    .ct-index a { color: var(--color-accent); }
    .ct-index span { color: color-mix(in srgb, var(--color-muted) 45%, transparent); }
    .ct-empty { padding: calc(var(--space) * 2); color: var(--color-muted); }

    /* The card. */
    .ct-detail { margin: 0; padding: calc(var(--space) * 3) calc(var(--space) * 4); overflow-y: auto;
                 background: var(--color-bg); box-shadow: none; border-radius: 0; }
    .ct-hero { display: flex; flex-direction: column; align-items: center; text-align: center;
               gap: calc(var(--space) * 0.5); margin: 0; padding: 0; background: none; border: 0; }
    .ct-avatar {
        display: flex; align-items: center; justify-content: center;
        border-radius: 50%;
        background: linear-gradient(180deg,
            color-mix(in srgb, var(--color-muted) 80%, var(--color-bg)),
            var(--color-muted));
        color: var(--color-bg);
        font-weight: 600; letter-spacing: 0.02em;
    }
    .ct-avatar.is-lg { width: 72px; height: 72px; font-size: 1.85rem; }
    .ct-name { margin: calc(var(--space) * 0.5) 0 0; font-size: 1.75rem; font-weight: 600; line-height: 1.2; }
    .ct-company { margin: 0; color: var(--color-muted); }

    .ct-actions { display: flex; justify-content: center; gap: calc(var(--space) * 2.5);
                  margin: calc(var(--space) * 2) 0 calc(var(--space) * 2.5); }
    .ct-action-wrap { display: inline-flex; }
    .ct-action {
        display: inline-flex; flex-direction: column; align-items: center; gap: calc(var(--space) * 0.4);
        width: auto; margin: 0; padding: 0; border: 0; background: none;
        color: var(--color-accent); font: inherit; font-size: var(--font-size-sm); font-weight: 500;
        cursor: pointer;
    }
    .ct-action-icon {
        display: flex; align-items: center; justify-content: center;
        width: 44px; height: 44px; border-radius: 50%;
        background: color-mix(in srgb, var(--color-accent) 12%, transparent);
    }
    .ct-action:hover .ct-action-icon { background: color-mix(in srgb, var(--color-accent) 20%, transparent); }
    .ct-action:disabled { color: var(--color-muted); cursor: default; opacity: 1; }
    .ct-action:disabled .ct-action-icon { background: color-mix(in srgb, var(--color-muted) 12%, transparent); }

    .ct-groups { display: flex; flex-direction: column; gap: calc(var(--space) * 1.25); max-width: 560px; margin: 0 auto; }
    .ct-groups > .ct-group:first-child { border-top: 1px solid var(--color-border); }
    .ct-field {
        display: grid; grid-template-columns: 6.5rem 1fr; align-items: baseline; gap: calc(var(--space) * 1.5);
        padding: calc(var(--space) * 0.75) 0;
        border-bottom: 1px solid var(--color-border);
    }
    /* The label column: quiet, lowercase, right-aligned against its value. */
    .ct-field-label { color: var(--color-muted); font-size: var(--font-size-sm); text-align: right; }
    .ct-field-body { display: flex; align-items: center; justify-content: space-between; gap: var(--space); min-width: 0; }
    .ct-field-value { color: var(--color-fg); overflow-wrap: anywhere; }
    .ct-note { margin: 0; white-space: pre-wrap; }
    .ct-detail-foot { display: flex; justify-content: center; margin: calc(var(--space) * 2.5) 0 0;
                      padding: 0; background: none; border: 0; }

    .ct-back { display: inline-flex; align-items: center; gap: 0.2rem; margin-bottom: var(--space);
               color: var(--color-accent); font-weight: 500; }

    /* Form. */
    .ct-form-heading { margin: 0 0 calc(var(--space) * 1.5); font-size: 1.35rem; }
    .ct-form {
        max-width: 560px;
        --pico-form-element-spacing-vertical: calc(var(--space) * 0.5);
        --pico-form-element-spacing-horizontal: calc(var(--space) * 0.75);
    }
    .ct-form-names { display: grid; grid-template-columns: 1fr 1fr; gap: 0 var(--space); }
    .ct-form-names label:last-child { grid-column: 1 / -1; }
    .ct-form label { font-size: var(--font-size-sm); color: var(--color-muted); }
    .ct-form input, .ct-form select, .ct-form textarea { margin-top: 0.2rem; color: var(--color-fg); }
    .ct-form-group { margin: var(--space) 0; padding: 0; border: 0; }
    .ct-form-group legend { color: var(--color-muted); font-size: var(--font-size-sm); margin-bottom: 0.3rem; }
    .ct-handle-row { display: grid; grid-template-columns: 7.5rem 1fr; gap: 0 calc(var(--space) * 0.75); align-items: start; }
    .ct-handle-row select, .ct-handle-row input { margin-bottom: calc(var(--space) * 0.6); }
    .ct-handle-row .ct-error { grid-column: 2; margin: -0.3rem 0 calc(var(--space) * 0.6); }
    .ct-add-row {
        display: inline-flex; align-items: center; gap: 0.3rem; width: auto; margin: 0;
        padding: 0.2rem 0; border: 0; background: none; color: var(--color-accent);
        font: inherit; font-size: var(--font-size-sm); font-weight: 600; cursor: pointer;
    }
    .ct-form-check { display: flex; align-items: center; gap: 0.5rem; margin: var(--space) 0; color: var(--color-fg) !important; }
    .ct-form-check input { margin: 0; }
    .ct-form-foot { display: flex; justify-content: flex-end; gap: var(--space); margin-top: calc(var(--space) * 1.5); }
    .ct-error { color: var(--color-danger); font-size: var(--font-size-sm); }
    .ct-error-summary {
        padding: calc(var(--space) * 0.75) var(--space); border-radius: var(--radius);
        border: 1px solid color-mix(in srgb, var(--color-danger) 45%, transparent);
        background: color-mix(in srgb, var(--color-danger) 8%, transparent);
        color: var(--color-danger);
    }
    .ct-handle-input[aria-invalid="true"] { border-color: var(--color-danger); }
    .ct-delete { max-width: 560px; margin-top: calc(var(--space) * 2.5); padding-top: calc(var(--space) * 1.5);
                 border-top: 1px solid var(--color-border); }
    .ct-dialog article { max-width: 26rem; }
    .ct-dialog footer { display: flex; justify-content: flex-end; gap: var(--space); }
    .ct-dialog footer form { margin: 0; }

    /* Phone: a single column of full-width rows. */
    .ct-root.is-phone { padding: var(--space) var(--space) calc(var(--space) + var(--oa-dock-space, 0px)); }
    .ct-root.is-phone .ct-window { border-radius: var(--radius); }
    .ct-root.is-phone .ct-detail { padding: calc(var(--space) * 1.5); }
    .ct-root.is-phone .ct-index { display: none; }
    .ct-root.is-phone .ct-field { grid-template-columns: 4.5rem 1fr; gap: var(--space); }
    .ct-root.is-phone .ct-form-names { grid-template-columns: 1fr; }
""")


def page(*panes, status: int = 200):
    """The frame every route renders: header, then the window."""
    factor = device.form_factor(app.config)
    body = Div(
        contacts_theme(),
        styles,
        Style(copy_styles("ct-copy")),
        Div(logo_title_container, new_contact_link(), cls="ct-top"),
        Div(*panes, cls="ct-window" + (" is-single" if len(panes) == 1 else "")),
        COPY_SCRIPT,
        cls=f"ct-root is-{factor}",
        data_theme=pico_theme(),
        data_layout=current_layout(),
        data_single=str(single_pane()).lower(),
    )
    if status != 200:
        # FtResponse keeps FastHTML's full-page rendering (headers, htmx,
        # Pico) and only changes the status code.
        return FtResponse((Title(cfg().title), body), status_code=status)
    return Title(cfg().title), body


def not_found_page():
    return page(Article(back_link(), P(cfg().not_found, cls="ct-empty"), cls="ct-detail"), status=404)


def frame(detail, query: str = "", selected_id: Optional[int] = None):
    """Two panes, or one: the list when there is no detail to show, else the
    detail alone with its back link."""
    if single_pane():
        return page(detail) if detail is not None else page(list_pane(query, selected_id))
    if detail is None:
        detail = Article(P(cfg().no_contacts, cls="ct-empty"), cls="ct-detail", id="ct-detail")
    return page(list_pane(query, selected_id), detail)


# --- Routes -------------------------------------------------------------------


@app.get("/contacts")
def contacts_index(q: str = ""):
    if single_pane():
        return page(list_pane(q))
    people = sorted((p for p in all_contacts() if matches(p, q)), key=sort_name)
    first = people[0] if people else None
    return frame(detail_card(first) if first else None, q, first.id if first else None)


@app.get("/contacts/search")
def contacts_search(q: str = "", selected: str = ""):
    """htmx partial: the list for a search term. A direct (non-htmx) hit, e.g.
    from history, gets the full page instead of a bare fragment."""
    selected_id = int(selected) if selected.isdigit() else None
    return contact_list(q, selected_id)


@app.get("/contacts/new")
def contacts_new(phone: str = "", email: str = "", first_name: str = "", last_name: str = "", company: str = ""):
    """The blank form, optionally pre-filled -- the messenger's "Add to
    contacts" banner links here with the unknown sender's number."""
    values = {
        "first_name": first_name, "last_name": last_name, "company": company,
        "phones": [("mobile", format_handle(normalize_handle(phone) or phone))] if phone else [],
        "emails": [("home", email)] if email else [],
    }
    return frame(contact_form(values, {}))


@app.get("/contacts/field-row")
def contacts_field_row(kind: str = "phone", index: int = 0):
    """htmx partial: one more empty phone/email row, plus the add button again
    (out of band) carrying the next index."""
    kind = "email" if kind == "email" else "phone"
    button = add_row_button(kind, index + 1)
    button.attrs["hx-swap-oob"] = "true"
    return handle_input_row(kind, index), button


@app.get("/contacts/{contact_id:int}")
def contacts_detail(contact_id: int):
    contact = get_contact(contact_id)
    if contact is None:
        return not_found_page()
    return frame(detail_card(contact), selected_id=contact.id)


@app.get("/contacts/{contact_id:int}/edit")
def contacts_edit(contact_id: int):
    contact = get_contact(contact_id)
    if contact is None:
        return not_found_page()
    return frame(contact_form(values_from(contact), {}, contact.id), selected_id=contact.id)


@app.post("/contacts")
async def contacts_create(req):
    values = parse_form(await req.form())
    errors, clean = validate(values)
    if errors:
        return _form_error(req, contact_form(values, errors))
    person = contacts.insert(
        Contact(id=None, first_name=values["first_name"], last_name=values["last_name"],
                company=values["company"], note=values["note"], favorite=values["favorite"])
    )
    _store_handles(person.id, clean)
    return _saved(req, f"/contacts/{person.id}")


@app.post("/contacts/{contact_id:int}")
async def contacts_update(contact_id: int, req):
    contact = get_contact(contact_id)
    if contact is None:
        return not_found_page()
    values = parse_form(await req.form())
    values["_name"] = display_name(contact)
    errors, clean = validate(values, contact_id)
    if errors:
        return _form_error(req, contact_form(values, errors, contact_id), selected_id=contact_id)
    contact.first_name, contact.last_name = values["first_name"], values["last_name"]
    contact.company, contact.note, contact.favorite = values["company"], values["note"], values["favorite"]
    contacts.update(contact)
    for h in handles_for(contact_id):
        handles.delete(h.id)
    _store_handles(contact_id, clean)
    return _saved(req, f"/contacts/{contact_id}")


@app.post("/contacts/{contact_id:int}/delete")
def contacts_delete(contact_id: int):
    contact = get_contact(contact_id)
    if contact is None:
        return not_found_page()
    for h in handles_for(contact_id):
        handles.delete(h.id)
    contacts.delete(contact_id)
    return RedirectResponse("/contacts", status_code=303)


@app.get("/contacts/{contact_id:int}/copy/{handle_id:int}")
def contacts_copy(contact_id: int, handle_id: int):
    """The value a Copy pill puts on the clipboard, as plain text. A read, so
    ``/contacts_all`` is unaffected; 404 rather than an empty string that
    would paste as nothing."""
    owned = next((h for h in handles_for(contact_id) if h.id == handle_id), None)
    if owned is None:
        return Response("", status_code=404)
    return Response(format_handle(owned.value) if owned.kind == "phone" else owned.value, media_type="text/plain")


def _is_htmx(req) -> bool:
    return bool(req.headers.get("hx-request"))


def _saved(req, url: str):
    """After a save, go to the card with a full navigation. For htmx that is
    HX-Redirect: a plain 303 would be followed inside the XHR and the whole
    page swapped into the form's slot."""
    if _is_htmx(req):
        return Response("", headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def _form_error(req, form_article, selected_id: Optional[int] = None):
    """A refused submission re-renders the form with its errors, so they are
    on the page the agent is already looking at. Through htmx that is the
    form alone, swapped in place with a 200 (htmx does not swap 4xx bodies);
    without script it is the whole page with 422."""
    if _is_htmx(req):
        return form_article
    if single_pane():
        response = page(form_article, status=422)
    else:
        response = page(list_pane("", selected_id), form_article, status=422)
    return response


def get_contacts_routes():
    return app.routes
