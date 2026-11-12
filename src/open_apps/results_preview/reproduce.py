"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

How a model's runs were set up, and the command that runs them again.

:func:`setup_from_config` reduces a job's saved ``config.yaml`` (already cut
down to a few top-level keys by :func:`open_apps.results_preview.runs.read_config`)
to a small, hashable :class:`Setup`, once per config file. The drill-down then
shows, from the runs of one model:

* :func:`commands` -- ``launch_parallel_agents.py`` invocations that re-run
  exactly those (task, app variation) cells, one per distinct configuration
  and per block of tasks sharing the same variations;
* :func:`configuration` -- a readable summary (agent, episodes, sweep, code),
  where a value that differs across the runs reads ``varies: a, b``.
"""

from __future__ import annotations

import dataclasses
import functools
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence

import yaml

if TYPE_CHECKING:  # pragma: no cover
    from open_apps.results_preview.runs import Run

LAUNCHER = "uv run launch_parallel_agents.py"
#: Hydra's record of a run's command-line overrides, when it wrote one.
HYDRA_OVERRIDES = Path(".hydra") / "overrides.yaml"
#: Config keys that may hold the code version a run was launched from.
COMMIT_KEYS = ("git_commit", "git_hash", "commit")
#: How main ends an episode when the config names no termination rule
#: (``add_tasks_to_browsergym.validate`` and BrowserGym's own actions).
DEFAULT_TERMINATION = "task success or report_infeasible (else at max steps)"
#: The repo's agent configs, to name one the runs' config did not record.
AGENT_CONFIGS = Path(__file__).resolve().parents[3] / "config" / "agent"
#: Stands in for an agent config name nothing recorded or matched.
UNKNOWN_AGENT = "AGENT_CONFIG"

_CLIENTS = {"vllm": "vLLM", "dummy": "dummy"}
_OBSERVATIONS = (
    ("use_screenshot", "screenshot"),
    ("use_axtree", "AXTree"),
    ("use_html", "HTML"),
    ("save_som", "set-of-marks"),
)
_BARE = re.compile(r"[A-Za-z_][A-Za-z0-9_\-./:@]*")
_NUMBER = re.compile(r"-?\d+(\.\d+)?")
_HYDRA_WORDS = {"true", "false", "null", "nan", "inf"}


@dataclass(frozen=True)
class Setup:
    """What one job's config.yaml records about how its run was set up.
    ``None`` means not recorded."""

    agent_config: Optional[str] = None
    task_config: Optional[str] = None
    model_name: Optional[str] = None
    pretty_name: Optional[str] = None
    client: Optional[str] = None
    observation: Optional[tuple[str, ...]] = None
    history: Optional[bool] = None
    temperature: Optional[str] = None  # display text; "default" for null
    max_tokens: Optional[str] = None  # display text; "none" for null
    coord_scale: Optional[str] = None
    action_parser: Optional[str] = None
    hostname: Optional[str] = None
    seed: Optional[str] = None
    task_seed: Optional[str] = None
    max_steps: Optional[str] = None
    device: Optional[str] = None
    viewport: Optional[tuple[int, ...]] = None
    termination: Optional[str] = None
    cluster: Optional[str] = None
    commit: Optional[str] = None
    #: Hydra's recorded command-line overrides, verbatim, if it saved them.
    overrides: Optional[tuple[str, ...]] = None


#: Setup fields a sweep's app variation can set (as ``key=value`` overrides),
#: with their override key: runs differing only there are one configuration.
_VARIATION_KEYS = {
    "device": "device",
    "viewport": "device",
    "seed": "seed",
    "task_seed": "browsergym_env_args.task_seed",
    "max_steps": "browsergym_env_args.max_steps",
}
#: Setup fields that become command-line overrides, in command order. Runs
#: that differ in any of them need separate commands.
_COMMAND_FIELDS = (
    "overrides", "agent_config", "model_name", "task_config", "seed", "task_seed",
    "max_steps", "device", "hostname",
)  # fmt: skip
_FIELD_NAMES = {
    "overrides": "recorded overrides",
    "agent_config": "agent config",
    "model_name": "model",
    "task_config": "task catalog",
    "task_seed": "task seed",
    "max_steps": "max steps",
    "hostname": "model server",
}


def _text(value: Any) -> Optional[str]:
    return None if value is None or value == "" else str(value)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def setup_from_config(config: dict, config_path: Optional[Path] = None) -> Setup:
    """The :class:`Setup` a job's (partial) config records. ``config_path``
    locates Hydra's ``.hydra/overrides.yaml``, if the run dir has one."""
    agent = _dict(config.get("agent"))
    env = _dict(config.get("browsergym_env_args"))
    device = _dict(config.get("device")) or _dict(env.get("device"))
    observation = None
    if any(key in agent for key, _ in _OBSERVATIONS):
        observation = tuple(label for key, label in _OBSERVATIONS if agent.get(key))
    history = agent.get("use_history")
    viewport = device.get("viewport")
    termination = config.get("task_termination") or _dict(env.get("task_kwargs")).get(
        "task_termination"
    )
    client = _text(agent.get("client_type"))
    return Setup(
        agent_config=_text(config.get("agent_config_name")),
        task_config=_text(config.get("task_config_name")),
        model_name=_text(agent.get("model_name")),
        pretty_name=_text(agent.get("model_pretty_name")),
        client=_CLIENTS.get(client, f"API ({client})") if client else None,
        observation=observation,
        history=history if isinstance(history, bool) else None,
        temperature=_null_as(agent, "temperature", "default"),
        max_tokens=_null_as(agent, "max_tokens", "none"),
        coord_scale=_text(agent.get("coord_scale")),
        action_parser=_text(agent.get("action_parser")),
        hostname=_text(agent.get("hostname")),
        seed=_text(config.get("seed")),
        task_seed=_text(env.get("task_seed")),
        max_steps=_text(env.get("max_steps")),
        device=_text(device.get("name")),
        viewport=tuple(viewport) if isinstance(viewport, list) else None,
        termination=_text(termination),
        cluster=_text(config.get("cluster")),
        commit=next((_text(config[k]) for k in COMMIT_KEYS if config.get(k)), None),
        overrides=_hydra_overrides(config_path) if config_path else None,
    )


