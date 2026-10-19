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

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from open_apps import config_dir

if TYPE_CHECKING:  # catalog imports this module; avoid the cycle at runtime
    from open_apps.compose.catalog import Catalog
    from open_apps.compose.command import Selection

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


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

#: Derived facts a `when` key may name besides config paths.
FACTS = {"task_app", "task_in_tasks", "target", "sweep_axes"}

_COMPARISON = re.compile(r"^(<=|>=|!=|<|>)\s*(.+)$")


@dataclass(frozen=True)
class Finding:
    """A rule that fires for a selection."""

    rule_id: str
    then: str
    reason: str
    fix: dict[str, Any] | None = None


def _rules(catalog: "Catalog") -> list[Rule]:
    return [Rule(**r) for r in catalog.rules]


def _yaml_scalar(raw: str) -> Any:
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def facts(selection: "Selection", catalog: "Catalog") -> dict[str, list[Any]]:
    """Every value a rule can test, as a list (several when the selection
    varies that axis). Effective values are layered: the target config's
    defaults, then each selected (or default) group option's
    :attr:`~open_apps.compose.catalog.Option.sets` in catalog order -- so
    ``+experiment`` overrides ``browsergym_env_args`` as it does at runtime --
    then the target script's own defaults, explicit scalars, and finally
    ``key=value`` extra overrides -- ``+key=value`` (add) and ``++key=value``
    (add or override) set the key just the same; ``~key`` deletes it, which
    is no value to test.
    """
    from open_apps.compose.catalog import TARGETS
    from open_apps.compose.command import (
        ALL_TASKS,
        CommandError,
        default_for,
        resolve_target,
    )

    try:
        target: str | None = resolve_target(selection, catalog)
    except CommandError:
        target = None
    effective_target = target or "single"
    config_name = TARGETS[effective_target].config_name
    choices = selection.choices

    out: dict[str, list[Any]] = {}
    for path in catalog.groups:
        out[path] = list(
            choices.get(path) or [default_for(path, effective_target, catalog)]
        )

    tracked = {p: [v] for p, v in catalog.baseline.items()}
    for path, scalar in catalog.scalars.items():
        tracked[path] = [scalar.defaults[config_name]]
    for path, group in catalog.groups.items():
        options = [o for o in group.options if o.name in out[path]]
        for key in {k for o in options for k in o.sets}:
            layered: list[Any] = []
            for option in options:
                layered += [option.sets[key]] if key in option.sets else tracked[key]
            tracked[key] = list(dict.fromkeys(layered))
    for key, value in TARGETS[effective_target].defaults.items():
        if key in tracked:
            tracked[key] = [value]
    for key, values in choices.items():
        if key in catalog.scalars:
            tracked[key] = list(values)
    out.update(tracked)

    for token in selection.extra:
        key, sep, raw = token.partition("=")
        key = key[2:] if key.startswith("++") else key.removeprefix("+")
        if sep and not key.startswith(("+", "~", "-")):
            out[key] = [_yaml_scalar(raw)]

    names = list(
        choices.get("task_name")
        or [default_for("task_name", effective_target, catalog)]
    )
    apps: set[str] = set()
    in_tasks = True
    for tasks_option in out["tasks"]:
        entries = {t.name: t for t in catalog.tasks.get(tasks_option, [])}
        wanted = list(entries) if names == [ALL_TASKS] else names
        for name in wanted:
            if name in entries:
                apps.update(entries[name].apps)
            else:
                in_tasks = False
    out["task_app"] = sorted(apps)
    out["task_in_tasks"] = [in_tasks]
    out["target"] = [target]
    # The sweep axes chosen away from their default: apps/* groups and
    # task_name, what a frozen suite (benchmark=...) pins itself.
    out["sweep_axes"] = [
        key
        for key, values in choices.items()
        if (
            key == "task_name"
            or (key in catalog.groups and catalog.groups[key].sweepable)
        )
        and values != [default_for(key, effective_target, catalog)]
    ]
    return out


def _matches_one(condition: Any, actual: Any) -> bool:
    if isinstance(condition, list):
        return any(_matches_one(c, actual) for c in condition)
    if condition is None:
        return actual is None
    if isinstance(condition, bool) or isinstance(actual, bool):
        # bool is an int in Python; never let True match 1.
        return (
            isinstance(condition, bool)
            and isinstance(actual, bool)
            and condition == actual
        )
    if isinstance(condition, str):
        match = _COMPARISON.match(condition)
        if match:
            op, raw = match.groups()
            if op == "!=":
                return str(actual) != raw
            try:
                a, b = float(actual), float(raw)
            except (TypeError, ValueError):
                return False
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    return condition == actual or str(condition) == str(actual)


def _holds(condition: Any, actuals: list[Any]) -> bool:
    """Any-semantics: a sweep cell or pool agent that trips the rule is enough."""
    return any(_matches_one(condition, a) for a in actuals)


def evaluate(
    selection: "Selection", catalog: "Catalog", rules: list[Rule] | None = None
) -> list[Finding]:
    """The rules that fire for ``selection``, in rules-file order."""
    known = facts(selection, catalog)
    found = []
    for rule in rules if rules is not None else _rules(catalog):
        if all(_holds(cond, known.get(key, [None])) for key, cond in rule.when.items()):
            found.append(Finding(rule.id, rule.then, rule.reason, rule.fix))
    return found


def apply_fix(
    selection: "Selection", fix: dict[str, Any], catalog: "Catalog"
) -> "Selection":
    """``selection`` with a rule's ``fix`` applied -- the one-click fix button.

    Group and scalar keys become choices; any other path replaces (or adds)
    the matching ``key=value`` extra override.
    """
    from open_apps.compose.command import format_value, normalize

    choices = {k: list(v) for k, v in selection.choices.items()}
    extra = list(selection.extra)
    for key, value in fix.items():
        if key in catalog.groups or key in catalog.scalars:
            choices[key] = [value]
        else:
            token = f"{key}={format_value(value)}"
            same = [i for i, t in enumerate(extra) if t.split("=", 1)[0] == key]
            if same:
                extra[same[-1]] = token  # the one Hydra applies (last wins)
            else:
                extra.append(token)
    return normalize(replace(selection, choices=choices, extra=extra), catalog)


def check_option(
    selection: "Selection", key: str, value: Any, catalog: "Catalog"
) -> list[Finding]:
    """What choosing ``key=value`` would trigger: the UI disables a pill when
    this includes a ``disable`` finding and shows its reason on hover."""
    choices = {**selection.choices, key: [value]}
    before = {f.rule_id for f in evaluate(selection, catalog)}
    return [
        f
        for f in evaluate(replace(selection, choices=choices), catalog)
        if f.rule_id not in before or key in _rule_keys(f.rule_id, catalog)
    ]


def _rule_keys(rule_id: str, catalog: "Catalog") -> set[str]:
    return next((set(r.when) for r in _rules(catalog) if r.id == rule_id), set())
