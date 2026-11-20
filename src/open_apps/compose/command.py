"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Render a composer selection to a launch command, and parse one back.

A :class:`Selection` is what the UI holds: chosen values per group/scalar
(several values on an axis means "vary this"), an optional forced target, a
repeat count and a parallelism cap, and whatever it did not understand, kept
verbatim. Two targets, picked automatically unless forced:

* one value per axis                       -> ``uv run launch_agent.py ...``
* anything else: several values on an
  ``apps/*`` axis, several tasks (or
  ``task_name=all``), several agents, a
  repeat count, a parallelism cap, or
  ``mode=slurm_cluster``                   -> ``uv run launch_parallel_agents.py
  [agents=a,b] parallel_tasks.app_variations=[[...]] parallel_tasks.task_names=[...]
  [parallel_tasks.repeats=n] [max_parallel=n]``

launch_parallel_agents.py is the one interface for parallel runs: several
agents are its ``agents=a,b`` selector, repeats are ``parallel_tasks.repeats``
(every job N times, seed fixed), and ``max_parallel`` runs that many local jobs
at once (SLURM schedules its own, so it is refused there).

Several ``tasks`` groups are not a sweep: they combine into one inventory,
``'tasks=[all_tasks,openbanking]'`` (catalog option order), on any target --
see :data:`open_apps.compose.catalog.COMBINABLE_GROUPS`.

A sweep on SLURM is ``launch_parallel_agents.py mode=slurm_cluster``
(submitit), a single run on SLURM a sweep of one job. Only non-default values
are emitted, with deliberate exceptions.
Sweeps always spell out ``app_variations`` and ``task_names``:
config_parallel_tasks.yaml's built-in lists are a demo grid, and
create_configs rebuilds each job's ``apps`` from config.yaml plus the cell's
overrides only, so an ``apps/*`` choice anywhere but inside every cell would
be silently dropped.

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
#: Targets an old share link may name: the worker pools the composer used to
#: render (``scripts/conduct*.sh``). Both load as the sweep, which now does
#: what they did -- several agents, repeats, a parallelism cap, SLURM.
LEGACY_TARGETS = ("pool", "slurm_pool")

#: A shell variable name. The one copy in Python: parse, the server's env
#: check and its argv split all use :data:`ENV_ASSIGNMENT` (the JS port spells
#: the same pattern; a test holds them together).
ENV_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
#: ``NAME=value`` -> (name, value); the value may be empty or span lines.
ENV_ASSIGNMENT = re.compile(rf"^({ENV_NAME})=(.*)$", re.DOTALL)
_OVERRIDE = re.compile(r"^(\+\+|\+|~)?([A-Za-z_][\w./@-]*)=(.*)$", re.DOTALL)


class CommandError(ValueError):
    """The selection (or command) cannot be expressed as one launch command."""


@dataclass
class Selection:
    """What the composer has selected. JSON-shaped; see :meth:`to_dict`."""

    #: group path or scalar path -> chosen values (several = a sweep axis).
    choices: dict[str, list[Any]] = field(default_factory=dict)
    #: Forced target; ``None`` picks one automatically (:func:`resolve_target`).
    target: str | None = None
    #: Sweeps: runs per job, seed fixed (``parallel_tasks.repeats``).
    repeats: int | None = None
    #: Local sweeps: jobs at once (``max_parallel``).
    max_parallel: int | None = None
    #: Hydra overrides the composer does not model, passed through verbatim.
    extra: list[str] = field(default_factory=list)
    #: ``NAME=value`` environment assignments it does not model.
    env: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """The JSON form, with empty fields omitted (what the fixtures store)."""
        out: dict[str, Any] = {}
        if self.target:
            out["target"] = self.target
        if self.choices:
            out["choices"] = {k: list(v) for k, v in self.choices.items()}
        for key in ("repeats", "max_parallel"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        for key in ("extra", "env"):
            if getattr(self, key):
                out[key] = list(getattr(self, key))
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Selection":
        """The selection ``data`` describes, upgrading the worker-pool form.

        A pool target loads as the sweep (``slurm_pool`` adds
        ``mode=slurm_cluster``); a pool's ``count`` -- total runs, round-robin
        over the agents -- becomes enough ``repeats`` per agent to cover it;
        ``sbatch_args`` are dropped (submitit reads the SLURM settings from
        ``mode``).
        """
        choices = {k: list(v) for k, v in (data.get("choices") or {}).items()}
        target = data.get("target")
        repeats = data.get("repeats")
        if target in LEGACY_TARGETS:
            if target == "slurm_pool":
                choices.setdefault("mode", ["slurm_cluster"])
            target = "sweep"
        count = data.get("count")
        if repeats is None and isinstance(count, int) and count > 1:
            agents = max(1, len(choices.get("agent") or []))
            repeats = -(-count // agents)  # ceil
        return cls(
            choices=choices,
            target=target,
            repeats=repeats,
            max_parallel=data.get("max_parallel"),
            extra=list(data.get("extra") or []),
            env=list(data.get("env") or []),
        )


# ---------------------------------------------------------------------------
# Targets and defaults
# ---------------------------------------------------------------------------


def combines(key: str, catalog: Catalog) -> bool:
    """Whether several values of ``key`` merge into one (``tasks=[a,b]``)
    rather than vary across runs."""
    group = catalog.groups.get(key)
    return group is not None and group.combinable


def _varied(choices: dict[str, list[Any]], catalog: Catalog) -> list[str]:
    """Keys with several values that vary across runs (not combined ones)."""
    return [k for k, v in choices.items() if len(v) > 1 and not combines(k, catalog)]


def task_names(selection: "Selection", target: str, catalog: Catalog) -> list[str]:
    """Every task the selected task groups hold -- what ``task_name=all``
    expands to: the union of the groups (or the target's default group)."""
    options = selection.choices.get("tasks") or [default_for("tasks", target, catalog)]
    return [t.name for t in catalog.tasks_in([o for o in options if o is not None])]


def _bound(choices: dict[str, list[Any]], catalog: Catalog) -> list[str]:
    """Chosen groups only some targets can bind (see ``Group.targets``)."""
    return [
        key
        for key, values in choices.items()
        if key in catalog.groups
        and len(catalog.groups[key].targets) < len(TARGETS)
        and any(v is not None for v in values)
    ]


def suite(selection: Selection, catalog: Catalog) -> str | None:
    """The chosen group that pins the sweep (``benchmark``), if any."""
    return next(
        (k for k in _bound(selection.choices, catalog) if catalog.groups[k].pins_sweep),
        None,
    )


def resolve_target(selection: Selection, catalog: Catalog) -> str:
    """Which command runs this selection: single or sweep.

    A group only one target binds (``benchmark``: the sweep) picks that one.
    Anything parallel -- several values on an axis, repeats, a parallelism
    cap -- and anything on SLURM is the sweep: launch_agent.py runs one
    episode in this process.
    """
    choices = selection.choices
    if selection.target:
        if selection.target not in TARGETS:
            raise CommandError(
                f"unknown target {selection.target!r}; one of {', '.join(TARGETS)}"
            )
        return selection.target
    bound = _bound(choices, catalog)
    if bound:
        return catalog.groups[bound[0]].targets[0]
    if (
        _varied(choices, catalog)
        or choices.get("task_name") == [ALL_TASKS]
        or (selection.repeats or 0) > 1
        or (selection.max_parallel or 0) > 1
        or "slurm_cluster" in choices.get("mode", [])
    ):
        return "sweep"
    return "single"


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
        if unique and combines(key, catalog):
            # One inventory whatever order it was picked in: the catalog's.
            order = [o.name for o in catalog.groups[key].options]
            unique.sort(key=lambda v: order.index(v) if v in order else len(order))
        if unique:
            choices[key] = unique
    sel = replace(selection, choices=choices)
    # 1 is each one's default (config_parallel_tasks.yaml), so it says nothing.
    if sel.repeats is not None and sel.repeats <= 1:
        sel = replace(sel, repeats=None)
    if sel.max_parallel is not None and sel.max_parallel <= 1:
        sel = replace(sel, max_parallel=None)

    target = resolve_target(sel, catalog)
    if target != "sweep":
        sel = replace(sel, repeats=None, max_parallel=None)

    for key in list(choices):
        values = choices[key]
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


def _choice_override(key: str, values: list[Any], catalog: Catalog) -> str:
    """The one override for a key's values: ``key=v``, or ``key=[a,b]`` for
    a combined group (Hydra composes every option of the list)."""
    if len(values) > 1 and combines(key, catalog):
        return _override(key, _hydra_list([str(v) for v in values], False), catalog)
    return _override(key, values[0], catalog)


def _ordered_keys(choices: dict[str, list[Any]], catalog: Catalog) -> list[str]:
    known = [k for k in catalog.groups if k in choices]
    known += [k for k in catalog.scalars if k in choices]
    return known + [k for k in choices if k not in known]


def _check_expressible(sel: Selection, target: str, catalog: Catalog) -> None:
    for key in sel.choices:
        if key not in catalog.groups and key not in catalog.scalars:
            raise CommandError(f"unknown key {key!r}; pass it as an extra override")
    varied = _varied(sel.choices, catalog)
    if target == "single":
        allowed: set[str] = set()
    else:
        allowed = {k for k, g in catalog.groups.items() if g.sweepable}
        allowed |= {"task_name", "agent"}
    for key in varied:
        if key in allowed:
            continue
        if key in catalog.groups and target == "sweep":
            why = (
                "a sweep varies only apps/* groups, task_name and the agent -- "
                "create_configs copies just `apps` out of each cell"
            )
        else:
            why = f"a {TARGETS[target].label.lower()} takes one value per axis"
        raise CommandError(f"cannot vary {key!r}: {why}")
    if sel.choices.get("task_name") == [ALL_TASKS] and target != "sweep":
        raise CommandError("task_name=all needs the sweep target")
    for key in _bound(sel.choices, catalog):
        targets = catalog.groups[key].targets
        if target not in targets:
            labels = " or a ".join(TARGETS[t].label.lower() for t in targets)
            raise CommandError(f"{key} runs only as a {labels}")
    pinned = suite(sel, catalog)
    if pinned:
        axes = [
            k
            for k in sel.choices
            if k == "task_name" or (k in catalog.groups and catalog.groups[k].sweepable)
        ]
        if axes:
            raise CommandError(
                f"{pinned}={sel.choices[pinned][0]}: the frozen suite pins the tasks "
                f"and app variations; drop {', '.join(axes)}"
            )
    if sel.max_parallel is not None and "slurm_cluster" in sel.choices.get("mode", []):
        raise CommandError(
            "max_parallel runs local jobs at once; on SLURM the scheduler decides"
        )


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

    overrides: list[str] = []
    for key in _ordered_keys(sel.choices, catalog):
        values = sel.choices[key]
        if key == "agent" and len(values) > 1:
            # launch_parallel_agents.py's selector: every job once per agent.
            overrides.append("agents=" + ",".join(values))
        elif target == "sweep" and (
            key == "task_name"
            or (key in catalog.groups and catalog.groups[key].sweepable)
        ):
            continue  # inside app_variations / task_names below
        else:
            overrides.append(_choice_override(key, values, catalog))

    if target == "sweep" and not suite(sel, catalog):
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
    if sel.repeats is not None:
        overrides.append(f"parallel_tasks.repeats={sel.repeats}")
    if sel.max_parallel is not None:
        overrides.append(f"max_parallel={sel.max_parallel}")

    program = TARGETS[target].command.split()
    words = [shlex.quote(t) for t in [*program, *overrides, *sel.extra]]
    env_words = [_quote_assignment(a) for a in sel.env]
    return " ".join(env_words + words)


def _quote_assignment(assignment: str) -> str:
    # Quote the value only: a quoted `'NAME=value'` is a command word to the
    # shell, not an assignment.
    name, value = assignment.split("=", 1)
    return f"{name}={shlex.quote(value)}"


def run_count(selection: Selection, catalog: Catalog) -> int | None:
    """How many episodes the rendered command launches; None when a frozen
    suite decides (its blocks are not in the catalog)."""
    sel = normalize(selection, catalog)
    target = resolve_target(sel, catalog)
    if suite(sel, catalog):
        return None
    if target == "sweep":
        names = sel.choices.get("task_name") or [None]
        if names == [ALL_TASKS]:
            names = task_names(sel, target, catalog)
        agents = len(sel.choices.get("agent") or [None])
        return len(_sweep_cells(sel, catalog)) * len(names) * agents * (sel.repeats or 1)
    return 1


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

_SCRIPTS = {
    "launch_agent.py": "single",
    "launch_parallel_agents.py": "sweep",
}
#: The worker-pool scripts the composer no longer writes, and what replaced them.
_RETIRED_SCRIPTS = ("conduct.sh", "conduct_slurm.sh")
_AGENT_LIST = re.compile(r"^[\w.\-]+(,[\w.\-]+)+$")


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
    if script in _RETIRED_SCRIPTS:
        raise CommandError(
            f"{tokens[i]} is a worker pool; the composer runs parallel jobs with "
            "launch_parallel_agents.py (agents=a,b, parallel_tasks.repeats=N, "
            "max_parallel=N, mode=slurm_cluster)"
        )
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
    while tokens and ENV_ASSIGNMENT.match(tokens[0]):
        name, value = ENV_ASSIGNMENT.match(tokens[0]).groups()
        env.append((name, value))
        tokens = tokens[1:]
    target, sbatch_args, rest = _split_program(tokens)
    if sbatch_args:
        raise CommandError(
            "sbatch submits a script; a sweep goes to SLURM with mode=slurm_cluster"
        )

    sel = Selection(env=[f"{name}={value}" for name, value in env])
    choices = sel.choices
    for token in rest:
        if not (
            _parse_run_setting(token, target, sel, catalog)
            or _parse_override(token, target, choices, catalog)
        ):
            sel.extra.append(token)
    if sel.max_parallel is not None and "slurm_cluster" in choices.get("mode", []):
        # Not something the composer can show (it refuses the pair): keep it
        # as the launcher would see it.
        sel.extra.append(f"max_parallel={sel.max_parallel}")
        sel.max_parallel = None

    auto = resolve_target(sel, catalog)
    sel.target = None if auto == target else target
    return normalize(sel, catalog)


def _positive_int(raw: str) -> int | None:
    return int(raw) if raw.isdigit() and int(raw) >= 1 else None


def _parse_run_setting(
    token: str, target: str, sel: Selection, catalog: Catalog
) -> bool:
    """Record a sweep's ``agents=``, ``parallel_tasks.repeats=`` or
    ``max_parallel=`` in ``sel``; False for anything else (or a repeat)."""
    if target != "sweep":
        return False
    key, sep, raw = token.partition("=")
    if not sep:
        return False
    if key == "agents" and "agent" not in sel.choices and _AGENT_LIST.match(raw):
        names = raw.split(",")
        known = {o.name for o in catalog.groups["agent"].options}
        if len(set(names)) == len(names) and all(n in known for n in names):
            sel.choices["agent"] = names
            return True
        return False
    if key == "parallel_tasks.repeats" and sel.repeats is None:
        sel.repeats = _positive_int(raw)
        return sel.repeats is not None
    if key == "max_parallel" and sel.max_parallel is None:
        sel.max_parallel = _positive_int(raw)
        return sel.max_parallel is not None
    return False


def _combined_values(token: str, raw: str) -> list[str]:
    """``[a,b]`` (Hydra's list grammar) -> its options; a plain value is one.
    Anything else -- an empty or nested list, a sweep -- is no values, so the
    token stays verbatim."""
    if not raw.startswith("["):
        return [raw]
    try:
        value = _hydra_value(token)
    except HydraException:
        return []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    return []


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
        if target not in group.targets:
            return False  # this script's config cannot bind it; keep it visible
        if group.sweepable and target == "sweep":
            return False  # a no-op outside app_variations; keep it visible
        if prefix != ("+" if group.append else ""):
            return False
        values = _combined_values(token, raw) if group.combinable else [raw]
        names = {o.name for o in group.options}
        if not values or any(v not in names for v in values):
            return False
        choices[key] = values
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
