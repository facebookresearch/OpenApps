"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The OpenPasswords app: a password manager in 1Password's shape.

One vault ("Personal") of items -- logins, Wi-Fi networks and secure notes --
shown as categories, a list, and the selected item's labelled fields. A
password is masked until revealed, and a Copy pill fetches its value on click
rather than carrying it in the markup, so a secret reaches the page an agent
observes only when the agent asks for it.

One table: an item's fields depend on its kind, but they are few enough that
a column per field (empty where the kind has none) beats a second table.
Deleting moves an item to Recently Deleted (``deleted``); deleting it there
removes it.

The scoring surface is ``/passwords_all``: every item, deleted ones included,
sorted by id and byte-stable across reads like every other ``/<app>_all``.
The agent's ``goto`` refuses it (any path segment ending in ``_all``).
"""
import json
from dataclasses import dataclass
from datetime import date
from typing import Optional
from urllib.parse import urlencode

from fasthtml.common import *

from open_apps import device
from open_apps.apps.start_page.helper import create_logo_header
from open_apps.frontend import local_hdrs
from open_apps.theme import theme_asset, theme_style
from open_apps.ui.copy import COPY_SCRIPT, CopyButton, copy_styles

KINDS = ("login", "wifi", "note")


@dataclass
class Item:
    id: int
    kind: str  # login | wifi | note
    title: str  # a login's name, a network's name, a note's title
    username: str  # logins only
    password: str  # logins and Wi-Fi
    website: str  # logins only
    security: str  # Wi-Fi only ("WPA3")
    notes: str
    favorite: bool
    deleted: bool  # in Recently Deleted
    edited_on: str  # ISO date of the last save, from the pinned calendar


app, rt = fast_app(default_hdrs=False, hdrs=local_hdrs())

db = None
items = None
logo_title_container = None


def _plain(node):
    """An OmegaConf node (or None) as plain Python containers."""
    from omegaconf import OmegaConf

    return OmegaConf.to_container(node, resolve=True) if OmegaConf.is_config(node) else node


def set_environment(config):
    """Create the table and re-seed it from the Hydra config.

    Called again on reset, so the table is cleared first rather than relying
    on the caller having dropped it.
    """
    global db, items, logo_title_container
    app.config = config
    db = database(config.passwords.database_path)
    items = db.create(Item, name="items", pk="id")
    items.delete_where()
    # `extra_entries` is how the noise packs add decoys: a plain key rather
    # than a `+entries` append, because plus-keys are merged by the launcher
    # only, and a config composed without it (tests, the MCP server) would
    # silently drop them.
    for raw in [*(_plain(config.passwords.get("entries")) or []),
                *(_plain(config.passwords.get("extra_entries")) or [])]:
        items.insert(_seed_item(raw))
    start_page_apps = getattr(getattr(config, "start_page", None), "apps", None)
    if start_page_apps is not None and "passwords" in start_page_apps:
        logo_title_container = create_logo_header(
            app_config=start_page_apps.passwords,
            base_url="/passwords",
            current_file_path=__file__,
        )


def _seed_item(raw: dict) -> Item:
    kind = str(raw.get("kind") or "login")
    if kind not in KINDS:
        raise ValueError(f"passwords seed: {raw.get('title')!r} has unknown kind {kind!r}")
    return Item(
        id=None,
        kind=kind,
        title=str(raw.get("title") or ""),
        username=str(raw.get("username") or ""),
        password=str(raw.get("password") or ""),
        website=str(raw.get("website") or ""),
        security=str(raw.get("security") or ""),
        notes=str(raw.get("notes") or ""),
        favorite=bool(raw.get("favorite", False)),
        deleted=bool(raw.get("deleted", False)),
        edited_on=str(raw.get("edited_on") or today()),
    )


def cfg():
    """The app's own config node."""
    return app.config.passwords


def today() -> str:
    """The pinned "today" a save is stamped with (ISO)."""
    return str(getattr(app.config.passwords, "today", None) or "")


def all_items() -> list[Item]:
    return sorted(items(), key=lambda i: i.id)


def get_item(item_id: int) -> Optional[Item]:
    return next((i for i in items() if i.id == item_id), None)


def item_payload(item: Item) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "username": item.username,
        "password": item.password,
        "website": item.website,
        "security": item.security,
        "notes": item.notes,
        "favorite": bool(item.favorite),
        "deleted": bool(item.deleted),
        "edited_on": item.edited_on,
    }


# ---------------------------------------------------------------------------
# Interface.
#
# Modelled on 1Password: categories on the left, the item list, and the
# selected item's fields as a labelled table. Navigation is plain links and
# form posts -- full page loads -- never an htmx swap into <body>, because the
# window chrome is spliced in only on full-page GETs. htmx is used only for
# pieces inside the page: the live search and the password reveal.

CATEGORIES = ("all", "favorites", "login", "wifi", "note")
MASK = "\u2022" * 10  # fixed length: the mask must not give the length away


def current_layout() -> str:
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return str(getattr(config.passwords, "layout", "default"))


def single_pane() -> bool:
    """One pane at a time: the ``list_detail`` layout, and always on a phone."""
    return current_layout() == "list_detail" or device.form_factor(app.config) == "phone"


def passwords_theme():
    return theme_style(app.config, "passwords")


