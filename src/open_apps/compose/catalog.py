"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Describe the Hydra config tree as data: groups, options, defaults, scalars.

The composer UI renders its pills from a :class:`Catalog`, so the catalog is
built from the same machinery the launchers use -- Hydra's config loader for
discovery (``list_groups`` / ``get_group_options``) and ``compose`` for
defaults -- rather than from a directory walk and a YAML parser. That is what
makes nested groups (``apps/todo/layout``) and ``# @package`` headers behave
exactly as they do at runtime, and it means a group added under ``config/``
shows up in the composer with no code change.

    uv run -m open_apps.compose.catalog --out catalog.json

Build cost is dominated by the online shop's 999-product content pack, which
every full composition of ``config.yaml`` loads (~3 s). The build composes the
two primary configs in full once each and does everything else with isolated
or shop-less compositions; :func:`build_catalog` caches its result per process
so the test suite pays for it once.
"""

import argparse
import dataclasses
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from omegaconf import DictConfig, OmegaConf

from open_apps import config_dir
from open_apps.apps.availability import task_needs_onlineshop
from open_apps.compose.rules import RULES_FILE, load_rules

__all__ = ["RULES_FILE", "Catalog", "build_catalog", "humanize", "parse_header"]

#: Primary configs the targets compose against. Others at the root (the goal-
#: variation sweep config, the rules file) are not composer targets.
PRIMARY_CONFIGS = ("config", "config_parallel_tasks")

#: Agent-config keys a rule may test. Recorded per agent option in
#: :attr:`Option.sets` (see ``_option_sets``).
WATCHED_PATHS = ("agent.client_type", "agent.hostname")

#: Words kept in their canonical case when a file stem is turned into a label.
ACRONYMS = {
    "gpt": "GPT",
    "axtree": "AXTree",
    "ui": "UI",
    "vl": "VL",
    "mcp": "MCP",
    "som": "SOM",
}

#: A group with at most this many options renders as pills, otherwise a select.
PILLS_MAX = 5
#: A group with more than this many options gets a search box.
SEARCHABLE_MIN = 12

# Labels for groups whose path does not humanize into something readable.
_GROUP_LABELS = {
    "browsergym_env_args": "Episode settings",
    "apps/theme": "Theme",
    "apps/pop_ups": "Pop-ups",
    "apps/chrome": "Window chrome",
}

# Which app's state a task class rewrites (and so which app it touches). Keyed
# on the class name rather than imported from open_apps.tasks.tasks so the
# catalog builds without importing browsergym. A class missing here derives no
# apps -- see derive_task_apps.
TASK_CLASS_APPS: dict[str, tuple[str, ...]] = {
    "AddEventTask": ("calendar",),
    "RemoveEventTask": ("calendar",),
    "AddToDoTask": ("todo",),
    "MarkToDoDoneTask": ("todo",),
    "DeleteToDoTask": ("todo",),
    "SendMessageTask": ("messenger",),
    "SavePlaceTask": ("maps",),
    "RemoveLandmarkTask": ("maps",),
    "EditFileTask": ("code_editor",),
}

# NavigateToAppTask names apps by URL stem (tasks._NAV_APP_URL_PREFIXES); map
# them onto the config/apps group names the rest of the catalog uses.
NAV_APP_NAMES = {
    "map": "maps",
    "messages": "messenger",
    "codeeditor": "code_editor",
    "uilibrary": "ui_library",
}
# Every task starts on the start page, so "touches start_page" carries no
# information and would match every task.
_NOT_A_TASK_APP = {"start_page"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Header:
    """A config file's leading comment block, split into the parts the UI shows."""

    label: str | None
    summary: str
    details: str


@dataclass
class Option:
    """One selectable config file in a group (``agent=dummy``)."""

    name: str
    label: str
    summary: str
    details: str
    #: Values this option gives the scalars and watched paths rules look at --
    #: e.g. ``browsergym_env_args=screenshot`` sets ``max_steps`` to 25. Lets a
    #: rule see the effective value without the evaluator composing anything,
    #: which the static (JS) host cannot do.
    sets: dict[str, Any] = field(default_factory=dict)


