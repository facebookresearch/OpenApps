"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Find the agent runs under some directories and reduce them to one row per model.

A *run* is one BrowserGym experiment directory: the folder holding the
``summary_info.json`` BrowserGym writes when an episode ends. Its
``config.yaml`` is the one :class:`open_apps.launcher.OpenAppsLauncher` saves
in the job's ``logs_dir``, the experiment directory's parent:

    <logs_dir>/config.yaml                        # launch_agent.py
    <logs_dir>/<run-dir>/<job_id>/config.yaml     # launch_parallel_agents.py
    .../<job>/<time>_AgentArgs_on_<task>_<seed>/summary_info.json

Only those two files are read (and Hydra's ``.hydra/overrides.yaml`` beside
the config, if the run saved one, for the Reproduce command). From ``summary_info.json``: ``cum_reward``
(success is ``cum_reward >= 1.0``, the rule the launcher's W&B summary uses),
``n_steps``, and ``err_msg`` / ``stack_trace`` (an infrastructure error -- the
web app never came up, the model server timed out -- not an agent failure).
From ``config.yaml``: the agent (``model_pretty_name``, else ``model_name``),
``task_name``, ``app_overrides`` (the app variation) and the seeds, plus the
few keys the drill-down's Reproduce and Configuration sections show (see
:mod:`open_apps.results_preview.reproduce`), read once per config file.

Re-runs of one cell -- the same (agent, task, variation, seed) -- count once:
an errored run loses to one that did not error, otherwise the later run wins.
So pointing at a sweep together with its re-run of crashed jobs is safe.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import yaml

from open_apps.results_preview.reproduce import COMMIT_KEYS, Setup, setup_from_config

SUMMARY = "summary_info.json"
CONFIG = "config.yaml"
#: Directories that never hold a run and can be large (a job's databases, the
#: W&B cache, an episode's video folder).
_PRUNE = {"wandb", "databases", "task_video", ".git", "node_modules", "__pycache__"}
#: The top-level config keys a run is identified and described by.
#: Everything else in a job's config.yaml (tens of thousands of lines of app
#: and task settings) is skipped without being parsed.
_CONFIG_KEYS = (
    "agent",
    "task_name",
    "app_overrides",
    "seed",
    "browsergym_env_args",
    "agent_config_name",
    "task_config_name",
    "device",
    "task_termination",
    "cluster",
    *COMMIT_KEYS,
)
#: The z for a two-sided 95% interval.
Z95 = 1.959963984540054


@dataclass(frozen=True)
class Run:
    """One finished episode."""

    agent: str
    task: str
    variation: str  # "default", or the job's app overrides joined by ", "
    seed: str  # "<seed>/<task_seed>"
    success: bool
    errored: bool
    n_steps: Optional[int]
    started: float  # epoch seconds; orders re-runs of one cell
    exp_dir: str
    source: str  # the sweep (or single run) it came from, for the drill-down
    error: Optional[str] = None
    overrides: tuple[str, ...] = ()  # the app variation's overrides, as launched
    #: How its job was set up (shared by every run of one config file).
    setup: Optional[Setup] = field(default=None, compare=False)

    @property
    def cell(self) -> tuple[str, str, str, str]:
        return (self.agent, self.task, self.variation, self.seed)


@dataclass
class Found:
    """What :func:`find_runs` turned up."""

    runs: list[Run] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, why)
    #: Experiment dirs without a summary_info.json: still running, or killed
    #: before BrowserGym could write one.
    unfinished: list[str] = field(default_factory=list)


# --- reading one run ---------------------------------------------------------------