def pico_theme() -> str:
    tone = theme_asset(app.config, "passwords", "tone", "light")
    return "dark" if str(tone) == "dark" else "light"


def in_category(item: Item, category: str) -> bool:
    if category == "deleted":
        return bool(item.deleted)
    if item.deleted:
        return False
    if category == "favorites":
        return bool(item.favorite)
    if category in KINDS:
        return item.kind == category
    return True


def matches(item: Item, query: str) -> bool:
    """Search over title, website and username -- never the password."""
    q = " ".join(str(query or "").split()).casefold()
    if not q:
        return True
    return any(q in field.casefold() for field in (item.title, item.website, item.username) if field)


def listed(category: str, query: str = "") -> list[Item]:
    chosen = [i for i in all_items() if in_category(i, category) and matches(i, query)]
    return sorted(chosen, key=lambda i: (i.title.casefold(), i.id))


def subtitle(item: Item) -> str:
    if item.kind == "login":
        return item.username or item.website
    if item.kind == "wifi":
        return cfg().kind_labels.wifi
    return (item.notes or "").splitlines()[0] if item.notes else ""


def human_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _category(value: str) -> str:
    return value if value in (*CATEGORIES, "deleted") else "all"


# --- Glyphs -----------------------------------------------------------------

def _svg(body: str, size: int = 18) -> NotStr:
    return NotStr(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{size}" height="{size}" '
        'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true" focusable="false">{body}</svg>'
    )


GLYPHS = {
    "all": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
           '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "favorites": '<path d="m12 4 2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.4 7.2 18.9l.9-5.4-3.9-3.8 5.4-.8z"/>',
    "login": '<circle cx="8.5" cy="12" r="3.5"/><path d="M12 12h8M17 12v3M20 12v2"/>',
    "wifi": '<path d="M4.5 9.5a11 11 0 0 1 15 0M7.5 12.8a6.5 6.5 0 0 1 9 0M10.4 16a2.3 2.3 0 0 1 3.2 0"/>'
            '<circle cx="12" cy="18.6" r=".6" fill="currentColor"/>',
    "note": '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4M9 12h6M9 15.5h6"/>',
    "deleted": '<path d="M5 7h14M10 7V5h4v2M7 7l1 12.5h8L17 7"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "back": '<path d="m14.5 5.5-6.5 6.5 6.5 6.5"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12"/><circle cx="12" cy="12" r="3"/>',
}


def glyph(name: str, size: int = 18) -> NotStr:
    return _svg(GLYPHS[name], size)


def item_tile(item: Item, size: str = "sm"):
    """The list's icon tile: the item's initial for a login, a glyph otherwise."""
    if item.kind == "login":
        letter = next((ch for ch in item.title if ch.isalnum()), "?").upper()
        return Span(letter, cls=f"pw-tile is-{size} is-login", aria_hidden="true")
    return Span(glyph(item.kind, 22 if size == "lg" else 16), cls=f"pw-tile is-{size} is-{item.kind}",
                aria_hidden="true")


# --- Sidebar ----------------------------------------------------------------

def sidebar(category: str):
    c = cfg()
    counts = {key: len(listed(key)) for key in (*CATEGORIES, "deleted")}

    def entry(key: str, label: str):
        return A(
            Span(glyph(key), cls="pw-side-icon"),
            Span(label, cls="pw-side-label"),
            Span(str(counts[key]), cls="pw-count"),
            href=f"/passwords?category={key}",
            cls="pw-side-link",
            aria_current="page" if key == category else None,
            data_testid=f"category-{key}",
        )

    return Nav(
        Div(Span(c.vault_name, cls="pw-vault-name"), cls="pw-vault"),
        Div(*[entry(key, c.categories[key]) for key in CATEGORIES], cls="pw-side-group"),
        Div(entry("deleted", c.deleted_label), cls="pw-side-group is-bottom"),
        cls="pw-sidebar",
        aria_label=c.categories_label,
    )


# --- List -------------------------------------------------------------------

def item_row(item: Item, category: str, selected_id: Optional[int]):
    sub = subtitle(item)
    return A(
        item_tile(item),
        Span(
            Span(item.title, cls="pw-row-title"),
            Span(sub, cls="pw-row-sub") if sub else None,
            cls="pw-row-text",
        ),
        href=f"/passwords/{item.id}?category={category}",
        cls="pw-row",
        id=f"item-row-{item.id}",
        data_testid=f"item-row-{item.id}",
        aria_current="page" if item.id == selected_id else None,
    )


def item_list(category: str, query: str = "", selected_id: Optional[int] = None):
    c = cfg()
    rows = listed(category, query)
    if not rows:
        if query.strip():
            message = c.no_results.format(query=query)
        elif category == "deleted":
            message = c.empty_deleted
        else:
            message = c.no_items
        return Div(P(message, cls="pw-empty"), id="pw-list", cls="pw-list")
    return Div(*[item_row(i, category, selected_id) for i in rows], id="pw-list", cls="pw-list")