@dataclass
class Group:
    """A Hydra config group (``agent``, ``apps/todo/layout``)."""

    path: str
    label: str
    package: str
    options: list[Option]
    #: Default option per primary config (``None`` = no option selected).
    defaults: dict[str, str | None]
    #: May be unselected: a null default (``apps/theme``) or absent from the
    #: defaults list altogether (``experiment``).
    nullable: bool
    #: Absent from the defaults list, so selecting it needs ``+group=option``.
    #: A *null* default does not: ``apps/theme=dark`` binds as is.
    append: bool
    #: Can vary across a sweep's ``parallel_tasks.app_variations``. Only
    #: ``apps/*``: create_configs copies just ``apps`` out of each variation's
    #: composition, so a device or agent there would silently do nothing.
    sweepable: bool
    targets: list[str]
    control: Literal["pills", "select"]
    searchable: bool
    #: What the unset choice of a nullable group is called, and what it
    #: means; ``None`` when the group always has an option. See
    #: :func:`_null_option`.
    null_label: str | None = None
    null_summary: str | None = None

    def option(self, name: str) -> Option:
        for option in self.options:
            if option.name == name:
                return option
        raise KeyError(f"{self.path} has no option {name!r}")


@dataclass
class Scalar:
    """A free-form, typed config value (``seed``, ``browsergym_env_args.max_steps``)."""

    path: str
    label: str
    summary: str
    type: Literal["bool", "int", "str", "enum"]
    default: Any
    #: Default per primary config; :attr:`default` is ``defaults["config"]``.
    defaults: dict[str, Any]
    targets: list[str]
    control: Literal["toggle", "number", "text", "select"]
    searchable: bool = False
    #: Set through this environment variable rather than an override when the
    #: target is a worker pool (conduct.sh reads ``HEADLESS``).
    pool_env: str | None = None


@dataclass
class TaskEntry:
    """One task of a ``tasks`` group option -- a value for ``task_name``."""

    name: str
    label: str
    goal: str
    kind: str
    #: config/apps group names this task touches -- the ``task_app`` rule fact.
    apps: list[str]


@dataclass
class Target:
    """A way to run a selection; see :mod:`open_apps.compose.command`."""

    name: str
    label: str
    config_name: str
    command: str
    #: Where the script's own defaults differ from ``config_name``'s.
    defaults: dict[str, Any] = field(default_factory=dict)
    #: Offered as a Target pill. The SLURM pool is not: Mode already says
    #: "on SLURM", so a pill of its own could only agree with the Mode row or
    #: contradict it. Worker pool + ``mode=slurm_cluster`` resolves to it.
    pill: bool = True


TARGETS: dict[str, Target] = {
    "single": Target("single", "Single run", "config", "uv run launch_agent.py"),
    "sweep": Target(
        "sweep",
        "Sweep",
        "config_parallel_tasks",
        "uv run launch_parallel_agents.py",
    ),
    "pool": Target(
        "pool",
        "Worker pool",
        "config",
        "./scripts/conduct.sh",
        # conduct.sh: AGENTS="${AGENTS:-dummy}".
        defaults={"agent": "dummy"},
    ),
    "slurm_pool": Target(
        "slurm_pool",
        "Worker pool on SLURM",
        "config",
        "sbatch scripts/conduct_slurm.sh",
        # conduct_slurm.sh passes `use_wandb=True` ahead of the user's args,
        # and its AGENTS default names no config that exists, so there is no
        # usable agent default -- the command always spells AGENTS out.
        defaults={"agent": None, "use_wandb": True},
        pill=False,
    ),
}

ALL_TARGETS = list(TARGETS)


