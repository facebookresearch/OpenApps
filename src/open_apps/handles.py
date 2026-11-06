"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Phone and email handles: one canonical spelling for every app.

A *handle* is how a person is reached -- a phone number or an email address.
Contacts stores them, the messenger keys its threads by them, and the bank's
payment flow sends money to them. All three have to agree on one spelling, or
"(555) 010-0001" typed into a form would miss "+15550100001" in state, and a
task's target would diff against the observed state on punctuation alone.

So every handle goes through :func:`normalize_handle` on the way in:

* phones become E.164 (``+15550100001``). Ten digits with no country code are
  read as US numbers, which matches every seeded number in the repo;
* 5-6 digit short codes (``72524``) stay bare digits, the way verification and
  spam senders appear on a real phone;
* emails are stripped and lowercased.

Anything else is ``None``, so a caller can refuse it inline rather than store a
handle nobody can ever match.
"""
from __future__ import annotations

import re

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Characters people put in phone numbers that carry no meaning.
_PHONE_PUNCTUATION = re.compile(r"[\s().\-]")


def normalize_handle(raw: str | None) -> str | None:
    """The canonical form of ``raw``, or ``None`` when it is not a handle."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if "@" in text:
        email = text.lower()
        return email if _EMAIL.match(email) else None

    compact = _PHONE_PUNCTUATION.sub("", text)
    if compact.startswith("+"):
        digits = compact[1:]
        return f"+{digits}" if digits.isdigit() and 8 <= len(digits) <= 15 else None
    if not compact.isdigit():
        return None
    if len(compact) == 10:
        return f"+1{compact}"
    if len(compact) == 11 and compact.startswith("1"):
        return f"+{compact}"
    if 5 <= len(compact) <= 6:
        return compact
    return None


def handle_kind(handle: str) -> str:
    """``"email"`` or ``"phone"`` (short codes count as phones)."""
    return "email" if "@" in handle else "phone"


def format_handle(handle: str) -> str:
    """How a handle is shown to a person: ``+1 (555) 010-0001`` for a North
    American number, everything else as stored. Feeding the result back
    through :func:`normalize_handle` gives the handle again."""
    if handle.startswith("+1") and len(handle) == 12:
        d = handle[2:]
        return f"+1 ({d[:3]}) {d[3:6]}-{d[6:]}"
    return handle
