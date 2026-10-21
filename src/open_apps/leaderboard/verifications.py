"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

``leaderboard/verifications.yaml``: the maintainer-owned record of which
bundles were re-run on the held-out seeds, and with what result.

    verifications:
      - bundle: <org>__<agent>__<suite>
        episodes_sha256: <sha256 of the bundle's episodes.jsonl.gz>
        agent_config_hash: <meta.yaml's agent_config_hash>
        date: 2026-10-20
        seeds_hash: <salted hash of the held-out seed set>
        aligned: 0.42      # held-out aligned rate
        delta: -0.03       # held-out minus public aligned

Why a separate file, not a ``verified`` block in the bundle's meta.yaml: a
submitter controls their bundle, and no PR check can be the enforcement
point (on ``pull_request`` the PR can edit the workflow that checks it). This
file is protected by CODEOWNERS (maintainer review required) and CI requires
a PR touching it to touch nothing else; ``build`` on main reads verified
status from here only. Each entry pins the exact bundle it verified -- the
episodes file's sha256 and the agent config hash -- so a re-submitted bundle
silently loses the badge (``build`` reports the entry as stale) instead of
inheriting a verification of different runs.

Written by ``uv run -m open_apps.leaderboard.verify record``; never by hand.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from open_apps.leaderboard import bundle as B

FILENAME = "verifications.yaml"
SCHEMA = "verifications.schema.json"
#: The fields a page shows for a verified row (the rest pin the bundle).
SHOWN = ("date", "seeds_hash", "aligned", "delta")

_HEADER = """\
# Maintainer-owned (CODEOWNERS): which bundles were re-run on the held-out
# seeds, and the result. Written by `uv run -m open_apps.leaderboard.verify
# record`; never edit by hand, and never in a PR that touches anything else
# (CI fails it). The leaderboard reads verified status from here only, and an
# entry whose episodes_sha256 or agent_config_hash no longer matches its
# bundle is dropped (reported as stale). See docs/leaderboard.md#verification.
"""


def default_path() -> Path:
    return B.leaderboard_dir() / FILENAME


def episodes_sha256(bundle: Path) -> str:
    h = hashlib.sha256()
    with open(Path(bundle) / B.EPISODES_FILE, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else value


def load(path: Optional[Path] = None) -> list[dict]:
    """The entries, schema-checked. A missing file is no verifications;
    a malformed one raises ``ValueError``."""
    path = Path(path) if path else default_path()
    if not path.is_file():
        return []
    data = B.read_yaml(path) or {}
    if isinstance(data, dict) and isinstance(data.get("verifications"), list):
        data = {
            "verifications": [
                {k: _plain(v) for k, v in e.items()} if isinstance(e, dict) else e
                for e in data["verifications"]
            ]
        }
    errors = B.schema_errors(data, B.load_schema(SCHEMA))
    if errors:
        raise ValueError(f"{path.name} is malformed: {'; '.join(errors[:5])}")
    entries = data["verifications"]
    names = [e["bundle"] for e in entries]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"{path.name} verifies {dupes} more than once")
    return entries


def write(entries: list[dict], path: Optional[Path] = None) -> Path:
    import yaml

    path = Path(path) if path else default_path()
    entries = sorted(entries, key=lambda e: e["bundle"])
    errors = B.schema_errors({"verifications": entries}, B.load_schema(SCHEMA))
    if errors:
        raise ValueError(f"would write a malformed {path.name}: {errors[:5]}")
    body = yaml.safe_dump(
        {"verifications": entries}, sort_keys=False, allow_unicode=True
    )
    path.write_text(_HEADER + body)
    return path


def upsert(entry: dict, path: Optional[Path] = None) -> Path:
    """Add ``entry``, replacing any earlier one for the same bundle."""
    entries = [e for e in load(path) if e["bundle"] != entry["bundle"]]
    return write([*entries, entry], path)


@dataclass
class Match:
    """What ``build`` makes of the entries against the bundles it ranks."""

    # bundle id -> the shown fields of its (current) verification
    verified: dict[str, dict]
    # (bundle id, why the entry no longer applies)
    stale: list[tuple[str, str]]


def match(entries: list[dict], bundles: dict[str, tuple[Path, dict]]) -> Match:
    """Entries that still describe their bundle. ``bundles`` maps a ranked
    bundle's id to ``(path, meta)``. An entry for a bundle that is not
    ranked, or whose episodes or agent config hash changed, is stale."""
    verified, stale = {}, []
    for entry in entries:
        name = entry["bundle"]
        if name not in bundles:
            stale.append((name, "no such ranked bundle"))
            continue
        path, meta = bundles[name]
        if entry["agent_config_hash"] != meta.get("agent_config_hash"):
            stale.append((name, "agent_config_hash differs from the bundle's"))
            continue
        if entry["episodes_sha256"] != episodes_sha256(path):
            stale.append((name, "episodes.jsonl.gz changed since it was verified"))
            continue
        verified[name] = {k: entry[k] for k in SHOWN}
    return Match(verified, stale)