def _null_as(mapping: dict, key: str, null: str) -> Optional[str]:
    if key not in mapping:
        return None
    return null if mapping[key] is None else str(mapping[key])


@functools.lru_cache(maxsize=4096)
def _hydra_overrides(config_path: Path) -> Optional[tuple[str, ...]]:
    """The overrides Hydra saved beside the job's config, or in the run dir
    above it (``hydra.output_subdir`` set)."""
    for directory in (config_path.parent, config_path.parent.parent):
        path = directory / HYDRA_OVERRIDES
        if path.is_file():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                return None
            if isinstance(data, list):
                return tuple(str(item) for item in data)
    return None


@functools.lru_cache(maxsize=256)
def guess_agent_config(model_name: Optional[str], pretty_name: Optional[str]) -> Optional[str]:
    """The one config in the repo's ``config/agent/`` with this model's
    pretty name (else model name), for configs that predate
    ``agent_config_name``. None when there is no single match."""
    if not AGENT_CONFIGS.is_dir():
        return None
    found: dict[str, list[str]] = {"pretty": [], "model": []}
    for path in sorted(AGENT_CONFIGS.glob("*.yaml")):
        try:
            data = _dict(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError):
            continue
        if pretty_name and data.get("model_pretty_name") == pretty_name:
            found["pretty"].append(path.stem)
        if model_name and data.get("model_name") == model_name:
            found["model"].append(path.stem)
    for kind in ("pretty", "model"):
        if len(found[kind]) == 1:
            return found[kind][0]
    return None


# --- the reproduce command -----------------------------------------------------


def _hydra_str(value: str) -> str:
    """``value`` as a Hydra string element: bare when Hydra reads it back as
    the same string, else double-quoted."""
    if _BARE.fullmatch(value) and value.lower() not in _HYDRA_WORDS:
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _hydra_scalar(value: str) -> str:
    """A scalar override's value: numbers stay numbers."""
    return value if _NUMBER.fullmatch(value) else _hydra_str(value)


def _hydra_list(values: Iterable[str], quote: bool = False) -> str:
    items = ('"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' if quote
             else _hydra_str(v) for v in values)  # fmt: skip
    return "[" + ",".join(items) + "]"


def _override_key(override: str) -> str:
    return override.partition("=")[0].lstrip("+~")


def _variation_sets(overrides: Sequence[str]) -> set[str]:
    return {_override_key(o) for o in overrides}


def _set_by_variation(run: "Run") -> set[str]:
    """The Setup fields ``run``'s app variation sets itself."""
    keys = _variation_sets(run.overrides)
    return {f for f, key in _VARIATION_KEYS.items() if key in keys}


def _command_key(run: "Run") -> tuple:
    """What tells one configuration from another, with the fields this run's
    app variation sets itself left as None (a wildcard)."""
    if run.setup is None:
        return (None,) * len(_COMMAND_FIELDS)
    cleared = _set_by_variation(run)
    return tuple(
        None if f in cleared else getattr(run.setup, f) for f in _COMMAND_FIELDS
    )