def list_pane(category: str, query: str = "", selected_id: Optional[int] = None):
    c = cfg()
    return Div(
        Form(
            Input(
                type="search",
                name="q",
                value=query,
                id="pw-search",
                placeholder=c.search_placeholder,
                aria_label=c.search_label,
                autocomplete="off",
                hx_get="/passwords/search",
                hx_trigger="input changed delay:200ms, search",
                hx_target="#pw-list",
                hx_swap="outerHTML",
                hx_include="#pw-list-state",
            ),
            Input(type="hidden", name="category", value=category),
            method="get",
            action="/passwords",
            role="search",
            cls="pw-search",
        ),
        Div(
            Input(type="hidden", name="category", value=category),
            Input(type="hidden", name="selected", value=str(selected_id or "")),
            id="pw-list-state",
            hidden=True,
        ),
        item_list(category, query, selected_id),
        cls="pw-listpane",
        aria_label=c.list_label,
    )


# --- Detail -----------------------------------------------------------------

def field_row(label: str, body, row_id: Optional[str] = None):
    return Div(
        Span(label, cls="pw-field-label"),
        Div(*body, cls="pw-field-body") if isinstance(body, tuple) else Div(body, cls="pw-field-body"),
        cls="pw-field",
        id=row_id,
    )


def copy_button(item: Item, field: str, label: str):
    c = cfg()
    return CopyButton(
        label,
        url=f"/passwords/{item.id}/copy/{field}",
        copy_label=c.copy_label,
        copied_label=c.copied_label,
        failed_label=c.copy_failed_label,
        prefix="pw-copy",
        testid=f"copy-{field}",
    )


def password_row(item: Item, revealed: bool = False):
    """The password, masked until revealed. Reveal is a swap of this row
    alone, and a read: it writes nothing."""
    c = cfg()
    return field_row(
        c.password_label,
        (
            Span(item.password if revealed else MASK, cls="pw-secret" + (" is-revealed" if revealed else ""),
                 data_testid="password-value"),
            Span(
                Button(
                    glyph("eye", 16), Span(c.hide_label if revealed else c.reveal_label),
                    type="button",
                    cls="pw-reveal",
                    aria_pressed="true" if revealed else "false",
                    hx_get=f"/passwords/{item.id}/reveal?show={0 if revealed else 1}",
                    hx_target="#pw-password-row",
                    hx_swap="outerHTML",
                    data_testid="reveal-password",
                ),
                copy_button(item, "password", c.password_label),
                cls="pw-field-actions",
            ),
        ),
        row_id="pw-password-row",
    )


def detail_fields(item: Item) -> list:
    c = cfg()
    rows = []
    if item.kind == "login":
        if item.username:
            rows.append(field_row(c.username_label, (
                Span(item.username, cls="pw-field-value"),
                Span(copy_button(item, "username", c.username_label), cls="pw-field-actions"),
            )))
        if item.password:
            rows.append(password_row(item))
        if item.website:
            rows.append(field_row(c.website_label, A(
                item.website, href=item.website, target="_blank", rel="noopener noreferrer",
                cls="pw-field-value pw-link",
            )))
    elif item.kind == "wifi":
        rows.append(field_row(c.network_label, Span(item.title, cls="pw-field-value")))
        if item.password:
            rows.append(password_row(item))
        if item.security:
            rows.append(field_row(c.security_label, Span(item.security, cls="pw-field-value")))
    if item.notes:
        rows.append(field_row(c.notes_label, P(item.notes, cls="pw-notes")))
    return rows


def back_link(category: str):
    return A(glyph("back"), Span(cfg().back_label), href=f"/passwords?category={category}", cls="pw-back",
             data_testid="back-to-list")


def detail_card(item: Item, category: str = "all", extra=None):
    c = cfg()
    return Article(
        back_link(category) if single_pane() else None,
        Header(
            item_tile(item, "lg"),
            Div(
                H2(item.title, cls="pw-title", data_testid="item-title"),
                P(c.kind_labels[item.kind], Span(c.favorite_badge, cls="pw-badge") if item.favorite else None,
                  cls="pw-kind"),
            ),
            cls="pw-hero",
        ),
        P(c.deleted_note, cls="pw-deleted-note") if item.deleted else None,
        Div(*detail_fields(item), cls="pw-fields"),
        extra,
        P(c.last_edited.format(date=human_date(item.edited_on)), cls="pw-edited"),
        detail_actions(item, category),
        cls="pw-detail",
        id="pw-detail",
        data_item_id=str(item.id),
    )


def empty_detail(message: str):
    return Article(P(message, cls="pw-empty"), cls="pw-detail", id="pw-detail")


def confirm_dialog(dialog_id: str, text: str, action: str, button: str, testid: str):
    c = cfg()
    return Dialog(
        Article(
            P(text, cls="pw-dialog-text"),
            Footer(
                Form(Button(c.cancel_label, cls="pw-btn", data_testid=f"{testid}-cancel"), method="dialog"),
                Form(Button(button, type="submit", cls="pw-btn is-danger", data_testid=f"{testid}-confirm"),
                     method="post", action=action),
            ),
        ),
        id=dialog_id,
        cls="pw-dialog",
    )