# (path, label, summary, control, pool_env). Type and default are read from the
# composed config, not declared here.
_SCALAR_SPECS: list[tuple[str, str, str, str, str | None]] = [
    (
        "task_name",
        "Task",
        "Which task in the selected tasks group to run.",
        "select",
        None,
    ),
    (
        "browsergym_env_args.max_steps",
        "Max steps",
        "Maximum number of agent steps per episode.",
        "number",
        None,
    ),
    ("seed", "Seed", "Seed for Python's random module.", "number", None),
    (
        "use_wandb",
        "W&B logging",
        "Log the run to Weights & Biases.",
        "toggle",
        None,
    ),
    (
        "browsergym_env_args.headless",
        "Headless",
        "Run the agent's browser without a window.",
        "toggle",
        "HEADLESS",
    ),
    (
        "record_video",
        "Record video",
        "Record each episode as a .webm (and log it to W&B when enabled).",
        "toggle",
        None,
    ),
]


@dataclass
class Catalog:
    """Everything the composer needs to know about ``config/``."""

    groups: dict[str, Group]
    scalars: dict[str, Scalar]
    #: Task list per ``tasks`` option; ``task_name`` is enumerated from here.
    tasks: dict[str, list[TaskEntry]]
    targets: dict[str, Target]
    rules: list[dict[str, Any]]
    #: Values of :data:`WATCHED_PATHS` in config.yaml's default composition --
    #: what :attr:`Option.sets` are diffs against.
    baseline: dict[str, Any]
    #: ``static`` (docs site: render and copy only) or ``local`` (compose.py:
    #: validate / preview / record / run). The UI hides local-only buttons
    #: when static.
    capabilities: Literal["static", "local"] = "static"

    def task(self, tasks_option: str, name: str) -> TaskEntry:
        for entry in self.tasks[tasks_option]:
            if entry.name == name:
                return entry
        raise KeyError(f"tasks={tasks_option} has no task {name!r}")

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Headers, labels
# ---------------------------------------------------------------------------

_PACKAGE_LINE = re.compile(r"^#\s*@package\b")
_LABEL_LINE = re.compile(r"^label:\s*(.+?)\s*$")
# A period that is not the end of a sentence because it ends an abbreviation.
_ABBREVIATIONS = ("e.g", "i.e", "etc", "vs", "cf")


def _comment_block(lines: list[str], i: int) -> list[str]:
    block = []
    while i < len(lines) and lines[i].startswith("#"):
        text = lines[i][1:]
        block.append(text.removeprefix(" "))
        i += 1
    return block


def _first_sentence(paragraph: str) -> str:
    for match in re.finditer(r"[.!?](?=\s|$)", paragraph):
        head = paragraph[: match.start()]
        if head.lower().endswith(_ABBREVIATIONS):
            continue
        return paragraph[: match.end()]
    return paragraph


