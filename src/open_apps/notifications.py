"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Server side of the adversarial events: delivery and the unread-toast store.

``OpenAppsTask`` decides *when* an event fires (``open_apps.alignment.events``)
and POSTs it to ``/events/fire``. This module does what that route promises:

* ``channel: messenger`` -- writes the message into OpenMessages through the
  app's own ``receive_message`` (new senders become contacts), so it is real
  state the agent can open and answer;
* every channel -- records an unread toast. Every full page the server sends
  renders the unread ones (``open_apps.ui.toast`` via the start page's page
  renderer), so a toast survives navigation until it is dismissed or opened.

NOT SCOREABLE, like the window chrome's state: the store lives in memory, is
reset with the apps, and nothing here reaches ``get_current_state``. A task
must never pass or fail on whether a notification was left showing, and the
reward diff must never see toast bookkeeping. ``tests/test_events_ui.py``
checks that.

Kept free of the routes and the markup so it can be unit-tested without a
server; ``open_apps.apps.start_page.main`` mounts the routes.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Optional

CHANNELS = ("messenger", "system")
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")


# Run in the agent's page right after ``/events/fire`` answers (by
# ``OpenAppsTask`` and the MCP ``Session``): put the toast stylesheet in
# <head> once, then replace the toast stack (or add it). The markup is the
# route's answer, rendered for that page, so it is byte-for-byte what a reload
# would show. Replacing the whole stack rather than appending one toast keeps
# the two identical. Takes ``{css, html, css_id, stack_id}``.
INJECT_TOAST_JS = """
(args) => {
  if (!document.getElementById(args.css_id)) {
    const style = document.createElement("style");
    style.id = args.css_id;
    style.textContent = args.css;
    (document.head || document.documentElement).appendChild(style);
  }
  const holder = document.createElement("div");
  holder.innerHTML = args.html;
  const stack = holder.firstElementChild;
  const old = document.getElementById(args.stack_id);
  if (old) { old.replaceWith(stack); }
  else { (document.body || document.documentElement).appendChild(stack); }
  return true;
}
"""


class NotificationError(ValueError):
    """A malformed ``/events/fire`` payload (answered with a 400)."""


@dataclass
class Notification:
    id: str
    title: str
    body: str
    channel: str
    link: Optional[str] = None
    sender: Optional[str] = None
    category: Optional[str] = None
    # Messenger events: whether delivery opened a new conversation (the
    # sender was not a contact). None for toast-only events.
    created: Optional[bool] = None

    @property
    def dom_id(self) -> str:
        """``id`` reduced to characters that are safe in an HTML id."""
        return _SAFE_ID.sub("-", self.id).strip("-") or "toast"

    def to_dict(self) -> dict:
        return asdict(self)


# id -> notification, in firing order. Unread only: reading one removes it.
_unread: dict[str, Notification] = {}


def reset() -> None:
    """Forget every toast. Called whenever the apps are (re)seeded."""
    _unread.clear()


def unread() -> list[Notification]:
    return list(_unread.values())


def get(notification_id: str) -> Optional[Notification]:
    return _unread.get(notification_id)


def mark_read(notification_id: str) -> Optional[Notification]:
    """Dismissed or opened: either way it stops rendering. Returns what was
    removed (None if it was not unread)."""
    return _unread.pop(notification_id, None)


def mark_conversation_read(contact: str) -> list[Notification]:
    """The user opened ``contact``'s conversation: every unread messenger
    toast from that contact is read. Returns what was removed.

    Opening the thread from the messenger's own list (or by URL) reads the
    message exactly as following the toast does, so leaving the toast up
    would show the agent an "unread" message it has already read -- and make
    toast state depend on which of two equivalent paths the agent took.
    """
    read = [
        n for n in _unread.values() if n.channel == "messenger" and n.sender == contact
    ]
    for n in read:
        _unread.pop(n.id, None)
    return read


def is_same_origin_path(link: Any) -> bool:
    """A path on this server, never a URL elsewhere. A toast is rendered into
    every page and its link is followed by an agent, so it must not be able to
    send the agent off-site (``//host`` is protocol-relative, not a path)."""
    return isinstance(link, str) and link.startswith("/") and not link.startswith("//")


def _text(payload: Mapping, key: str, required: bool = True) -> Optional[str]:
    value = payload.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise NotificationError(f"'{key}' must be a non-empty string, got {value!r}")
    return value


def parse(payload: Any) -> Notification:
    """Validate an ``/events/fire`` body (``Event.payload()``)."""
    if not isinstance(payload, Mapping):
        raise NotificationError(f"payload must be a JSON object, got {payload!r}")
    channel = payload.get("channel")
    if channel not in CHANNELS:
        raise NotificationError(f"'channel' must be one of {CHANNELS}, got {channel!r}")
    toast = payload.get("toast") or {}
    if not isinstance(toast, Mapping):
        raise NotificationError(f"'toast' must be an object, got {toast!r}")
    link = toast.get("link")
    if link is not None and not is_same_origin_path(link):
        raise NotificationError(f"'toast.link' must be a same-origin path, got {link!r}")
    return Notification(
        id=_text(payload, "id"),
        title=_text(toast, "title"),
        body=_text(payload, "body"),
        channel=channel,
        link=link,
        sender=_text(payload, "sender", required=channel == "messenger"),
        category=payload.get("category"),
    )


def fire(
    payload: Any, deliver_message: Callable[[str, str], Optional[bool]]
) -> Notification:
    """Deliver one event and record its toast.

    ``deliver_message(sender, body)`` is the messenger's ``receive_message``;
    passed in so this module does not import an app (and tests can stub it).
    Its return value -- did it open a new conversation -- is kept as
    ``Notification.created``.
    The message is written *before* the toast is recorded: a toast whose link
    led to a conversation that failed to appear would be worse than none.
    """
    notification = parse(payload)
    if notification.channel == "messenger":
        notification.created = bool(deliver_message(notification.sender, notification.body))
    _unread[notification.id] = notification
    return notification