def detail_actions(item: Item, category: str):
    """Edit and Delete for an item in the vault; only Delete Permanently for
    one in Recently Deleted. Both deletes ask first."""
    c = cfg()
    if item.deleted:
        return Footer(
            Button(c.purge_label, type="button", cls="pw-btn is-danger", data_testid="purge-item",
                   onclick="document.getElementById('pw-purge-dialog').showModal()"),
            confirm_dialog("pw-purge-dialog", c.purge_confirm.format(title=item.title),
                           f"/passwords/{item.id}/purge", c.purge_confirm_button, "purge"),
            cls="pw-actions",
        )
    return Footer(
        A(c.edit_label, href=f"/passwords/{item.id}/edit", cls="pw-btn", data_testid="edit-item"),
        Button(c.delete_label, type="button", cls="pw-btn is-danger", data_testid="delete-item",
               onclick="document.getElementById('pw-delete-dialog').showModal()"),
        confirm_dialog("pw-delete-dialog", c.delete_confirm.format(title=item.title),
                       f"/passwords/{item.id}/delete", c.delete_confirm_button, "delete"),
        cls="pw-actions",
    )


def type_picker():
    c = cfg()
    return Article(
        back_link("all") if single_pane() else None,
        H2(c.new_item_heading, cls="pw-form-heading"),
        Div(
            *[
                A(Span(glyph(kind, 22), cls=f"pw-tile is-lg is-{kind}"), Span(c.kind_labels[kind], cls="pw-pick-label"),
                  href=f"/passwords/new?kind={kind}", cls="pw-pick", data_testid=f"new-{kind}")
                for kind in KINDS
            ],
            cls="pw-picker",
        ),
        cls="pw-detail",
        id="pw-detail",
    )


def _input(name: str, value: str, errors: dict, **attrs):
    error = errors.get(name)
    return Input(name=name, value=value or "", id=f"pw-{name}", autocomplete="off",
                 aria_invalid="true" if error else None,
                 aria_describedby=f"pw-{name}-error" if error else None, **attrs)


def form_field(label: str, control, name: str, errors: dict):
    error = errors.get(name)
    return Label(
        Span(label, cls="pw-form-label"),
        control,
        Small(error, id=f"pw-{name}-error", cls="pw-error") if error else None,
        cls="pw-form-field",
    )


def item_form(values: dict, errors: dict, item_id: Optional[int] = None):
    """Create and edit share one form. Submitted through htmx so a refusal
    swaps in place (a re-rendered POST response would come back without the
    window chrome); a save answers with HX-Redirect, a real navigation."""
    c = cfg()
    kind = values.get("kind", "login")
    editing = item_id is not None
    heading = (c.edit_heading if editing else c.new_heading).format(kind=c.kind_labels[kind])
    fields = [form_field(c.title_label, _input("title", values.get("title"), errors), "title", errors)]
    if kind == "login":
        fields += [
            form_field(c.username_label, _input("username", values.get("username"), errors), "username", errors),
            form_field(c.password_label, _input("password", values.get("password"), errors, type="text",
                                                spellcheck="false"), "password", errors),
            form_field(c.website_label, _input("website", values.get("website"), errors, type="url",
                                               placeholder="https://"), "website", errors),
        ]
    elif kind == "wifi":
        chosen = values.get("security") or c.security_options[0]
        fields += [
            form_field(c.password_label, _input("password", values.get("password"), errors, type="text",
                                                spellcheck="false"), "password", errors),
            form_field(c.security_label, Select(*[Option(o, value=o, selected=o == chosen)
                                                  for o in c.security_options], name="security",
                                                id="pw-security"), "security", errors),
        ]
    fields.append(form_field(c.notes_label, Textarea(values.get("notes", ""), name="notes", id="pw-notes",
                                                     rows="4"), "notes", errors))
    fields.append(Label(Input(type="checkbox", name="favorite", value="1", checked=bool(values.get("favorite"))),
                        c.favorite_label, cls="pw-form-check"))
    summary = errors.get("_form")
    action = f"/passwords/{item_id}" if editing else "/passwords"
    cancel = f"/passwords/{item_id}" if editing else "/passwords"
    return Article(
        back_link("all") if single_pane() else None,
        H2(heading, cls="pw-form-heading"),
        Form(
            P(summary, cls="pw-error-summary", role="alert") if summary else None,
            Input(type="hidden", name="kind", value=kind),
            *fields,
            Div(
                A(c.cancel_label, href=cancel, cls="pw-btn", data_testid="cancel-edit"),
                Button(c.save_label, type="submit", cls="pw-btn is-primary", data_testid="save-item"),
                cls="pw-form-foot",
            ),
            method="post",
            action=action,
            hx_post=action,
            hx_target="#pw-detail",
            hx_swap="outerHTML",
            novalidate=True,
            cls="pw-form",
            id="pw-form",
        ),
        cls="pw-detail is-form",
        id="pw-detail",
    )


def values_from(item: Item) -> dict:
    return {key: getattr(item, key) for key in
            ("kind", "title", "username", "password", "website", "security", "notes", "favorite")}


# --- Page frame -------------------------------------------------------------

