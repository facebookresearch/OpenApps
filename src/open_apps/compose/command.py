"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Render a composer selection to a launch command, and parse one back.

A :class:`Selection` is what the UI holds: chosen values per group/scalar
(several values on an axis means "vary this"), an optional forced target, a
repeat count, and whatever it did not understand, kept verbatim. Four targets,
picked automatically unless forced:

* one value per axis                       -> ``uv run launch_agent.py ...``
* several values on an ``apps/*`` axis or
  several tasks (or ``task_name=all``)     -> ``uv run launch_parallel_agents.py
  parallel_tasks.app_variations=[[...]] parallel_tasks.task_names=[...]``
* several agents, a repeat count or a
  parallelism cap                          -> ``AGENTS=... COUNT=n ./scripts/conduct.sh``
* any of the above (but a sweep) with
  ``mode=slurm_cluster``                   -> ``AGENTS=... sbatch scripts/conduct_slurm.sh``

A sweep on SLURM stays ``launch_parallel_agents.py mode=slurm_cluster``
(submitit). Only non-default values are emitted, with two deliberate
exceptions in sweeps, which always spell out ``app_variations`` and
``task_names``: config_parallel_tasks.yaml's built-in lists are a demo grid,
and create_configs rebuilds each job's ``apps`` from config.yaml plus the
cell's overrides only, so an ``apps/*`` choice anywhere but inside every cell
would be silently dropped.

``parse(render(s)) == s`` for any normalized selection; ``render(parse(cmd))``
keeps every token of ``cmd`` except values the target applies by default.
"""

import itertools
import re
import shlex
from dataclasses import dataclass, field, replace
from typing import Any

from hydra.core.override_parser.overrides_parser import OverridesParser
from hydra.errors import HydraException

from open_apps.compose.catalog import TARGETS, Catalog

ALL_TASKS = "all"  # mirrors open_apps.tasks.parallel_tasks.ALL_TASKS
POOL_TARGETS = ("pool", "slurm_pool")

_ENV_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
_OVERRIDE = re.compile(r"^(\+\+|\+|~)?([A-Za-z_][\w./@-]*)=(.*)$", re.DOTALL)


class CommandError(ValueError):
    """The selection (or command) cannot be expressed as one launch command."""


@dataclass
class Selection:
    """What the composer has selected. JSON-shaped; see :meth:`to_dict`."""

    #: group path or scalar path -> chosen values (several = sweep / pool).
    choices: dict[str, list[Any]] = field(default_factory=dict)
    #: Forced target; ``None`` picks one automatically (:func:`resolve_target`).
    target: str | None = None
    #: Worker pools: total runs, round-robin over the agents (``COUNT``).
    count: int | None = None
    #: Worker pools: concurrent runs (``MAX_PARALLEL``).
    max_parallel: int | None = None
    #: Hydra overrides the composer does not model, passed through verbatim.
    extra: list[str] = field(default_factory=list)
    #: ``NAME=value`` environment assignments it does not model.
    env: list[str] = field(default_factory=list)
    #: ``sbatch`` options (``--account=...``) for the SLURM worker pool.
    sbatch_args: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """The JSON form, with empty fields omitted (what the fixtures store)."""
        out: dict[str, Any] = {}
        if self.target:
            out["target"] = self.target
        if self.choices:
            out["choices"] = {k: list(v) for k, v in self.choices.items()}
        for key in ("count", "max_parallel"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        for key in ("extra", "env", "sbatch_args"):
            if getattr(self, key):
                out[key] = list(getattr(self, key))
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Selection":
        return cls(
            choices={k: list(v) for k, v in (data.get("choices") or {}).items()},
            target=data.get("target"),
            count=data.get("count"),
            max_parallel=data.get("max_parallel"),
            extra=list(data.get("extra") or []),
            env=list(data.get("env") or []),
            sbatch_args=list(data.get("sbatch_args") or []),
        )


# ---------------------------------------------------------------------------
# Targets and defaults
# ---------------------------------------------------------------------------


def resolve_target(selection: Selection, catalog: Catalog) -> str:
    """Which command runs this selection: single, sweep, pool or slurm_pool."""
    choices = selection.choices
    slurm = "slurm_cluster" in choices.get("mode", [])
    if selection.target:
        if selection.target not in TARGETS:
            raise CommandError(
                f"unknown target {selection.target!r}; one of {', '.join(TARGETS)}"
            )
        if selection.target == "pool" and slurm:
            # The Worker pool pill on the SLURM mode (see Target.pill).
            return "slurm_pool"
        return selection.target
    if (
        len(choices.get("agent", [])) > 1
        or (selection.count or 0) > 1
        or selection.max_parallel is not None
    ):
        return "slurm_pool" if slurm else "pool"
    varied = {k for k, v in choices.items() if len(v) > 1 and k != "agent"}
    if varied or choices.get("task_name") == [ALL_TASKS]:
        return "sweep"
    return "slurm_pool" if slurm else "single"


def default_for(key: str, target: str, catalog: Catalog) -> Any:
    """The value ``key`` takes under ``target`` when the selection leaves it out."""
    spec = TARGETS[target]
    if key in spec.defaults:
        return spec.defaults[key]
    if key == "task_name":
        # Sweeps name their tasks explicitly (see the module docstring); with
        # nothing selected they run config.yaml's default task, like a single
        # run, rather than config_parallel_tasks' demo list.
        return catalog.scalars["task_name"].defaults["config"]
    if key in catalog.groups:
        group = catalog.groups[key]
        # A sweep composes each cell's apps against config.yaml, whatever the
        # sweep config's own defaults say (create_configs).
        config = "config" if group.sweepable else spec.config_name
        return group.defaults[config]
    if key in catalog.scalars:
        return catalog.scalars[key].defaults[spec.config_name]
    return None


def normalize(selection: Selection, catalog: Catalog) -> Selection:
    """Canonical form: dedupe values, drop defaults and redundant fields.

    Two selections that render the same command normalize to the same value,
    which is what makes ``parse(render(s)) == s`` checkable.
    """
    choices: dict[str, list[Any]] = {}
    for key, values in selection.choices.items():
        unique = list(dict.fromkeys(values))
        if unique:
            choices[key] = unique
    sel = replace(selection, choices=choices)
    if sel.target == "slurm_pool":
        # Not a pill: spell it the way the UI does, Worker pool + the SLURM
        # mode. Keeps old `#target=slurm_pool` links working.
        choices.setdefault("mode", ["slurm_cluster"])
        sel = replace(sel, target="pool")

    target = resolve_target(sel, catalog)
    if target == "slurm_pool" and "mode" not in choices:
        # The canonical way to say "SLURM worker pool" is mode=slurm_cluster.
        choices["mode"] = ["slurm_cluster"]
    if target not in POOL_TARGETS:
        sel = replace(sel, count=None, max_parallel=None, sbatch_args=[])
    if target != "slurm_pool":
        sel = replace(sel, sbatch_args=[])

    for key in list(choices):
        values = choices[key]
        if key == "mode" and target in POOL_TARGETS:
            # mode picks the pool script; slurm_cluster is how auto-selection
            # finds the SLURM pool, so it stays even though it is not emitted.
            if values == ["local"]:
                del choices[key]
            continue
        if len(values) == 1 and values[0] == default_for(key, target, catalog):
            del choices[key]

    sel = replace(sel, choices=choices)
    if sel.target and resolve_target(replace(sel, target=None), catalog) == target:
        sel = replace(sel, target=None)
    return sel


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"  # the repo's own spelling
    return str(value)


def _override(key: str, value: Any, catalog: Catalog) -> str:
    group = catalog.groups.get(key)
    prefix = "+" if group is not None and group.append else ""
    return f"{prefix}{key}={format_value(value)}"


def _ordered_keys(choices: dict[str, list[Any]], catalog: Catalog) -> list[str]:
    known = [k for k in catalog.groups if k in choices]
    known += [k for k in catalog.scalars if k in choices]
    return known + [k for k in choices if k not in known]


def _check_expressible(sel: Selection, target: str, catalog: Catalog) -> None:
    for key in sel.choices:
        if key not in catalog.groups and key not in catalog.scalars:
            raise CommandError(f"unknown key {key!r}; pass it as an extra override")
    varied = [k for k, v in sel.choices.items() if len(v) > 1]
    if target == "single":
        allowed: set[str] = set()
    elif target == "sweep":
        allowed = {k for k, g in catalog.groups.items() if g.sweepable} | {"task_name"}
    else:
        allowed = {"agent"}
    for key in varied:
        if key in allowed:
            continue
        if key == "agent":
            why = "several agents run as a worker pool, which cannot also sweep"
        elif target in POOL_TARGETS:
            why = "a worker pool varies only the agent; run one sweep per agent"
        elif key in catalog.groups and target == "sweep":
            why = (
                "a sweep varies only apps/* groups and task_name -- create_configs "
                "copies just `apps` out of each cell"
            )
        else:
            why = f"a {TARGETS[target].label.lower()} takes one value per axis"
        raise CommandError(f"cannot vary {key!r}: {why}")
    if sel.choices.get("task_name") == [ALL_TASKS] and target != "sweep":
        raise CommandError("task_name=all needs the sweep target")
    if target in POOL_TARGETS:
        expected = "slurm_cluster" if target == "slurm_pool" else "local"
        if sel.choices.get("mode", [expected]) != [expected]:
            raise CommandError(f"mode must be {expected!r} for the {target} target")


def _hydra_list(items: list[str], quote: bool) -> str:
    return "[" + ",".join(f'"{i}"' if quote else i for i in items) + "]"


def _sweep_cells(sel: Selection, catalog: Catalog) -> list[list[str]]:
    axes = [
        [_override(path, v, catalog) for v in sel.choices[path]]
        for path, group in catalog.groups.items()
        if group.sweepable and path in sel.choices
    ]
    return [list(cell) for cell in itertools.product(*axes)]


def _has_extra(sel: Selection, key: str) -> bool:
    return any(token.split("=", 1)[0] == key for token in sel.extra)


def render(selection: Selection, catalog: Catalog) -> str:
    """The shell command for ``selection`` (normalized first)."""
    sel = normalize(selection, catalog)
    target = resolve_target(sel, catalog)
    _check_expressible(sel, target, catalog)

    env: list[str] = []
    overrides: list[str] = []
    pool = target in POOL_TARGETS
    for key in _ordered_keys(sel.choices, catalog):
        values = sel.choices[key]
        if pool and key == "agent":
            env.append("AGENTS=" + " ".join(values))
        elif pool and key == "mode":
            continue  # chose the script
        elif pool and key in catalog.scalars and catalog.scalars[key].pool_env:
            env.append(f"{catalog.scalars[key].pool_env}={format_value(values[0])}")
        elif target == "sweep" and (
            key == "task_name"
            or (key in catalog.groups and catalog.groups[key].sweepable)
        ):
            continue  # inside app_variations / task_names below
        else:
            overrides.append(_override(key, values[0], catalog))

    if pool:
        if sel.count is not None:
            env.append(f"COUNT={sel.count}")
        if sel.max_parallel is not None:
            env.append(f"MAX_PARALLEL={sel.max_parallel}")
        # AGENTS, COUNT, MAX_PARALLEL, HEADLESS: the order conduct.sh reads them.
        order = ("AGENTS=", "COUNT=", "MAX_PARALLEL=", "HEADLESS=")
        env.sort(key=lambda a: next(i for i, p in enumerate(order) if a.startswith(p)))

    if target == "sweep":
        if not _has_extra(sel, "parallel_tasks.app_variations"):
            cells = _sweep_cells(sel, catalog)
            value = "[" + ",".join(_hydra_list(c, quote=True) for c in cells) + "]"
            overrides.append(f"parallel_tasks.app_variations={value}")
        if not _has_extra(sel, "parallel_tasks.task_names"):
            names = sel.choices.get("task_name") or [
                default_for("task_name", target, catalog)
            ]
            value = ALL_TASKS if names == [ALL_TASKS] else _hydra_list(names, False)
            overrides.append(f"parallel_tasks.task_names={value}")

    program = TARGETS[target].command.split()
    if target == "slurm_pool":
        program = [program[0], *sel.sbatch_args, *program[1:]]
    words = [shlex.quote(t) for t in [*program, *overrides, *sel.extra]]
    env_words = [_quote_assignment(a) for a in env + sel.env]
    return " ".join(env_words + words)


def _quote_assignment(assignment: str) -> str:
    # Quote the value only: a quoted `'NAME=value'` is a command word to the
    # shell, not an assignment.
    name, value = assignment.split("=", 1)
    return f"{name}={shlex.quote(value)}"


def run_count(selection: Selection, catalog: Catalog) -> int:
    """How many episodes the rendered command launches."""
    sel = normalize(selection, catalog)
    target = resolve_target(sel, catalog)
    if target in POOL_TARGETS:
        if sel.count is not None:
            return sel.count
        agents = len(sel.choices.get("agent", [])) or 1
        return agents if target == "pool" else 1  # conduct_slurm.sh: COUNT=1
    if target == "sweep":
        names = sel.choices.get("task_name") or [None]
        if names == [ALL_TASKS]:
            tasks_option = sel.choices.get("tasks", [None])[0] or default_for(
                "tasks", target, catalog
            )
            names = catalog.tasks[tasks_option]
        return len(_sweep_cells(sel, catalog)) * len(names)
    return 1


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

_SCRIPTS = {
    "launch_agent.py": "single",
    "launch_parallel_agents.py": "sweep",
    "conduct.sh": "pool",
    "conduct_slurm.sh": "slurm_pool",
}
_POOL_ENV = ("AGENTS", "COUNT", "MAX_PARALLEL")


def _parse_scalar(raw: str, kind: str) -> Any:
    if kind == "bool":
        if raw.lower() in ("true", "false"):
            return raw.lower() == "true"
        raise ValueError(raw)
    if kind == "int":
        return int(raw)
    return raw


def _hydra_value(token: str) -> Any:
    """The value of one override, parsed by Hydra's own grammar."""
    return OverridesParser.create().parse_override(token).value()


def _split_program(tokens: list[str]) -> tuple[str, list[str], list[str]]:
    """-> (target, sbatch options, remaining tokens)."""
    i = 0
    if tokens[:2] == ["uv", "run"]:
        i = 2
    elif tokens and tokens[0] in ("python", "python3", "bash", "sh"):
        i = 1
    sbatch: list[str] = []
    if tokens[i : i + 1] == ["sbatch"]:
        i += 1
        while i < len(tokens) and tokens[i].startswith("-"):
            sbatch.append(tokens[i])
            i += 1
    if i >= len(tokens):
        raise CommandError("no launch script found")
    script = tokens[i].rsplit("/", 1)[-1]
    if script not in _SCRIPTS:
        raise CommandError(f"unknown launch script {tokens[i]!r}")
    return _SCRIPTS[script], sbatch, tokens[i + 1 :]


def _decompose_cells(cells: Any, catalog: Catalog) -> dict[str, list[str]] | None:
    """Recover per-axis choices from app_variations, or None if it is not
    exactly what :func:`_sweep_cells` would render for some choices."""
    if not isinstance(cells, list) or not cells:
        return None
    if cells == [[]]:
        return {}
    keys: list[str] | None = None
    values: dict[str, list[str]] = {}
    for cell in cells:
        if not isinstance(cell, list):
            return None
        cell_keys = []
        for item in cell:
            match = _OVERRIDE.match(str(item))
            if not match:
                return None
            prefix, key, value = match.groups()
            group = catalog.groups.get(key)
            if (
                group is None
                or not group.sweepable
                or (prefix or "") != ("+" if group.append else "")
                or value not in {o.name for o in group.options}
            ):
                return None
            cell_keys.append(key)
            values.setdefault(key, [])
            if value not in values[key]:
                values[key].append(value)
        if keys is None:
            keys = cell_keys
        elif keys != cell_keys:
            return None
    choices = {k: values[k] for k in keys or []}
    rebuilt = _sweep_cells(Selection(choices=choices), catalog)
    return choices if rebuilt == [[str(i) for i in c] for c in cells] else None


def parse(command: str, catalog: Catalog) -> Selection:
    """Parse a launch command into a normalized :class:`Selection`.

    Anything not modelled -- unknown keys, a value that is not an option,
    a repeated key (Hydra lets the last one win), hand-written app variations
    that are not a plain product -- is kept verbatim in ``extra``/``env``.
    """
    tokens = shlex.split(command.replace("\\\n", " "))
    env: list[tuple[str, str]] = []
    while tokens and _ENV_ASSIGNMENT.match(tokens[0]):
        name, value = _ENV_ASSIGNMENT.match(tokens[0]).groups()
        env.append((name, value))
        tokens = tokens[1:]
    target, sbatch_args, rest = _split_program(tokens)
    pool = target in POOL_TARGETS

    sel = Selection(sbatch_args=sbatch_args)
    choices = sel.choices
    for name, value in env:
        if pool and name == "AGENTS" and "agent" not in choices:
            choices["agent"] = value.split()
        elif pool and name in ("COUNT", "MAX_PARALLEL") and value.isdigit():
            setattr(sel, name.lower(), int(value))
        elif pool and name == "HEADLESS" and value.lower() in ("true", "false"):
            choices["browsergym_env_args.headless"] = [value.lower() == "true"]
        else:
            sel.env.append(f"{name}={value}")
    if target == "slurm_pool":
        choices["mode"] = ["slurm_cluster"]

    for token in rest:
        if not _parse_override(token, target, choices, catalog):
            sel.extra.append(token)

    auto = resolve_target(sel, catalog)
    sel.target = None if auto == target else target
    return normalize(sel, catalog)


def _parse_override(
    token: str, target: str, choices: dict[str, list[Any]], catalog: Catalog
) -> bool:
    """Record ``token`` in ``choices`` if it is a modelled override."""
    match = _OVERRIDE.match(token)
    if not match:
        return False
    prefix, key, raw = match.groups()
    prefix = prefix or ""

    if target == "sweep" and prefix == "" and key.startswith("parallel_tasks."):
        try:
            value = _hydra_value(token)
        except HydraException:  # not valid override grammar: keep it verbatim
            return False
        if key == "parallel_tasks.app_variations":
            axes = _decompose_cells(value, catalog)
            if axes is None or any(k in choices for k in axes):
                return False
            choices.update(axes)
            return True
        if key == "parallel_tasks.task_names" and "task_name" not in choices:
            if value == ALL_TASKS:
                choices["task_name"] = [ALL_TASKS]
                return True
            if (
                isinstance(value, list)
                and value
                and all(isinstance(v, str) for v in value)
            ):
                choices["task_name"] = list(value)
                return True
        return False

    if key in choices:
        return False  # repeated: keep the later one verbatim, Hydra applies it
    if key in catalog.groups:
        group = catalog.groups[key]
        if group.sweepable and target == "sweep":
            return False  # a no-op outside app_variations; keep it visible
        if prefix != ("+" if group.append else ""):
            return False
        if raw not in {o.name for o in group.options}:
            return False
        if key == "mode" and target in POOL_TARGETS:
            return False  # the script decides; a forwarded mode is just an extra
        choices[key] = [raw]
        return True
    if key in catalog.scalars and prefix == "":
        scalar = catalog.scalars[key]
        if key == "task_name" and target == "sweep":
            return False  # create_configs sets it per job
        try:
            choices[key] = [_parse_scalar(raw, scalar.type)]
        except ValueError:
            return False
        return True
    return False
