"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Reference evaluator for ``config/compose_rules.yaml``.

Rules are data so the docs-site JS can evaluate the same file; this module is
the reference that port is tested against. See the rules file's header for
the rule grammar.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from open_apps import config_dir

#: The constraint rules, as data. At the config root on purpose: Hydra lists
#: root files as *primary* configs (``--config-name``), never as options of a
#: group, so no ``x=compose_rules`` override can ever select it.
RULES_FILE = config_dir() / "compose_rules.yaml"
ACTIONS = ("disable", "warn", "require")


@dataclass(frozen=True)
class Rule:
    id: str
    when: dict[str, Any]
    then: Literal["disable", "warn", "require"]
    reason: str
    fix: dict[str, Any] | None = field(default=None)


def load_rules(path: Path | None = None) -> list[Rule]:
    """Parse and validate the rules file (plain YAML: it is not a Hydra config)."""
    raw = yaml.safe_load((path or RULES_FILE).read_text("utf-8")) or []
    rules = []
    for entry in raw:
        rule = Rule(
            id=entry["id"],
            when=dict(entry["when"]),
            then=entry["then"],
            reason=" ".join(str(entry["reason"]).split()),
            fix=dict(entry["fix"]) if entry.get("fix") else None,
        )
        if rule.then not in ACTIONS:
            raise ValueError(f"rule {rule.id}: then={rule.then!r} not in {ACTIONS}")
        rules.append(rule)
    return rules