styles = Style("""
    .pw-root {
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
        max-width: 1240px;
        margin: 0 auto;
        padding: calc(var(--space) * 2) calc(var(--space) * 3)
                 calc(var(--space) * 2 + var(--oa-dock-space, 0px));
    }
    .pw-root[data-single="true"]:not(.is-phone) { max-width: 760px; }
    html, body { background-color: var(--color-bg); }
    .pw-root a { text-decoration: none; }

    .pw-top { display: flex; align-items: center; justify-content: space-between; gap: var(--space); }
    .pw-top > div, .pw-top > a:first-child { margin-bottom: 0 !important; }
    .pw-top h1 { color: var(--color-fg); }

    .pw-btn {
        display: inline-flex; align-items: center; gap: calc(var(--space) * 0.45);
        width: auto; margin: 0;
        padding: calc(var(--space) * 0.55) calc(var(--space) * 1.3);
        border: 1px solid var(--color-border); border-radius: var(--radius);
        background: transparent; color: var(--color-fg);
        font: inherit; font-size: var(--font-size-sm); font-weight: 600; line-height: 1.2;
        cursor: pointer;
    }
    .pw-btn:hover { border-color: var(--color-muted); }
    .pw-btn.is-primary { background: var(--color-primary); border-color: var(--color-primary); color: var(--color-on-primary); }
    .pw-btn.is-primary:hover { background: var(--color-primary-hover); border-color: var(--color-primary-hover); }
    .pw-btn.is-danger { color: var(--color-danger); border-color: color-mix(in srgb, var(--color-danger) 45%, transparent); }
    .pw-btn.is-danger[type="submit"] { background: var(--color-danger); color: var(--color-btn-fg); border-color: var(--color-danger); }
    .pw-btn:focus { box-shadow: none; }
    .pw-btn:focus-visible, .pw-row:focus-visible, .pw-side-link:focus-visible, .pw-reveal:focus-visible {
        outline: 2px solid var(--color-accent); outline-offset: 2px;
    }

    /* The window: categories, list, item -- one frame, hairlines between. */
    .pw-window {
        display: grid;
        grid-template-columns: 210px minmax(240px, 320px) 1fr;
        margin-top: calc(var(--space) * 1.5);
        border: 1px solid var(--color-border);
        border-radius: calc(var(--radius) * 1.5);
        overflow: hidden;
        height: calc(100vh - 7.5rem - var(--oa-titlebar-h, 0px) - var(--oa-dock-space, 0px));
        min-height: 26rem;
        background: var(--color-bg);
    }
    .pw-window.is-single { grid-template-columns: 1fr; height: auto; min-height: 0; }
    .pw-window.is-list { grid-template-columns: 1fr; height: auto; min-height: 0; }

    .pw-sidebar {
        display: flex; flex-direction: column; gap: var(--space);
        padding: var(--space) calc(var(--space) * 0.6);
        background: color-mix(in srgb, var(--color-surface) 70%, var(--color-bg));
        border-right: 1px solid var(--color-border);
        overflow-y: auto;
    }
    .pw-vault { padding: calc(var(--space) * 0.3) calc(var(--space) * 0.6); }
    .pw-vault-name { font-weight: 700; }
    .pw-side-group { display: flex; flex-direction: column; gap: 2px; }
    .pw-side-group.is-bottom { margin-top: auto; padding-top: var(--space); border-top: 1px solid var(--color-border); }
    .pw-side-link {
        display: flex; align-items: center; gap: calc(var(--space) * 0.6);
        padding: calc(var(--space) * 0.4) calc(var(--space) * 0.6);
        border-radius: var(--radius);
        color: var(--color-fg);
        font-size: var(--font-size-sm);
    }
    .pw-side-link:hover { background: color-mix(in srgb, var(--color-fg) 6%, transparent); }
    .pw-side-link[aria-current="page"] { background: color-mix(in srgb, var(--color-accent) 16%, transparent); font-weight: 600; }
    .pw-side-icon { display: inline-flex; color: var(--color-muted); }
    .pw-side-link[aria-current="page"] .pw-side-icon { color: var(--color-accent); }
    .pw-side-label { flex: 1; }
    .pw-count { color: var(--color-muted); font-size: 0.78rem; font-variant-numeric: tabular-nums; }

    .pw-listpane { display: flex; flex-direction: column; min-height: 0; border-right: 1px solid var(--color-border); }
    .pw-window.is-list .pw-listpane, .pw-window.is-single .pw-listpane { border-right: 0; }
    .pw-search { display: block; margin: 0; padding: var(--space); border-bottom: 1px solid var(--color-border); }
    .pw-search input[type="search"] { box-sizing: border-box; width: 100%; margin: 0; height: auto;
        padding-top: calc(var(--space) * 0.5); padding-bottom: calc(var(--space) * 0.5); }
    .pw-list { flex: 1; min-height: 0; overflow-y: auto; padding: calc(var(--space) * 0.4) 0; }
    .pw-row {
        display: flex; align-items: center; gap: calc(var(--space) * 0.75);
        padding: calc(var(--space) * 0.55) var(--space);
        color: var(--color-fg);
        border-left: 3px solid transparent;
    }
    .pw-row:hover { background: color-mix(in srgb, var(--color-fg) 5%, transparent); }
    .pw-row[aria-current="page"] { background: color-mix(in srgb, var(--color-accent) 14%, transparent); border-left-color: var(--color-accent); }
    .pw-row-text { display: flex; flex-direction: column; min-width: 0; }
    .pw-row-title { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .pw-row-sub { color: var(--color-muted); font-size: var(--font-size-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .pw-empty { padding: calc(var(--space) * 2); color: var(--color-muted); }

    /* Item tiles: a login shows its initial, other kinds a glyph. */
    .pw-tile {
        flex: none; display: inline-flex; align-items: center; justify-content: center;
        border-radius: calc(var(--radius) * 1.2);
        background: color-mix(in srgb, var(--color-accent) 16%, var(--color-bg));
        color: var(--color-accent); font-weight: 700;
    }
    .pw-tile.is-sm { width: 32px; height: 32px; font-size: 0.95rem; }
    .pw-tile.is-lg { width: 56px; height: 56px; font-size: 1.5rem; }
    .pw-tile.is-wifi { background: color-mix(in srgb, var(--color-primary) 16%, var(--color-bg)); color: var(--color-primary); }
    .pw-tile.is-note { background: color-mix(in srgb, var(--color-muted) 18%, var(--color-bg)); color: var(--color-fg); }

    .pw-detail { margin: 0; padding: calc(var(--space) * 2.5) calc(var(--space) * 3); overflow-y: auto;
                 background: var(--color-bg); box-shadow: none; border-radius: 0; }
    .pw-hero { display: flex; align-items: center; gap: var(--space); margin: 0 0 calc(var(--space) * 1.75);
               padding: 0; background: none; border: 0; }
    .pw-title { margin: 0; font-size: 1.55rem; font-weight: 650; line-height: 1.2; }
    .pw-kind { margin: 0.15rem 0 0; color: var(--color-muted); font-size: var(--font-size-sm); display: flex; gap: 0.5rem; align-items: center; }
    .pw-badge { padding: 0 0.45rem; border-radius: 999px; border: 1px solid var(--color-border); font-size: 0.72rem; }
    .pw-fields { border: 1px solid var(--color-border); border-radius: var(--radius); max-width: 620px; }
    .pw-field {
        display: grid; grid-template-columns: 7.5rem 1fr; align-items: center; gap: var(--space);
        padding: calc(var(--space) * 0.7) var(--space);
        border-bottom: 1px solid var(--color-border);
    }
    .pw-field:last-child { border-bottom: 0; }
    .pw-field-label { color: var(--color-muted); font-size: var(--font-size-sm); }
    .pw-field-body { display: flex; align-items: center; justify-content: space-between; gap: var(--space); min-width: 0; }
    .pw-field-value { overflow-wrap: anywhere; color: var(--color-fg); }
    .pw-link { color: var(--color-accent); }
    .pw-secret { font-family: var(--font-mono); letter-spacing: 0.08em; }
    .pw-secret.is-revealed { letter-spacing: 0.02em; }
    .pw-field-actions { display: inline-flex; align-items: center; gap: calc(var(--space) * 0.5); flex: none; }
    .pw-reveal {
        display: inline-flex; align-items: center; gap: 0.3rem; width: auto; margin: 0;
        padding: 0.15rem 0.6rem; border: 1px solid var(--color-border); border-radius: 999px;
        background: transparent; color: var(--color-fg); font: inherit; font-size: 0.8rem; cursor: pointer;
    }
    .pw-reveal:hover { border-color: var(--color-muted); }
    .pw-notes { margin: 0; white-space: pre-wrap; }
    .pw-edited { margin: calc(var(--space) * 1.25) 0 0; color: var(--color-muted); font-size: var(--font-size-sm); }
    .pw-back { display: inline-flex; align-items: center; gap: 0.2rem; margin-bottom: var(--space);
               color: var(--color-accent); font-weight: 500; }

    .pw-deleted-note { margin: 0 0 var(--space); color: var(--color-muted); font-size: var(--font-size-sm); }
    .pw-actions { display: flex; gap: var(--space); margin: calc(var(--space) * 1.5) 0 0; padding: 0;
                  background: none; border: 0; }
    .pw-dialog article { max-width: 26rem; }
    .pw-dialog footer { display: flex; justify-content: flex-end; gap: var(--space); }
    .pw-dialog footer form { margin: 0; }

    .pw-form-heading { margin: 0 0 calc(var(--space) * 1.5); font-size: 1.35rem; }
    .pw-picker { display: grid; grid-template-columns: repeat(3, minmax(0, 10rem)); gap: var(--space); }
    .pw-pick {
        display: flex; flex-direction: column; align-items: center; gap: calc(var(--space) * 0.6);
        padding: calc(var(--space) * 1.25) var(--space);
        border: 1px solid var(--color-border); border-radius: calc(var(--radius) * 1.5);
        color: var(--color-fg); font-weight: 600;
    }
    .pw-pick:hover { border-color: var(--color-accent); }
    .pw-form { max-width: 560px;
        --pico-form-element-spacing-vertical: calc(var(--space) * 0.5);
        --pico-form-element-spacing-horizontal: calc(var(--space) * 0.75); }
    .pw-form-field { display: block; margin-bottom: calc(var(--space) * 0.75); }
    .pw-form-label { display: block; color: var(--color-muted); font-size: var(--font-size-sm); margin-bottom: 0.2rem; }
    .pw-form input, .pw-form select, .pw-form textarea { margin-bottom: 0; color: var(--color-fg); }
    .pw-form #pw-password { font-family: var(--font-mono); }
    .pw-form-check { display: flex; align-items: center; gap: 0.5rem; margin: var(--space) 0; }
    .pw-form-check input { margin: 0; }
    .pw-form-foot { display: flex; justify-content: flex-end; gap: var(--space); margin-top: calc(var(--space) * 1.5); }
    .pw-error { display: block; color: var(--color-danger); font-size: var(--font-size-sm); margin-top: 0.25rem; }
    .pw-error-summary {
        padding: calc(var(--space) * 0.75) var(--space); border-radius: var(--radius);
        border: 1px solid color-mix(in srgb, var(--color-danger) 45%, transparent);
        background: color-mix(in srgb, var(--color-danger) 8%, transparent);
        color: var(--color-danger);
    }
    .pw-form input[aria-invalid="true"] { border-color: var(--color-danger); }

    /* One pane: the categories become a row of chips above the list. */
    .pw-window.is-list .pw-sidebar { flex-direction: row; flex-wrap: wrap; align-items: center;
        border-right: 0; border-bottom: 1px solid var(--color-border); }
    .pw-window.is-list .pw-vault { display: none; }
    .pw-window.is-list .pw-side-group { flex-direction: row; flex-wrap: wrap; }
    .pw-window.is-list .pw-side-group.is-bottom { margin-top: 0; padding-top: 0; border-top: 0; }

    .pw-root.is-phone { padding: var(--space) var(--space) calc(var(--space) + var(--oa-dock-space, 0px)); }
    .pw-root.is-phone .pw-detail { padding: calc(var(--space) * 1.5); }
    .pw-root.is-phone .pw-field { grid-template-columns: 1fr; gap: 0.2rem; }
""")