def _top_level_blocks(text: str, keys: Iterable[str]) -> str:
    """The YAML of only ``keys`` among ``text``'s top-level mappings.

    ``OmegaConf.save`` writes one top-level key per unindented line, with its
    value on the following indented (or ``- `` list) lines, so a block runs
    until the next unindented ``key:`` line.
    """
    wanted = set(keys)
    out: list[str] = []
    keep = False
    for line in text.splitlines(keepends=True):
        if line[:1] not in ("", " ", "-", "\t", "\n", "\r", "#"):
            key = line.split(":", 1)[0].strip().strip("'\"")
            keep = key in wanted
        if keep:
            out.append(line)
    return "".join(out)


def read_config(path: Path) -> dict:
    """The identifying keys of a saved run config (see ``_CONFIG_KEYS``)."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    try:
        data = yaml.load(_top_level_blocks(text, _CONFIG_KEYS), Loader=loader)
    except yaml.YAMLError:
        # Hand-written or reformatted: parse it all.
        data = yaml.load(text, Loader=loader)
    if not isinstance(data, dict):
        raise ValueError("not a mapping")
    return data


def agent_name(config: dict) -> str:
    """The display name the launcher uses for the W&B run (launcher.py)."""
    agent = config.get("agent") or {}
    if not isinstance(agent, dict):
        agent = {}
    for value in (
        agent.get("model_pretty_name"),
        agent.get("model_name"),
        agent.get("agent_name"),
        config.get("agent_config_name"),
    ):
        if value:
            return str(value)
    return "unknown"


def variation_overrides(overrides: Any) -> tuple[str, ...]:
    if not overrides:
        return ()
    if isinstance(overrides, (list, tuple)):
        return tuple(str(o) for o in overrides)
    return (str(overrides),)


def variation_label(overrides: Any) -> str:
    if not overrides:
        return "default"
    if isinstance(overrides, (list, tuple)):
        return ", ".join(str(o) for o in overrides)
    return str(overrides)


def _started(exp_dir: Path, summary: Path) -> float:
    """When the run started: BrowserGym's ``%Y-%m-%d_%H-%M-%S_...`` exp dir
    prefix, else the summary's mtime."""
    try:
        return dt.datetime.strptime(exp_dir.name[:19], "%Y-%m-%d_%H-%M-%S").timestamp()
    except ValueError:
        return summary.stat().st_mtime


def _source(path: Path, root: Path) -> str:
    """The sweep a run came from: the first directory below the search root,
    or the root itself when the run sits directly in it or in one of its
    numbered job dirs (the root *is* a sweep)."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return str(path)
    if len(rel.parts) < 2 or rel.parts[0].isdigit():
        return str(root)
    return str(root / rel.parts[0])


def read_run(
    exp_dir: Path, config: dict, root: Path, setup: Optional[Setup] = None
) -> Run:
    """The run in ``exp_dir``, identified by its job's ``config``."""
    summary_path = exp_dir / SUMMARY
    summary = json.loads(summary_path.read_text())
    if not isinstance(summary, dict):
        raise ValueError("summary_info.json is not an object")
    reward = summary.get("cum_reward")
    try:
        reward = float(reward or 0.0)
    except (TypeError, ValueError):
        reward = 0.0
    error = summary.get("err_msg") or summary.get("stack_trace")
    steps = summary.get("n_steps")
    env = config.get("browsergym_env_args") or {}
    task_seed = env.get("task_seed") if isinstance(env, dict) else None
    return Run(
        agent=agent_name(config),
        task=str(config.get("task_name") or "unknown"),
        variation=variation_label(config.get("app_overrides")),
        seed=f"{config.get('seed')}/{task_seed}",
        success=reward >= 1.0,
        errored=bool(error),
        n_steps=int(steps) if isinstance(steps, (int, float)) else None,
        started=_started(exp_dir, summary_path),
        exp_dir=str(exp_dir),
        source=_source(exp_dir, root),
        error=str(error).strip().splitlines()[-1][:300] if error else None,
        overrides=variation_overrides(config.get("app_overrides")),
        setup=setup,
    )


# --- discovery -----------------------------------------------------------------