def _configurations(runs: Sequence["Run"]) -> list[tuple[tuple, list]]:
    """``runs`` grouped by configuration, largest first. A run whose variation
    sets a field (``device=phone``) joins the largest group it agrees with
    on every other field, rather than looking like a configuration of its own."""
    groups: dict[tuple, list] = {}
    wild = []
    for run in runs:
        if run.setup is not None and _set_by_variation(run):
            wild.append(run)
        else:
            groups.setdefault(_command_key(run), []).append(run)
    for run in wild:
        key = _command_key(run)
        fits = [
            k for k in groups
            if all(a is None or a == b for a, b in zip(key, k))
        ]  # fmt: skip
        if fits:
            key = max(fits, key=lambda k: len(groups[k]))
        groups.setdefault(key, []).append(run)
    return sorted(groups.items(), key=lambda kv: (-len(kv[1]), repr(kv[0])))


@dataclass
class Command:
    """One launcher invocation re-running some of a model's runs."""

    text: str
    tasks: list[str]
    variations: list[tuple[str, ...]]
    n_runs: int
    config: int  # which configuration (0-based) it belongs to
    agent: Optional[str] = None  # its agent config name, if it names one
    agent_guessed: bool = False  # agent config inferred from the model name
    agent_unknown: bool = False  # nothing recorded or matched: a placeholder


@dataclass
class Reproduction:
    """Every command for one model's runs, and how its configurations differ."""

    commands: list[Command]
    n_configs: int
    differs_in: list[str]  # readable names of the fields that tell them apart
    cluster: Optional[str] = None


def _arguments(
    setup: Optional[Setup], tasks: Sequence[str], variations: Sequence[tuple[str, ...]]
) -> tuple[list[str], Optional[str], bool, bool]:
    """The launcher's overrides for ``tasks`` x ``variations`` under ``setup``."""
    sweep = [
        f"parallel_tasks.task_names={_hydra_list(tasks)}",
        "parallel_tasks.app_variations=["
        + ",".join(_hydra_list(v, quote=True) for v in variations)
        + "]",
    ]
    if setup is not None and setup.overrides is not None:
        # Hydra's exact record wins; only the sweep's own lists are replaced.
        mine = {"parallel_tasks.task_names", "parallel_tasks.app_variations", "task_name"}
        kept = [o for o in setup.overrides if _override_key(o) not in mine]
        agent = next((o.partition("=")[2] for o in kept if _override_key(o) == "agent"), None)
        return kept + sweep, agent, False, False
    setup = setup or Setup()
    guessed = unknown = False
    agent = setup.agent_config
    if agent is None:
        agent = guess_agent_config(setup.model_name, setup.pretty_name)
        guessed = agent is not None
    if agent is None:
        agent, unknown = UNKNOWN_AGENT, True
    args = [f"agent={_hydra_str(agent)}"]
    if setup.task_config:
        args.append(f"tasks={_hydra_str(setup.task_config)}")
    args += sweep
    for key, value in (
        ("seed", setup.seed),
        ("browsergym_env_args.task_seed", setup.task_seed),
        ("browsergym_env_args.max_steps", setup.max_steps),
        ("device", setup.device),
        ("agent.hostname", setup.hostname),
    ):
        if value is not None:
            args.append(f"{key}={_hydra_scalar(value)}")
    return args, agent, guessed, unknown


def format_command(args: Sequence[str]) -> str:
    """``LAUNCHER`` and ``args``, shell-quoted, one argument per line."""
    return " \\\n  ".join([LAUNCHER, *(shlex.quote(a) for a in args)])


def _blocks(cells: Iterable[tuple[str, tuple[str, ...]]]) -> list[tuple[list[str], list]]:
    """Split (task, variation) cells into exact task x variation grids: the
    tasks that ran the same set of variations go together."""
    by_task: dict[str, list[tuple[str, ...]]] = {}
    for task, variation in cells:
        seen = by_task.setdefault(task, [])
        if variation not in seen:
            seen.append(variation)
    grids: dict[tuple, list[str]] = {}
    for task in sorted(by_task):
        grids.setdefault(tuple(sorted(by_task[task])), []).append(task)
    blocks = [(tasks, list(variations)) for variations, tasks in grids.items()]
    return sorted(blocks, key=lambda b: (-len(b[0]) * len(b[1]), b[0]))