def new_item_link():
    c = cfg()
    return A(glyph("plus", 16), Span(c.new_item_label), href="/passwords/new", cls="pw-btn is-primary",
             data_testid="new-item")


def page(*panes, status: int = 200, list_only: bool = False):
    """The frame every route renders: header, then the window."""
    factor = device.form_factor(app.config)
    if len(panes) == 1:
        window_cls = "pw-window is-single"
    elif list_only:
        window_cls = "pw-window is-list"
    else:
        window_cls = "pw-window"
    body = Div(
        passwords_theme(),
        styles,
        Style(copy_styles("pw-copy")),
        Div(logo_title_container, new_item_link(), cls="pw-top"),
        Div(*panes, cls=window_cls),
        COPY_SCRIPT,
        cls=f"pw-root is-{factor}",
        data_theme=pico_theme(),
        data_layout=current_layout(),
        data_single=str(single_pane()).lower(),
    )
    if status != 200:
        return FtResponse((Title(cfg().title), body), status_code=status)
    return Title(cfg().title), body


def frame(detail, category: str = "all", query: str = "", selected_id: Optional[int] = None, status: int = 200):
    """Three panes, or one: in a single-pane layout, the categories and list
    when there is no item to show, else the item alone with its back link."""
    if single_pane():
        if detail is None:
            return page(sidebar(category), list_pane(category, query, selected_id), status=status, list_only=True)
        return page(detail, status=status)
    if detail is None:
        detail = empty_detail(cfg().empty_deleted if category == "deleted" else cfg().no_items)
    return page(sidebar(category), list_pane(category, query, selected_id), detail, status=status)


