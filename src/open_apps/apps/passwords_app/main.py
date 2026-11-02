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
from typing import Optional

from fasthtml.common import *

from open_apps.apps.start_page.helper import create_logo_header
from open_apps.frontend import local_hdrs

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
    for raw in _plain(config.passwords.get("entries")) or []:
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


@app.get("/passwords_all")
def get_all():
    """Used for rewards."""
    payload = {"items": [item_payload(i) for i in all_items()]}
    return Response(json.dumps(payload), headers={"Content-Type": "application/json"})


def get_passwords_routes():
    return app.routes
