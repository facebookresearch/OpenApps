"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Which out-of-scope changes are *harm* (irreversible) rather than merely
spurious (reversible).

Each app declares its own map in ``config/apps/<app>/default.yaml``:

    irreversible:
      <collection>:
        <added|removed|changed>: <category>

over the collections defined in ``open_apps.alignment.changes`` (e.g. todo
``items``, messenger ``sent``, online shop ``orders``). Living next to the app's
other config means a variation (or a future app) can override it like any
other key, and the map in effect is copied into each ``EpisodeRecord`` so an
offline re-score uses the rules the run was scored under.

Config groups are named for the app (``maps``, ``onlineshop``,
``code_editor``); the cross-app state is keyed differently (``map``,
``online_shop``, ``codeeditor``). Maps are stored by *state key*, because
that is what the changes they classify are keyed by.
"""

from __future__ import annotations

import copy
import functools
from pathlib import Path
from typing import Any, Mapping, Optional

from open_apps.alignment._config import plain
from open_apps.alignment.changes import OPS, Change

# config/apps/<group> -> key in get_current_state()
APP_STATE_KEYS: dict[str, str] = {
    "todo": "todo",
    "calendar": "calendar",
    "maps": "map",
    "messenger": "messenger",
    "openbanking": "openbanking",
    "onlineshop": "online_shop",
    "code_editor": "codeeditor",
    "ui_library": "ui_library",
}
# Every name a user might write for an app (config group, state key, or the
# app's URL stem) -> state key.
_ALIASES: dict[str, str] = {
    **APP_STATE_KEYS,
    **{v: v for v in APP_STATE_KEYS.values()},
    "messages": "messenger",
    "codeeditor": "codeeditor",
    "uilibrary": "ui_library",
}

IrreversibleMap = dict[str, dict[str, dict[str, str]]]


def resolve_app(name: str) -> Optional[str]:
    """State key for an app name, or None if unknown."""
    return _ALIASES.get(str(name))


def _validated(app: str, raw: Any) -> dict[str, dict[str, str]]:
    raw = plain(raw, resolve=False) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"apps.{app}.irreversible must be a mapping, got {raw!r}")
    out: dict[str, dict[str, str]] = {}
    for collection, ops in raw.items():
        ops = plain(ops, resolve=False) or {}
        bad = set(ops) - set(OPS)
        if bad:
            raise ValueError(
                f"apps.{app}.irreversible.{collection}: unknown op(s) {sorted(bad)}"
            )
        out[str(collection)] = {str(op): str(cat) for op, cat in ops.items()}
    return out


def map_from_apps_config(apps: Any) -> IrreversibleMap:
    """Collect ``irreversible:`` blocks from a resolved ``config.apps``."""
    out: IrreversibleMap = {}
    for group, cfg in (plain(apps, resolve=False) or {}).items():
        state_key = APP_STATE_KEYS.get(group)
        if (
            state_key
            and isinstance(cfg, Mapping)
            and cfg.get("irreversible") is not None
        ):
            out[state_key] = _validated(group, cfg["irreversible"])
    return out


@functools.lru_cache(maxsize=4)
def _load_default_map(config_root: str) -> IrreversibleMap:
    import yaml

    apps = {}
    for group in APP_STATE_KEYS:
        path = Path(config_root) / "apps" / group / "default.yaml"
        if path.exists():
            apps[group] = yaml.safe_load(path.read_text()) or {}
    return map_from_apps_config(apps)


def load_default_map(config_root: str | Path | None = None) -> IrreversibleMap:
    """The maps declared in ``config/apps/*/default.yaml``.

    For records that predate (or were built without) an embedded map. Reads
    the files directly instead of composing Hydra, so it works anywhere the
    repo's ``config/`` is present.
    """
    if config_root is None:
        from open_apps import config_dir

        config_root = config_dir()
    return copy.deepcopy(_load_default_map(str(config_root)))


def category_for(irreversible: Mapping, change: Change) -> Optional[str]:
    """Harm category of ``change``, or None when the change is reversible."""
    return ((irreversible.get(change.app) or {}).get(change.collection) or {}).get(
        change.op
    )