def not_found_page():
    return frame(Article(back_link("all"), P(cfg().not_found, cls="pw-empty"), cls="pw-detail",
                         id="pw-detail"), status=404) if not single_pane() else page(
        Article(back_link("all"), P(cfg().not_found, cls="pw-empty"), cls="pw-detail", id="pw-detail"),
        status=404,
    )


# --- Routes -------------------------------------------------------------------


@app.get("/passwords")
def passwords_index(category: str = "all", q: str = ""):
    category = _category(category)
    if single_pane():
        return frame(None, category, q)
    rows = listed(category, q)
    first = rows[0] if rows else None
    return frame(detail_card(first, category) if first else None, category, q, first.id if first else None)


@app.get("/passwords/search")
def passwords_search(q: str = "", category: str = "all", selected: str = ""):
    """htmx partial: the list for a search term."""
    selected_id = int(selected) if selected.isdigit() else None
    return item_list(_category(category), q, selected_id)


@app.get("/passwords/{item_id:int}")
def passwords_detail(item_id: int, category: str = "all"):
    item = get_item(item_id)
    if item is None:
        return not_found_page()
    category = _category(category)
    if not in_category(item, category):
        category = "deleted" if item.deleted else "all"
    return frame(detail_card(item, category), category, selected_id=item.id)


@app.get("/passwords/new")
def passwords_new(kind: str = ""):
    """The type picker, or with ``kind`` the blank form for that kind."""
    if kind not in KINDS:
        return frame(type_picker())
    return frame(item_form(blank_values(kind), {}))


@app.get("/passwords/{item_id:int}/edit")
def passwords_edit(item_id: int):
    item = get_item(item_id)
    if item is None or item.deleted:
        return not_found_page()
    return frame(item_form(values_from(item), {}, item.id), selected_id=item.id)


@app.post("/passwords")
async def passwords_create(req):
    values = parse_form(await req.form())
    item, errors = add_item(values)
    if errors:
        return _form_error(req, item_form(values, errors))
    return _saved(req, f"/passwords/{item.id}")


@app.post("/passwords/{item_id:int}")
async def passwords_update(item_id: int, req):
    item = get_item(item_id)
    if item is None or item.deleted:
        return not_found_page()
    values = {**parse_form(await req.form()), "kind": item.kind}
    saved, errors = update_item(item_id, values)
    if errors:
        return _form_error(req, item_form(values, errors, item_id), selected_id=item_id)
    return _saved(req, f"/passwords/{item_id}")


@app.post("/passwords/{item_id:int}/delete")
def passwords_delete(item_id: int):
    if delete_item(item_id) is None:
        return not_found_page()
    return RedirectResponse("/passwords?category=deleted", status_code=303)


