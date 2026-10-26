#!/usr/bin/env python3
"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

One-off: re-key the messenger content packs by handle.

The messenger used to key its threads -- and every message's sender -- by the
contact's name (``chat_history: {Alice: [[text, owner, "Alice", ts], ...]}``).
Names now live in the Contacts app only; the messenger stores the phone number
or email a thread is with, and draws whatever name Contacts has for it. This
rewrites ``config/apps/messenger/content/*.yaml`` into that shape:

    groups:                       # group chats: display title + member handles
      "group:fantastic4": {title: Fantastic4GroupChat, members: [...]}
    threads:                      # handle -> [[text, sender, timestamp], ...]
      "+15550100001":             # Alice
        - ["Hello Yann, ...", "+15550100001", "Sep 16, 10:00PM"]

``sender`` is ``"you"`` or the sender's handle; the old owner flag was the
same fact twice. Group metadata sits beside ``threads`` rather than inside a
thread so that a ``+threads`` content pack (open_apps.utils.merge_plus_keys)
can still append messages to a group chat: list values concatenate, dict
values would be overwritten wholesale.

The name -> handle table below must agree with the contacts seeds
(config/apps/contacts/content/default.yaml and long_descriptions.yaml). Kept
in the tree as the record of how the packs were converted; running it again
is a no-op once no pack has a ``chat_history`` key.

    uv run scripts/migrate_messenger_handles.py
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

CONTENT_DIR = Path(__file__).resolve().parents[1] / "config/apps/messenger/content"

GROUP_KEY = "group:fantastic4"
GROUP_TITLE = "Fantastic4GroupChat"

HANDLES = {
    "Alice": "+15550100001",
    "Bob": "+15550100002",
    "Charlie": "+15550100003",
}
# The long_descriptions pack's extra people, numbered in pack order.
LONG_PEOPLE = [
    "Sieglinde", "Hans", "Greta", "Lukas", "Anna", "Franz", "Josef", "Karl",
    "Helga", "Otto", "Mom", "Dad", "Alex", "Priya", "Jamal", "Elena", "Self",
]
HANDLES.update({name: f"+155501001{n:02d}" for n, name in enumerate(LONG_PEOPLE, start=1)})

# A number nobody has saved: the thread "add this person to your contacts"
# tasks act on. Its timestamp is the newest in the seed, as an unread text
# would be.
UNKNOWN_HANDLE = "+15550142290"
UNKNOWN_MESSAGE = {
    "default": "Hi, it's Jordan from the climbing gym. Save my number?",
    "german": "Hi, hier ist Jordan aus der Kletterhalle. Speicherst du meine Nummer?",
    "mandarin": "你好，我是攀岩馆的 Jordan。可以存一下我的号码吗？",
}
UNKNOWN_TIMESTAMP = "Apr 18, 6:30PM"

_FORMAT_COMMENT = (
    "# threads: handle -> [[message, sender, timestamp], ...]. `sender` is \"you\"\n"
    "# or the sender's handle. Names come from the Contacts app, never from here.\n"
)


def _q(value: str) -> str:
    """A YAML double-quoted scalar. JSON string syntax is valid YAML and keeps
    non-ASCII text (German, Mandarin) readable in the file."""
    return json.dumps(value, ensure_ascii=False)


def _key(name: str) -> str:
    if name == GROUP_TITLE:
        return GROUP_KEY
    return HANDLES[name]


def _sender(name: str) -> str:
    return "you" if name == "you" else HANDLES[name]


def _thread_block(key: str, label: str, messages: list[list[str]]) -> str:
    lines = [f"  {_q(key)}:  # {label}"]
    for text, sender, timestamp in messages:
        lines.append(f"    - [{_q(text)}, {_q(sender)}, {_q(timestamp)}]")
    return "\n".join(lines)


def _header(text: str) -> str:
    """Everything above the first data key, minus the old format comment."""
    kept = []
    for line in text.splitlines():
        if line.split(":", 1)[0].strip() in ("users", "+users", "chat_history", "+chat_history"):
            break
        if "chat messages:" in line or "bool indicating the owner" in line:
            continue
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + "\n"


def migrate(path: Path) -> bool:
    text = path.read_text()
    data = yaml.safe_load(text) or {}
    plus = "+chat_history" in data
    history = data.get("+chat_history" if plus else "chat_history")
    if history is None:
        print(f"skip {path.name}: already migrated")
        return False

    pack = path.stem
    blocks = []
    for name, rows in history.items():
        messages = [[str(text), _sender(sender), str(ts)] for text, _owner, sender, ts in rows]
        blocks.append(_thread_block(_key(name), name, messages))
    # Full packs carry their own copy of the unknown sender, in their own
    # language; append-only packs inherit default's.
    if not plus:
        message = UNKNOWN_MESSAGE.get(pack, UNKNOWN_MESSAGE["default"])
        blocks.append(
            _thread_block(UNKNOWN_HANDLE, "not in Contacts", [[message, UNKNOWN_HANDLE, UNKNOWN_TIMESTAMP]])
        )

    out = _header(text) + "\n" + _FORMAT_COMMENT
    if pack == "default":
        members = ", ".join(_q(HANDLES[n]) for n in ("Alice", "Bob", "Charlie"))
        out += (
            "groups:\n"
            f"  {_q(GROUP_KEY)}:\n"
            f"    title: {_q(GROUP_TITLE)}\n"
            f"    members: [{members}]\n"
        )
    out += ("+threads:\n" if plus else "threads:\n") + "\n\n".join(blocks) + "\n"
    path.write_text(out)
    print(f"migrated {path.name}: {len(blocks)} threads")
    return True


def main() -> None:
    for path in sorted(CONTENT_DIR.glob("*.yaml")):
        migrate(path)


if __name__ == "__main__":
    main()