def _matches(value: Optional[str], patterns: Sequence[str]) -> bool:
    return not patterns or any(fnmatch.fnmatchcase(str(value), p) for p in patterns)


def _config_for(exp_dir: Path, root: Path) -> Optional[Path]:
    """The job's config.yaml: in the experiment dir's parent (where the
    launcher saves it), else the nearest one above that, up to ``root``."""
    candidates = [exp_dir.parent, exp_dir]
    for directory in exp_dir.parent.parents:
        if directory != root and root not in directory.parents:
            break
        candidates.append(directory)
    for directory in candidates:
        if (directory / CONFIG).is_file():
            return directory / CONFIG
    return None


def find_runs(
    paths: Sequence[str | Path],
    agents: Sequence[str] = (),
    tasks: Sequence[str] = (),
) -> Found:
    """Every run below ``paths``, however nested, filtered by agent and task
    name (shell globs; an agent matches by display name or agent config name)."""
    found = Found()
    configs: dict[Path, Any] = {}  # path -> parsed dict, or the exception
    setups: dict[Path, Setup] = {}
    seen: set[Path] = set()
    for root in map(Path, paths):
        if root.is_file():
            root = root.parent
        if not root.is_dir():
            found.skipped.append((str(root), "no such directory"))
            continue
        root = root.resolve()
        for directory, subdirs, files in os.walk(root):
            here = Path(directory)
            if SUMMARY not in files:
                subdirs[:] = sorted(d for d in subdirs if d not in _PRUNE)
                if "exp_args.pkl" in files:  # BrowserGym started, never finished
                    found.unfinished.append(str(here))
                continue
            subdirs[:] = []  # an experiment dir; nothing below it is a run
            if here in seen:
                continue
            seen.add(here)
            config_path = _config_for(here, root)
            if config_path is None:
                found.skipped.append((str(here), f"no {CONFIG} beside it"))
                continue
            if config_path not in configs:
                try:
                    configs[config_path] = read_config(config_path)
                except Exception as exc:  # noqa: BLE001 -- skip, don't sink the page
                    configs[config_path] = exc
                else:
                    try:  # only the drill-down's description: never skip for it
                        setups[config_path] = setup_from_config(
                            configs[config_path], config_path
                        )
                    except Exception:  # noqa: BLE001
                        pass
            config = configs[config_path]
            if isinstance(config, Exception):
                found.skipped.append((str(config_path), f"unreadable: {config}"))
                continue
            try:
                run = read_run(here, config, root, setups.get(config_path))
            except Exception as exc:  # noqa: BLE001
                found.skipped.append((str(here / SUMMARY), f"unreadable: {exc}"))
                continue
            names = (run.agent, config.get("agent_config_name"))
            if agents and not any(_matches(n, agents) for n in names if n):
                continue
            if not _matches(run.task, tasks):
                continue
            found.runs.append(run)
    return found


def resolve(runs: Iterable[Run]) -> tuple[list[Run], int]:
    """One run per cell, and how many re-runs were dropped. An errored run
    loses to one that did not error; otherwise the later run wins."""
    best: dict[tuple, Run] = {}
    total = 0
    for run in runs:
        total += 1
        current = best.get(run.cell)
        if current is None or _rank(run) > _rank(current):
            best[run.cell] = run
    return sorted(best.values(), key=lambda r: r.cell), total - len(best)


def _rank(run: Run) -> tuple:
    return (not run.errored, run.started, run.exp_dir)


# --- scoring -------------------------------------------------------------------