def parse_header(text: str) -> Header:
    """Split a config file's leading comment block into label/summary/details.

    The block is the first run of ``#`` lines, skipping ``# @package`` lines.
    If the file opens with a ``defaults:`` list instead, the block right after
    that list counts: agent configs put their description there, since the
    defaults list has to come first to be read as one. A block *above* the
    defaults list wins over one below it, which is usually a field comment.

    ``# label: <text>`` inside the block sets the UI label and is removed from
    the summary and details. ``summary`` is the first sentence of the first
    paragraph; ``details`` the whole block, line breaks kept so the usage
    examples in it stay readable.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or _PACKAGE_LINE.match(lines[i])):
        i += 1
    block: list[str] = []
    if i < len(lines) and lines[i].startswith("#"):
        block = _comment_block(lines, i)
    elif i < len(lines) and lines[i].startswith("defaults:"):
        i += 1
        # The defaults list: indented entries (and comments), or `- x` at col 0.
        while i < len(lines) and lines[i][:1] in (" ", "\t", "-"):
            i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        block = _comment_block(lines, i)

    label = None
    kept = []
    for line in block:
        match = _LABEL_LINE.match(line)
        if match and label is None:
            label = match.group(1)
        else:
            kept.append(line.rstrip())
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()

    first_paragraph = []
    for line in kept:
        if not line.strip():
            break
        first_paragraph.append(line.strip())
    summary = _first_sentence(" ".join(first_paragraph))
    return Header(label=label, summary=summary, details="\n".join(kept))


def humanize(stem: str) -> str:
    """Turn a file stem into a sentence-case label: ``axtree-only`` -> ``AXTree only``.

    Tokens with a digit are kept verbatim so model names and versions
    (``Qwen3.6``, ``5.5``) survive; known acronyms keep their canonical case.
    """
    words = []
    for token in re.split(r"[-_]+", stem):
        if not token:
            continue
        if token.lower() in ACRONYMS:
            words.append(ACRONYMS[token.lower()])
        elif any(c.isdigit() for c in token):
            words.append(token)
        else:
            words.append(token.lower())
    if words and words[0][:1].islower():
        words[0] = words[0][:1].upper() + words[0][1:]
    return " ".join(words)


def _group_label(path: str) -> str:
    if path in _GROUP_LABELS:
        return _GROUP_LABELS[path]
    parts = path.split("/")
    if parts[0] == "apps" and len(parts) > 1:
        parts = parts[1:]
    return humanize("_".join(parts))


# What leaving a nullable group unset means, where "No <group>" undersells
# it. A null apps/theme is not "no theme": every app falls back to the look it
# prefers (config.yaml's defaults-list comment), which is a choice in itself.
_NULL_OPTIONS = {
    "apps/theme": (
        "App defaults",
        "No shared theme: each app keeps its own default look (the code "
        "editor's is VS Code dark).",
    ),
}


def _null_option(path: str, label: str) -> tuple[str, str]:
    """``(label, summary)`` for a nullable group's unset choice.

    Lives in the catalog, not the UI, so the docs page and compose.py -- and
    anything else reading catalog.json -- call it the same thing.
    """
    if path in _NULL_OPTIONS:
        return _NULL_OPTIONS[path]
    noun = label.lower()
    return (
        f"No {noun}",
        f"Leave {noun} unset: the options the rest of the config picks apply.",
    )


def _goal_label(goal: str) -> str:
    return _first_sentence(" ".join(goal.split()))


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def derive_task_apps(task_cfg: Any) -> list[str]:
    """Which config/apps apps a task touches, for the ``task_app`` rule fact.

    Derived from the task's class, not its goal text: each task class rewrites
    one app's state (:data:`TASK_CLASS_APPS`), ``NavigateToAppTask`` touches
    its ``source_app`` and ``target_app``, and ``CompositeTask`` the union of
    its sub-tasks. On top of that, ``onlineshop`` is added exactly when
    :func:`open_apps.apps.availability.task_needs_onlineshop` says so, so the
    composer flags the same tasks ``AgentLauncher.check_task_can_run`` refuses.

    Known blind spot: an app a task only *reads* (the OpenBanking tasks look a
    figure up in the bank, then write it to todo/calendar/messenger) is not
    derived, because nothing in the task config says so structurally.
    """
    raw = (
        OmegaConf.to_container(task_cfg, resolve=False)
        if OmegaConf.is_config(task_cfg)
        else task_cfg
    )
    apps: set[str] = set()

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        kind = str(node.get("_target_", "")).rsplit(".", 1)[-1]
        apps.update(TASK_CLASS_APPS.get(kind, ()))
        if kind == "NavigateToAppTask":
            for key in ("source_app", "target_app"):
                app = node.get(key)
                if app:
                    apps.add(NAV_APP_NAMES.get(app, app))
        for sub in node.get("subtasks") or []:
            visit(sub)

    visit(raw)
    if task_needs_onlineshop(raw):
        apps.add("onlineshop")
    return sorted(apps - _NOT_A_TASK_APP)


# ---------------------------------------------------------------------------
# Hydra
# ---------------------------------------------------------------------------


@contextmanager
def hydra_context(path: Path | None = None) -> Iterator[Any]:
    """Initialize Hydra on ``config/`` and yield its config loader.

    Restores whatever global Hydra state was there before -- the catalog is
    built from tests, from the CLI and (later) from inside compose.py, and
    must not clobber or trip over an already-initialized Hydra.
    """
    from hydra import initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra
    from hydra.initialize import get_gh_backup, restore_gh_from_backup

    saved = get_gh_backup()
    GlobalHydra.instance().clear()
    try:
        with initialize_config_dir(
            config_dir=str((path or config_dir()).resolve()), version_base=None
        ):
            yield GlobalHydra.instance().config_loader()
    finally:
        restore_gh_from_backup(saved)


def _compose(
    config_name: str | None, overrides: list[str], **kwargs: Any
) -> DictConfig:
    from hydra import compose

    return compose(config_name=config_name, overrides=overrides, **kwargs)


def _walk_groups(loader: Any, parent: str = "") -> list[str]:
    out = []
    for name in sorted(loader.list_groups(parent)):
        path = f"{parent}/{name}" if parent else name
        if path == "hydra":  # Hydra's own groups, not ours
            continue
        if loader.get_group_options(path):
            out.append(path)
        out.extend(_walk_groups(loader, path))
    return out


def load_tasks_config(tasks_option: str, path: Path | None = None) -> DictConfig:
    """The ``{task_name: task config}`` map a ``tasks=<option>`` selection loads."""
    with hydra_context(path):
        return _compose(None, [f"+tasks={tasks_option}"]).tasks


def _select(cfg: DictConfig, key: str) -> Any:
    value = OmegaConf.select(cfg, key, default=None)
    if OmegaConf.is_config(value):
        return OmegaConf.to_container(value, resolve=True)
    return value


def _scalar_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    return "str"


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _option_sets(
    group: str, package: str, option: str, append: bool, base: DictConfig
) -> dict[str, Any]:
    """Values an option gives the scalars and watched paths.

    Group-packaged options are composed in isolation (``+agent=x`` with no
    primary config: milliseconds, and their own defaults list still applies).
    Only ``_global_`` options (``experiment``, ``mode``) need the full config,
    since they override other groups -- composed without the shop catalog and
    with the smallest task set, which do not affect any scalar but are most of
    the cost of a full composition.
    """
    if package == "_global_":
        relevant = list(_TRACKED_PATHS)
        prefix = "+" if append else ""
        cfg = _compose("config", [f"{prefix}{group}={option}", *_FAST_OVERRIDES])
    else:
        relevant = [p for p in _TRACKED_PATHS if p.startswith(package + ".")]
        if not relevant:
            return {}
        cfg = _compose(None, [f"+{group}={option}"])
    # Only what differs from config.yaml's own composition: the evaluator
    # starts from that baseline (Catalog.baseline) and overlays these.
    return {p: _select(cfg, p) for p in relevant if _select(cfg, p) != _select(base, p)}


# Cheap stand-ins for the two expensive defaults; see _option_sets.
_FAST_OVERRIDES = ["apps/onlineshop/content=default", "tasks=openbanking"]
_TRACKED_PATHS = [s[0] for s in _SCALAR_SPECS if s[0] != "task_name"] + list(
    WATCHED_PATHS
)


def _build(path: Path | None, capabilities: str) -> Catalog:
    root = (path or config_dir()).resolve()
    with hydra_context(root) as loader:
        composed = {
            name: _compose(name, [], return_hydra_config=True)
            for name in PRIMARY_CONFIGS
        }
        choices = {
            name: OmegaConf.to_container(cfg.hydra.runtime.choices)
            for name, cfg in composed.items()
        }
        fast_base = _compose("config", list(_FAST_OVERRIDES))

        groups: dict[str, Group] = {}
        for group_path in _walk_groups(loader):
            names = sorted(loader.get_group_options(group_path), key=str.lower)
            present = {c: group_path in choices[c] for c in PRIMARY_CONFIGS}
            append = not any(present.values())
            package = None
            options = []
            for name in names:
                if package is None:
                    # Read through Hydra so `# @package` resolves as at runtime.
                    # Once per group: options share a package, and loading is
                    # a full YAML parse (the shop's webshop pack is ~2 s).
                    result = loader.repository.load_config(f"{group_path}/{name}")
                    package = result.header.get("package") or group_path.replace(
                        "/", "."
                    )
                header = parse_header(
                    (root / group_path / f"{name}.yaml").read_text("utf-8")
                )
                options.append(
                    Option(
                        name=name,
                        label=header.label or humanize(name),
                        summary=header.summary,
                        details=header.details,
                        sets=_option_sets(group_path, package, name, append, fast_base),
                    )
                )
            defaults = {c: choices[c].get(group_path) for c in PRIMARY_CONFIGS}
            label = _group_label(group_path)
            nullable = any(v is None for v in defaults.values())
            null_label, null_summary = (
                _null_option(group_path, label) if nullable else (None, None)
            )
            groups[group_path] = Group(
                path=group_path,
                label=label,
                package=package or "",
                options=options,
                defaults=defaults,
                nullable=nullable,
                append=append,
                sweepable=group_path.startswith("apps/"),
                targets=list(ALL_TARGETS),
                control="pills" if len(options) <= PILLS_MAX else "select",
                searchable=len(options) > SEARCHABLE_MIN,
                null_label=null_label,
                null_summary=null_summary,
            )

        tasks = {}
        for option in groups["tasks"].options:
            task_cfgs = _compose(None, [f"+tasks={option.name}"]).tasks
            tasks[option.name] = [
                TaskEntry(
                    name=str(name),
                    label=_goal_label(str(cfg.get("goal") or ""))
                    or humanize(str(name)),
                    goal=" ".join(str(cfg.get("goal") or "").split()),
                    kind=str(cfg.get("_target_", "")).rsplit(".", 1)[-1],
                    apps=derive_task_apps(cfg),
                )
                for name, cfg in task_cfgs.items()
            ]

    scalars = {}
    for spath, label, summary, control, pool_env in _SCALAR_SPECS:
        defaults = {c: _select(composed[c], spath) for c in PRIMARY_CONFIGS}
        is_enum = spath == "task_name"
        scalars[spath] = Scalar(
            path=spath,
            label=label,
            summary=summary,
            type="enum" if is_enum else _scalar_type(defaults["config"]),
            default=defaults["config"],
            defaults=defaults,
            targets=list(ALL_TARGETS),
            control=control,  # type: ignore[arg-type]
            searchable=is_enum,
            pool_env=pool_env,
        )

    # Order groups as the primary config's defaults list does (then nested
    # groups after their parent, then append-only groups) -- the order the
    # command renders overrides in, and a sensible default UI order.
    # Hydra fills runtime.choices while unwinding the defaults tree, so the
    # dict is in reverse defaults-list order.
    order = list(reversed(list(choices["config"])))

    def position(path: str) -> tuple[int, int, int, str]:
        # A nested group (apps/todo/layout) sorts right after its parent.
        parent = next((p for p in order if path.startswith(p + "/")), path)
        rank = order.index(parent) if parent in order else len(order)
        own = order.index(path) if path in order else 0
        return (rank, parent != path, own, path)

    groups = dict(sorted(groups.items(), key=lambda kv: position(kv[0])))
    return Catalog(
        groups=groups,
        scalars=scalars,
        tasks=tasks,
        targets=dict(TARGETS),
        rules=[dataclasses.asdict(r) for r in load_rules()],
        baseline={p: _select(composed["config"], p) for p in WATCHED_PATHS},
        capabilities=capabilities,  # type: ignore[arg-type]
    )


@lru_cache(maxsize=4)
def _cached_build(path: str | None, capabilities: str) -> Catalog:
    return _build(Path(path) if path else None, capabilities)


def build_catalog(
    path: Path | None = None, capabilities: Literal["static", "local"] = "static"
) -> Catalog:
    """Build (or reuse) the catalog for ``path`` (default: the repo ``config/``).

    Cached per process: the result is shared, so treat it as read-only.
    """
    return _cached_build(str(path) if path else None, capabilities)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, help="write JSON here (default: stdout)")
    parser.add_argument("--capabilities", choices=["static", "local"], default="static")
    parser.add_argument("--indent", type=int, default=None)
    args = parser.parse_args(argv)
    text = build_catalog(capabilities=args.capabilities).to_json(indent=args.indent)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