@app.post("/passwords/{item_id:int}/purge")
def passwords_purge(item_id: int):
    if not purge_item(item_id):
        return not_found_page()
    return RedirectResponse("/passwords?category=deleted", status_code=303)


def _is_htmx(req) -> bool:
    return bool(req.headers.get("hx-request"))


def _saved(req, url: str):
    """After a save, go to the item with a full navigation. For htmx that is
    HX-Redirect: a plain 303 would be followed inside the XHR."""
    if _is_htmx(req):
        return Response("", headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def _form_error(req, form_article, selected_id: Optional[int] = None):
    """A refused submission re-renders the form with its errors: through htmx
    the form alone, swapped in place with a 200 (htmx does not swap 4xx
    bodies); without script the whole page with 422."""
    if _is_htmx(req):
        return form_article
    return frame(form_article, selected_id=selected_id, status=422)


@app.get("/passwords/{item_id:int}/reveal")
def passwords_reveal(item_id: int, show: int = 1):
    """htmx partial: the password row, revealed or masked. A read."""
    item = get_item(item_id)
    if item is None or not item.password:
        return Response("", status_code=404)
    return password_row(item, bool(show))


@app.get("/passwords/{item_id:int}/copy/{field}")
def passwords_copy(item_id: int, field: str):
    """The value a Copy pill puts on the clipboard, as plain text. A read, so
    ``/passwords_all`` is unaffected; 404 rather than an empty string that
    would paste as nothing."""
    item = get_item(item_id)
    value = {"password": item.password, "username": item.username}.get(field) if item else None
    if not value:
        return Response("", status_code=404)
    return Response(value, media_type="text/plain")


# ---------------------------------------------------------------------------
# Writes. ``add_item`` and ``update_item`` are the only two; the routes call
# them, and a task's scripted episode can too.


def blank_values(kind: str = "login") -> dict:
    return {"kind": kind, "title": "", "username": "", "password": "", "website": "",
            "security": "", "notes": "", "favorite": False}


def parse_form(form) -> dict:
    """Submitted fields as values. Everything is trimmed except the password,
    which is kept exactly as typed: "  pass  " is a different secret."""
    kind = str(form.get("kind") or "login")
    return {
        "kind": kind if kind in KINDS else "login",
        "title": str(form.get("title", "")).strip(),
        "username": str(form.get("username", "")).strip(),
        "password": str(form.get("password", "")),
        "website": str(form.get("website", "")).strip(),
        "security": str(form.get("security", "")).strip(),
        "notes": str(form.get("notes", "")).strip(),
        "favorite": bool(form.get("favorite")),
    }


def validate(values: dict) -> dict:
    """Errors keyed like the form, plus ``"_form"`` for the summary."""
    c = cfg()
    errors = {}
    if not values["title"]:
        errors["title"] = c.error_title
    if values["kind"] == "login" and not (values["username"] or values["password"]):
        errors["password"] = c.error_login
    if values["kind"] == "wifi" and not values["password"]:
        errors["password"] = c.error_wifi
    if errors:
        errors["_form"] = c.error_summary
    return errors


def _clean(values: dict) -> dict:
    """Drop the fields the kind has no use for, so a note never stores a
    password that was typed into a field it does not show."""
    kind = values["kind"]
    return {
        **values,
        "username": values["username"] if kind == "login" else "",
        "website": values["website"] if kind == "login" else "",
        "password": values["password"] if kind in ("login", "wifi") else "",
        "security": values["security"] if kind == "wifi" else "",
    }


def add_item(values: dict) -> tuple[Optional[Item], dict]:
    """Validate and store a new item; ``(item, {})`` or ``(None, errors)``."""
    errors = validate(values)
    if errors:
        return None, errors
    v = _clean(values)
    item = items.insert(Item(id=None, kind=v["kind"], title=v["title"], username=v["username"],
                             password=v["password"], website=v["website"], security=v["security"],
                             notes=v["notes"], favorite=v["favorite"], deleted=False, edited_on=today()))
    return item, {}


def update_item(item_id: int, values: dict) -> tuple[Optional[Item], dict]:
    """Validate and save an edit. The kind is the item's own, whatever the
    form says: a login does not turn into a note by editing it."""
    item = get_item(item_id)
    if item is None or item.deleted:
        return None, {"_form": cfg().not_found}
    values = {**values, "kind": item.kind}
    errors = validate(values)
    if errors:
        return None, errors
    v = _clean(values)
    for key in ("title", "username", "password", "website", "security", "notes", "favorite"):
        setattr(item, key, v[key])
    item.edited_on = today()
    items.update(item)
    return item, {}


def delete_item(item_id: int) -> Optional[Item]:
    """Move an item to Recently Deleted."""
    item = get_item(item_id)
    if item is None or item.deleted:
        return None
    item.deleted = True
    items.update(item)
    return item


def purge_item(item_id: int) -> bool:
    """Remove an item that is already in Recently Deleted."""
    item = get_item(item_id)
    if item is None or not item.deleted:
        return False
    items.delete(item_id)
    return True


@app.get("/passwords_all")
def get_all():
    """Used for rewards."""
    payload = {"items": [item_payload(i) for i in all_items()]}
    return Response(json.dumps(payload), headers={"Content-Type": "application/json"})


def get_passwords_routes():
    return app.routes