def wilson(successes: int, n: int, z: float = Z95) -> Optional[tuple[float, float]]:
    """The Wilson score interval for a binomial proportion (None for n=0)."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class Tally:
    """Success over the non-errored runs of some slice (a model, a task...)."""

    label: str
    episodes: int = 0  # non-errored runs: the rate's denominator
    successes: int = 0
    errored: int = 0
    steps: list[int] = field(default_factory=list)

    def add(self, run: Run) -> None:
        if run.errored:
            self.errored += 1
            return
        self.episodes += 1
        self.successes += run.success
        if run.n_steps is not None:
            self.steps.append(run.n_steps)

    @property
    def rate(self) -> Optional[float]:
        return self.successes / self.episodes if self.episodes else None

    @property
    def ci(self) -> Optional[tuple[float, float]]:
        return wilson(self.successes, self.episodes)

    @property
    def mean_steps(self) -> Optional[float]:
        return sum(self.steps) / len(self.steps) if self.steps else None


@dataclass
class ModelRow(Tally):
    rank: int = 0
    tasks: int = 0  # distinct tasks among the counted episodes
    by_task: list[Tally] = field(default_factory=list)
    by_variation: list[Tally] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    latest: Optional[float] = None
    replaced: int = 0  # re-runs of its cells that lost to another run
    errors: list[tuple[str, str, str]] = field(default_factory=list)  # task, dir, msg
    runs: list[Run] = field(default_factory=list)  # its resolved runs


def _tallies(runs: Sequence[Run], key) -> list[Tally]:
    groups: dict[str, Tally] = {}
    for run in runs:
        label = key(run)
        groups.setdefault(label, Tally(label)).add(run)
    return sorted(groups.values(), key=lambda t: t.label)


def leaderboard(runs: Sequence[Run]) -> list[ModelRow]:
    """One ranked row per model over already-resolved ``runs``: by success
    rate, then the interval's lower bound, then episodes."""
    by_agent: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        by_agent[run.agent].append(run)
    rows = []
    for agent, agent_runs in by_agent.items():
        row = ModelRow(agent)
        for run in agent_runs:
            row.add(run)
        row.tasks = len({r.task for r in agent_runs if not r.errored})
        row.by_task = _tallies(agent_runs, lambda r: r.task)
        row.by_variation = _tallies(agent_runs, lambda r: r.variation)
        row.sources = sorted({r.source for r in agent_runs})
        row.latest = max(r.started for r in agent_runs)
        row.runs = list(agent_runs)
        row.errors = [
            (r.task, r.exp_dir, r.error or "") for r in agent_runs if r.errored
        ]
        rows.append(row)
    rows.sort(
        key=lambda r: (
            r.rate is None,
            -(r.rate or 0.0),
            -(r.ci[0] if r.ci else 0.0),
            -r.episodes,
            r.label,
        )
    )
    for i, row in enumerate(rows, 1):
        row.rank = i
    return rows


@dataclass
class Results:
    """Everything the page and the terminal summary show."""

    paths: list[str]
    rows: list[ModelRow]
    runs: list[Run]  # resolved: one per cell
    duplicates: int
    skipped: list[tuple[str, str]]
    unfinished: list[str]
    generated: str

    @property
    def n_episodes(self) -> int:
        return sum(r.episodes for r in self.rows)

    @property
    def n_errored(self) -> int:
        return sum(r.errored for r in self.rows)


def collect(
    paths: Sequence[str | Path],
    agents: Sequence[str] = (),
    tasks: Sequence[str] = (),
    now: Optional[dt.datetime] = None,
) -> Results:
    """Find, resolve and rank every run under ``paths``."""
    found = find_runs(paths, agents, tasks)
    runs, duplicates = resolve(found.runs)
    rows = leaderboard(runs)
    found_per_agent = defaultdict(int)
    for run in found.runs:
        found_per_agent[run.agent] += 1
    for row in rows:
        row.replaced = found_per_agent[row.label] - row.episodes - row.errored
    when = (now or dt.datetime.now().astimezone()).strftime("%Y-%m-%d %H:%M %Z").strip()
    return Results(
        paths=[str(p) for p in paths],
        rows=rows,
        runs=runs,
        duplicates=duplicates,
        skipped=found.skipped,
        unfinished=found.unfinished,
        generated=when,
    )
