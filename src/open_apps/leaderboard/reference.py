"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

What the repo says a suite cell is: the reference ``validate`` checks a
record against, and re-scores it under.

A record carries its own task config, irreversible map, fired events (with
their ``forbidden`` predicates) and adversarial flags, so an offline scorer
needs nothing else. That is exactly what makes them untrustworthy in a
submission: edit a task's ``expected``, drop a predicate or shrink the map,
and a re-score from the record's own copies agrees with whatever it says.
So for a leaderboard record the copies are *compared* against this
reference, and the re-score uses the reference, never the copies.

The reference is composed the way the sweep composes a cell
(``SuiteParallelTasksConfig`` -> ``AppVariationParallelTasksConfig``):

* the task config from the suite's root config -- ``config_parallel_tasks``
  with ``benchmark=<suite>``, whose ``tasks`` group is the suite's task set;
* the ``apps`` tree from ``config`` composed with the cell's overrides
  (``create_configs`` takes ``apps`` from that compose and nothing else),
  which yields the irreversible map, the events and their content
  languages, and the adversarial flags exactly as ``identity_from_config``
  and the launcher read them.

Composition costs ~3 s per distinct variation (it is YAML parsing of the
apps' content packs), so both halves are cached per process: align_v1 has a
dozen distinct variations however many cells and seeds it grows.
"""

from __future__ import annotations

import copy
import dataclasses
import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from omegaconf import OmegaConf

from open_apps import config_dir
from open_apps.alignment.record import CLAIM_ACTION, REFUSAL_ACTION
from open_apps.leaderboard.suite import pinned_env_args, suite_path, suites_dir

# Enough of a mismatching value to recognise it in a PR comment.
_MAX_REPR = 120


@dataclass(frozen=True)
class ReferenceCell:
    """The repo's view of one (task, variation, seed) cell."""

    task_name: str
    variation: tuple[str, ...]
    seed: int
    task_config: dict
    task_class: str
    goal: str
    goal_style: Optional[str]
    irreversible: dict
    # ``Event``s, already in this cell's content language (``Event.localized``).
    events: tuple = ()
    # Event id -> its resolved ``after_step`` (ranges drawn from ``seed``).
    event_steps: dict = field(default_factory=dict)
    adversarial_popup: bool = False
    adversarial_content: bool = False
    # The suite's pinned ``browsergym_env_args`` (``suite.pinned_env_args``):
    # the step budget bounds the trace, and both are stamped on the record.
    max_steps: Optional[int] = None
    task_seed: Optional[int] = None

    def event(self, event_id: str) -> Any:
        return next((e for e in self.events if e.id == event_id), None)


# --- composition -------------------------------------------------------------------


def _compose(config_name: str, overrides: Sequence[str]) -> Any:
    from hydra import compose, initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra

    if GlobalHydra.instance().is_initialized():
        GlobalHydra.instance().clear()
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        return compose(config_name=config_name, overrides=list(overrides))


def suite_task_overrides(suite: str | Path) -> list[str]:
    """Overrides that give the sweep's root config this suite's task set.

    A suite under ``config/benchmark/`` is selected the way a sweep selects
    it (``benchmark=<name>``). Anywhere else (tests) Hydra cannot find it as
    a group option, so its ``override /tasks`` default is applied by hand.
    """
    path = suite_path(suite).resolve()
    if path.parent == suites_dir().resolve():
        return [f"benchmark={path.stem}"]
    defaults = OmegaConf.to_container(OmegaConf.load(path)).get("defaults") or []
    out = []
    for entry in defaults:
        if not isinstance(entry, dict):
            continue
        for key, value in entry.items():
            group = str(key).removeprefix("override ").strip().lstrip("/")
            if group == "tasks":
                names = value if isinstance(value, list) else [value]
                out.append(f"tasks=[{','.join(map(str, names))}]")
    return out


@functools.lru_cache(maxsize=8)
def _root_tasks(overrides: tuple[str, ...]) -> dict:
    """``{task_name: task_config}`` of the suite's root config, resolved the
    way ``identity_from_config`` stores it."""
    config = _compose("config_parallel_tasks", overrides)
    return OmegaConf.to_container(config.tasks, resolve=True)


@dataclass(frozen=True)
class _CellApps:
    irreversible: dict
    events: tuple
    languages: dict
    adversarial_popup: bool
    adversarial_content: bool


@functools.lru_cache(maxsize=64)
def _cell_apps(variation: tuple[str, ...]) -> _CellApps:
    from open_apps.alignment.events import content_languages, load_events
    from open_apps.alignment.irreversible import map_from_apps_config
    from open_apps.alignment.record import adversarial_content, adversarial_popup

    # The sweep adds task_name=<task> here; nothing under `apps` reads it,
    # so it is left out and one compose serves every task of the cell.
    apps = _compose("config", variation).apps
    languages = content_languages(apps)
    events = tuple(
        e.localized(languages.get(e.target_app or ""))
        for e in load_events(apps.get("events"))
    )
    return _CellApps(
        irreversible=map_from_apps_config(apps),
        events=events,
        languages=languages,
        adversarial_popup=adversarial_popup(apps.get("pop_ups")),
        adversarial_content=adversarial_content(apps),
    )


@functools.lru_cache(maxsize=1024)
def _task_facts(
    overrides: tuple[str, ...], task_name: str
) -> tuple[str, str, Optional[str]]:
    """``(class name, goal, goal_style)`` of the instantiated task."""
    from hydra.utils import instantiate

    task = instantiate(_root_tasks(overrides)[task_name], _convert_="all")
    return type(task).__name__, task.goal, getattr(task, "goal_style", None)


def reference_cell(
    suite: str | Path, task_name: str, variation: Sequence[str], seed: int
) -> ReferenceCell:
    """The repo-composed reference for one suite cell. Raises ``KeyError``
    when the suite's task set has no such task."""
    from open_apps.alignment.events import EventScheduler

    overrides = tuple(suite_task_overrides(suite))
    env_args = pinned_env_args(suite)
    tasks = _root_tasks(overrides)
    if task_name not in tasks:
        raise KeyError(f"task {task_name!r} is not in the suite's task set")
    apps = _cell_apps(tuple(variation))
    task_class, goal, goal_style = _task_facts(overrides, task_name)
    scheduler = EventScheduler(list(apps.events), seed=seed) if apps.events else None
    steps = {
        e.id: scheduler.step_for(e.id)
        for e in apps.events
        if scheduler is not None and scheduler.step_for(e.id) is not None
    }
    return ReferenceCell(
        task_name=task_name,
        variation=tuple(variation),
        seed=int(seed),
        # Copies: the cached trees are shared by every record of the cell.
        task_config=copy.deepcopy(tasks[task_name]),
        task_class=task_class,
        goal=goal,
        goal_style=goal_style,
        irreversible=copy.deepcopy(apps.irreversible),
        events=apps.events,
        event_steps=steps,
        adversarial_popup=apps.adversarial_popup,
        adversarial_content=apps.adversarial_content,
        max_steps=env_args["max_steps"],
        task_seed=env_args["task_seed"],
    )


def clear_cache() -> None:
    """Forget composed references (tests that edit config files)."""
    for fn in (_root_tasks, _cell_apps, _task_facts):
        fn.cache_clear()


# --- comparison ----------------------------------------------------------------------


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= _MAX_REPR else text[: _MAX_REPR - 3] + "..."


def diff(record_value: Any, repo_value: Any, path: str) -> list[str]:
    """Every leaf where ``record_value`` differs from ``repo_value``, as
    ``"<path>: record <x>, repo <y>"``. Bools and numbers are told apart
    (``True == 1`` must not hide an edit)."""
    if isinstance(record_value, dict) and isinstance(repo_value, dict):
        out = []
        for key in sorted(set(record_value) | set(repo_value), key=str):
            sub = f"{path}.{key}"
            if key not in record_value:
                out.append(
                    f"{sub}: missing from the record (repo {_short(repo_value[key])})"
                )
            elif key not in repo_value:
                out.append(
                    f"{sub}: not in the repo's config (record {_short(record_value[key])})"
                )
            else:
                out += diff(record_value[key], repo_value[key], sub)
        return out
    if (
        isinstance(record_value, list)
        and isinstance(repo_value, list)
        and len(record_value) == len(repo_value)
    ):
        out = []
        for i, (a, b) in enumerate(zip(record_value, repo_value)):
            out += diff(a, b, f"{path}[{i}]")
        return out
    same = type(record_value) is type(repo_value) and record_value == repo_value
    if (
        not same
        and not isinstance(record_value, bool)
        and not isinstance(repo_value, bool)
    ):
        # 1 vs 1.0 is serialisation, not tampering.
        numbers = (int, float)
        same = (
            isinstance(record_value, numbers)
            and isinstance(repo_value, numbers)
            and float(record_value) == float(repo_value)
        )
    return (
        []
        if same
        else [f"{path}: record {_short(record_value)}, repo {_short(repo_value)}"]
    )


def compare(record: Any, ref: ReferenceCell) -> tuple[list[str], list[tuple[str, int]]]:
    """``(errors, omitted)``.

    ``errors``: where the record's embedded copies disagree with the
    reference, and fired events the cell does not configure.

    ``omitted``: ``(event_id, step)`` for every configured event that should
    have fired -- the episode ran past its trigger step -- but is absent from
    the record (:func:`omitted_events`). Not an error: a failed delivery is a
    legitimate infrastructure outcome. But the episode no longer measures
    what the cell measures, so ``validate`` counts its cell as missing.
    """
    ident = record.identity
    errors = diff(ident.task_config, ref.task_config, "identity.task_config")
    for name in (
        "task_class",
        "goal",
        "goal_style",
        "adversarial_popup",
        "adversarial_content",
    ):
        errors += diff(getattr(ident, name), getattr(ref, name), f"identity.{name}")
    if record.irreversible is None:
        errors.append(
            "irreversible: missing from the record (the run embeds the map it was scored under)"
        )
    else:
        errors += diff(record.irreversible, ref.irreversible, "irreversible")

    seen: set[str] = set()
    for i, event in enumerate(record.events):
        where = f"events[{i}]"
        configured = ref.event(event.id)
        if configured is None:
            ids = sorted(e.id for e in ref.events)
            errors.append(
                f"{where}: {event.id!r} is not an event this cell configures (configured: {ids})"
            )
            continue
        if event.id in seen:
            errors.append(
                f"{where}: {event.id!r} fired twice (an event fires at most once)"
            )
        seen.add(event.id)
        want = configured.record(event.step, created=event.created)
        for key in ("channel", "category", "sender", "body", "forbidden"):
            errors += diff(getattr(event, key), want[key], f"{where}.{key}")
        earliest = ref.event_steps.get(event.id)
        if earliest is not None and event.step < earliest:
            errors.append(
                f"{where}.step: {event.step}, but {event.id!r} cannot fire before step {earliest}"
            )

    errors += trace_errors(record, ref.max_steps)
    return errors, omitted_events(record, ref)


def _has_report(step: Any, nxt: Any, action: str, kind: str, role: str) -> bool:
    """Does the episode's last action carry a claim/refusal? Any of the
    three traces the record keeps of it counts: the action text (a
    multi-action can put it after a click), the task's per-step snapshot
    (``Snapshot.message``), or the chat message it sent, which shows up in
    the next observation's chat delta under ``role``."""
    if action in (step.action or ""):
        return True
    if step.snapshot is not None and step.snapshot.message == kind:
        return True
    return nxt is not None and any(m.role == role for m in nxt.chat_messages)


#: termination reason -> (the action that makes it, Snapshot.message kind,
#: the chat role BrowserGym files its message under -- ``_CLAIM_ROLE`` /
#: ``_REFUSAL_ROLE`` in ``tasks.add_tasks_to_browsergym``, not imported: that
#: module pulls in playwright and wandb).
_REPORTS = {
    "claim": (CLAIM_ACTION, "claim", "assistant"),
    "refusal": (REFUSAL_ACTION, "refusal", "infeasible"),
}


def trace_errors(record: Any, max_steps: Optional[int]) -> list[str]:
    """Where the record's own trace contradicts itself or the step budget.

    BrowserGym numbers observations from 0 (after reset) and ends with the
    observation after the last action, so an ``n``-step episode has ``n + 1``
    steps, indexed ``0..n`` (``summary_info.n_steps`` is ``len - 1``). The
    count is what the Steps column and :func:`omitted_events` read, so it
    must be the trace's: a smaller ``n_steps`` would both flatter efficiency
    and make an event that should have fired look not yet due.

    The termination reason must be one the trace shows: ``max_steps`` only
    at the budget, ``claim`` / ``refusal`` only when the last action made
    one. (A fabricated trace can still agree with itself; see the trust
    model in docs/leaderboard.md.)
    """
    errors: list[str] = []
    steps = record.steps
    n = record.n_steps
    if not steps:
        return ["steps: empty (a record carries its trace, one entry per observation)"]
    bad = [(i, s.step) for i, s in enumerate(steps) if s.step != i]
    for i, got in bad[:3]:
        errors.append(f"steps[{i}].step: {got}, expected {i} (indices run 0..n)")
    if n != len(steps) - 1:
        errors.append(
            f"n_steps: {n}, but the record has {len(steps)} steps "
            f"(n_steps is the number of actions, {len(steps) - 1})"
        )
    if max_steps is not None and n > max_steps:
        errors.append(f"n_steps: {n} exceeds the suite's max_steps {max_steps}")
    reason = record.termination.reason
    if reason == "max_steps" and max_steps is not None and n != max_steps:
        errors.append(
            f"termination.reason 'max_steps' after {n} of {max_steps} steps "
            "(an episode only runs out of steps at the budget)"
        )
    if reason in _REPORTS and 0 < n < len(steps):
        action, kind, role = _REPORTS[reason]
        last, nxt = steps[n - 1], steps[n]
        if not _has_report(last, nxt, action, kind, role):
            errors.append(
                f"termination.reason {reason!r}, but the last action ({_short(last.action)}) "
                f"made no {action} (no {kind!r} snapshot, no {role!r} chat message)"
            )
    elif reason in _REPORTS and n == 0:
        errors.append(f"termination.reason {reason!r} on an episode with no actions")
    return errors


def omitted_events(record: Any, ref: ReferenceCell) -> list[tuple[str, int]]:
    """``(event_id, step)`` of configured events the record should show but
    does not: the episode ran past the event's trigger step, so it fired (or
    should have) in the validate after that step's action.

    Why this cannot simply be a warning: dropping an event line is the
    cheapest way to turn a ``complied`` episode into a clean one -- the
    scorer only checks ``forbidden`` predicates of events the record says
    fired. Failing the bundle would punish honest delivery failures; counting
    the cell as covered would reward the edit. Missing is the honest middle:
    the bundle stays valid but ``partial`` until the cell is re-run.
    """
    fired = {event.id for event in record.events}
    # The trace's own count, not the claimed one (``trace_errors`` fails a
    # record where they differ; this keeps a smaller claim from excusing an
    # event even before that).
    n_steps = max(record.n_steps, len(record.steps) - 1)
    return [
        (event_id, step)
        for event_id, step in sorted(ref.event_steps.items())
        if event_id not in fired and n_steps > step
    ]


def rebased(record: Any, ref: ReferenceCell) -> Any:
    """``record`` with every scoring input that comes from config replaced by
    the reference: task config, adversarial flags, irreversible map, and each
    fired event's definition (its step and delivery outcome stay the run's).
    Events the cell does not configure are dropped (``compare`` fails them).
    """
    from open_apps.alignment.record import EventRecord

    events = []
    for event in record.events:
        configured = ref.event(event.id)
        if configured is not None:
            events.append(
                EventRecord(**configured.record(event.step, created=event.created))
            )
    identity = dataclasses.replace(
        record.identity,
        task_config=ref.task_config,
        adversarial_popup=ref.adversarial_popup,
        adversarial_content=ref.adversarial_content,
    )
    return dataclasses.replace(
        record, identity=identity, events=events, irreversible=ref.irreversible
    )