def commands(runs: Sequence["Run"]) -> Reproduction:
    """The commands that re-run ``runs`` (one model's): one per distinct
    configuration and, within it, per exact block of tasks x variations."""
    ordered = _configurations(runs)
    out: list[Command] = []
    for index, (_, group) in enumerate(ordered):
        setup = _shared_setup(group)
        for tasks, variations in _blocks((r.task, tuple(r.overrides)) for r in group):
            args, agent, guessed, unknown = _arguments(setup, tasks, variations)
            n = sum(1 for r in group if r.task in tasks and tuple(r.overrides) in variations)
            out.append(Command(format_command(args), tasks, variations, n, index,
                               agent, guessed, unknown))  # fmt: skip
    keys = [k for k, _ in ordered]
    differs = [
        _FIELD_NAMES.get(field, field)
        for i, field in enumerate(_COMMAND_FIELDS)
        if len({k[i] for k in keys}) > 1
    ]
    clusters = {r.setup.cluster for r in runs if r.setup and r.setup.cluster}
    return Reproduction(out, len(ordered), differs, ", ".join(sorted(clusters)) or None)


def _shared_setup(group: Sequence["Run"]) -> Optional[Setup]:
    """One setup for a configuration's runs. A field a variation can set
    takes its value from the runs whose variation leaves it alone."""
    with_setup = [r for r in group if r.setup is not None]
    if not with_setup:
        return None
    values = {}
    for f in _VARIATION_KEYS:
        seen = {getattr(r.setup, f) for r in with_setup if f not in _set_by_variation(r)}
        values[f] = seen.pop() if len(seen) == 1 else None
    return dataclasses.replace(with_setup[0].setup, **values)


# --- the configuration summary -------------------------------------------------


def _values(setups: Sequence[Setup], show) -> Optional[str]:
    """One field's value across ``setups``: the value, ``varies: a, b`` when
    they differ, or None when no setup records it."""
    seen: list[str] = []
    for setup in setups:
        value = show(setup)
        if value is not None and value not in seen:
            seen.append(value)
    if not seen:
        return None
    if len(seen) == 1:
        return seen[0]
    return "varies: " + ", ".join(sorted(seen))


def _model(s: Setup) -> Optional[str]:
    if s.model_name and s.pretty_name and s.pretty_name != s.model_name:
        return f"{s.model_name} ({s.pretty_name})"
    return s.model_name or s.pretty_name


def _device(s: Setup) -> Optional[str]:
    if s.device is None and s.viewport is None:
        return None
    size = "×".join(str(v) for v in s.viewport) if s.viewport else ""
    return " ".join(p for p in (s.device, size) if p)


def _coords(s: Setup) -> Optional[str]:
    parts = []
    if s.coord_scale:
        parts.append(f"0–{s.coord_scale} grid")
    if s.action_parser:
        parts.append(f"{s.action_parser} parser")
    return ", ".join(parts) or None


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def configuration(runs: Sequence["Run"]) -> list[tuple[str, list[tuple[str, str]]]]:
    """``[(heading, [(label, value), ...]), ...]`` describing how ``runs``
    were set up. Lines nothing recorded are left out; so is *Code* when no
    config records a commit."""
    setups = [r.setup for r in runs if r.setup is not None]
    if not setups:
        return []

    def line(label: str, show) -> list[tuple[str, str]]:
        value = _values(setups, show)
        return [(label, value)] if value is not None else []

    agent = (
        line("model", _model)
        + line("agent config", lambda s: s.agent_config)
        + line("client", lambda s: s.client)
        + line("observation", lambda s: None if s.observation is None
               else " + ".join(s.observation) or "none")  # fmt: skip
        + line("history", lambda s: None if s.history is None else ("on" if s.history else "off"))
        + line("temperature", lambda s: s.temperature)
        + line("max_tokens", lambda s: s.max_tokens)
        + line("coordinates", _coords)
    )
    has_steps = any(s.max_steps is not None for s in setups)
    episodes = (
        line("max steps", lambda s: s.max_steps)
        + (
            line("ends on", lambda s: f"{s.termination} (task_termination)"
                 if s.termination else DEFAULT_TERMINATION)  # fmt: skip
            if has_steps or any(s.termination for s in setups)
            else []
        )
        + line("device", _device)
    )
    tasks = sorted({r.task for r in runs})
    variations = {tuple(r.overrides) for r in runs}
    cells = {(r.task, tuple(r.overrides)) for r in runs}
    grid = f"{_count(len(tasks), 'task')} × {_count(len(variations), 'variation')}"
    if len(cells) < len(tasks) * len(variations):
        grid += f" ({len(cells)} of the {len(tasks) * len(variations)} cells)"
    sweep = (
        [("grid", grid)]
        + line("task catalog", lambda s: s.task_config)
        + line("seed", lambda s: s.seed)
        + line("task seed", lambda s: s.task_seed)
    )
    code = line("commit", lambda s: s.commit)
    sections = [("Agent", agent), ("Episodes", episodes), ("Sweep", sweep), ("Code", code)]
    return [(title, items) for title, items in sections if items]
