"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

What a leaderboard row's model page shows: absolute totals, the launch
config, breakdowns by app / layout, theme and device, and every episode
with its action log -- all read from the records, never from a summary.

Shared by ``build`` (submission bundles) and ``preview`` (a logs dir), so
both pages show the same numbers for the same episodes. Everything that
reaches the page goes through the bundle redaction: configs through
``bundle.redact`` (keys, hostnames) and free text (reasoning, actions,
errors, chat, commands) through ``bundle.scrub_text``, because a saved
preview is a file people pass around.

"Mistake" here is an episode whose task check failed (``task_success`` is
False); why it ended (out of steps, a wrong claim, a refusal, an error) is
the record's own termination reason, not a new metric.
"""

from __future__ import annotations

import functools
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from open_apps.leaderboard import bundle as B

# The page's dimensions, in its order. ``app`` is the task's app; ``layout``
# is that app's layout in the episode's cell.
DIMENSIONS = ("app", "layout", "theme", "device")
DEFAULT_THEME = "default"
DEFAULT_LAYOUT = "default"
DEFAULT_DEVICE = "desktop"
MULTI_APP = "multi-app"
OTHER_APP = "other"

# Long free text is cut here: a page with every episode's log inline must
# stay loadable. The reasoning in real runs averages well under 100 chars.
_MAX_TEXT = 1000
# State changes listed per episode (a runaway agent can make hundreds).
_MAX_CHANGES = 20
# A config value longer than this is shown folded (prompts, action lists).
LONG_VALUE = 120

# Task class -> the app (config group name) it acts on. Wrappers
# (AmbiguityTask, AbstentionTask) and composites are resolved from the tasks
# they wrap; anything unlisted falls back to where the agent was.
_TASK_APPS = {
    "AddEventTask": "calendar",
    "RemoveEventTask": "calendar",
    "AddToDoTask": "todo",
    "MarkToDoDoneTask": "todo",
    "DeleteToDoTask": "todo",
    "SendMessageTask": "messenger",
    "AddContactTask": "contacts",
    "EditContactTask": "contacts",
    "DeleteContactTask": "contacts",
    "SavePlaceTask": "maps",
    "RemoveLandmarkTask": "maps",
    "BuyWithCardTask": "onlineshop",
    "DeclinedCardPurchaseTask": "onlineshop",
    "DownloadStatementTask": "openbanking",
    "DeleteFileTask": "files",
    "RestoreFileTask": "files",
    "PermanentlyDeleteTask": "files",
    "RenameFileTask": "files",
    "MoveFileTask": "files",
    "EditFileTask": "code_editor",
}
# NavigateToAppTask's ``target_app`` and URL path segments -> config group.
_URL_APPS = {
    "todo": "todo",
    "calendar": "calendar",
    "messages": "messenger",
    "messenger": "messenger",
    "contacts": "contacts",
    "codeeditor": "code_editor",
    "map": "maps",
    "maps": "maps",
    "uilibrary": "ui_library",
    "openbanking": "openbanking",
    "onlineshop": "onlineshop",
    "files": "files",
}


def _clip(text: Any, limit: int = _MAX_TEXT) -> Optional[str]:
    """Scrubbed, capped free text; None for nothing."""
    if text is None or text == "":
        return None
    text, _ = B.scrub_text(str(text))
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- one episode ---------------------------------------------------------------------


def _apps_in(config: Any) -> set[str]:
    """Apps a task config acts on, through wrappers and composites."""
    if isinstance(config, list):
        return set().union(*(_apps_in(c) for c in config)) if config else set()
    if not isinstance(config, dict):
        return set()
    cls = str(config.get("_target_", "")).rsplit(".", 1)[-1]
    if cls in _TASK_APPS:
        return {_TASK_APPS[cls]}
    if cls == "NavigateToAppTask":
        target = str(config.get("target_app", ""))
        return {_URL_APPS.get(target, target)} if target else set()
    return set().union(
        *(_apps_in(v) for k, v in config.items() if isinstance(v, (dict, list)))
    )


def _url_app(urls: Iterable[Optional[str]]) -> Optional[str]:
    """The app the agent spent most steps in, from the step URLs."""
    from urllib.parse import urlsplit

    seen: Counter = Counter()
    for url in urls:
        try:
            first = (urlsplit(url or "").path.strip("/").split("/") or [""])[0]
        except ValueError:
            continue
        if first in _URL_APPS:
            seen[_URL_APPS[first]] += 1
    return seen.most_common(1)[0][0] if seen else None


def task_app(record: Any) -> str:
    """The app an episode's task acts on (a config group name), ``multi-app``
    for a task across several, else where the agent mostly was."""
    ident = record.identity
    cls = ident.task_class
    if cls in _TASK_APPS:
        return _TASK_APPS[cls]
    apps = _apps_in(ident.task_config or {})
    if len(apps) == 1:
        return next(iter(apps))
    if len(apps) > 1:
        return MULTI_APP
    return _url_app(s.url for s in record.steps) or OTHER_APP


def cell_values(record: Any, app: str) -> dict[str, str]:
    """``{layout, theme, device}`` of an episode's cell, defaults filled in."""
    from open_apps.alignment.aggregate import axes_from_variation

    axes = axes_from_variation(record.identity.variation)
    return {
        "layout": axes.get(f"{app}.layout", DEFAULT_LAYOUT),
        "theme": axes.get("theme", DEFAULT_THEME),
        "device": axes.get("device", DEFAULT_DEVICE),
    }


def _agent_message(record: Any) -> Optional[str]:
    """The agent's last chat message: its claim, refusal or answer. Step 0's
    chat is the env's greeting and the goal, never the agent's."""
    for step in reversed(record.steps[1:]):
        for msg in reversed(step.chat_messages or []):
            if msg.role == "assistant" and msg.message:
                return msg.message
    return None


def _change_label(change: Any) -> str:
    item = change.after or change.before or {}
    name = next(
        (
            item.get(k)
            for k in ("title", "name", "path", "user", "description", "sku")
            if item.get(k)
        ),
        change.key,
    )
    return f"{change.app} · {change.collection} · {change.op} · {name}"


def _changes(record: Any) -> Optional[list[str]]:
    """What the agent changed, initial -> final (None when unreadable)."""
    if getattr(record, "unscorable", None):
        return None
    from open_apps.alignment.changes import agent_changes

    try:
        found = agent_changes(record.initial_state, record.final_state)
    # A state shape the differ does not know must not sink the page.
    except Exception:  # noqa: BLE001
        return None
    labels = [_clip(_change_label(c), 200) for c in found]
    if len(labels) > _MAX_CHANGES:
        labels = labels[:_MAX_CHANGES] + [f"… and {len(labels) - _MAX_CHANGES} more"]
    return labels


def find_media(exp_dir: Optional[Path]) -> dict:
    """Screenshots (by step) and the recording BrowserGym left in an exp
    dir; empty for a bundle or a dir without them."""
    if not exp_dir or not Path(exp_dir).is_dir():
        return {}
    exp_dir = Path(exp_dir)
    steps = []
    for png in exp_dir.glob("screenshot_step_*.png"):
        try:
            steps.append(int(png.stem.rsplit("_", 1)[-1]))
        except ValueError:
            continue
    video = sorted((exp_dir / "task_video").glob("*.webm"))
    out: dict[str, Any] = {}
    if steps:
        out["screenshots"] = sorted(steps)
    if video:
        out["video"] = True
    return out


def episode_entry(index: int, record: Any, row: dict, media: dict) -> dict:
    """One episode as the model page lists it."""
    ident = record.identity
    app = task_app(record)
    trace = []
    for s in record.steps:
        if s.action is None and not s.think:
            continue  # the final observation: no action taken from it
        trace.append(
            {
                "step": s.step,
                "action": _clip(s.action, 400),
                "think": _clip(s.think),
                "error": _clip(s.last_action_error, 400),
            }
        )
    term = record.termination
    return {
        "id": index,
        "task": ident.task_name,
        "goal": _clip(ident.goal, 400),
        "app": app,
        **cell_values(record, app),
        "cell": row.get("cell"),
        "success": row.get("task_success"),
        "steps": record.n_steps,
        "tokens_in": row.get("tokens_in"),
        "tokens_out": row.get("tokens_out"),
        "wall_s": record.wall_s,
        "termination": term.reason,
        "final_action": _clip(term.final_action, 400),
        "message": _clip(_agent_message(record), 600),
        "error": _clip(term.err_msg, 600),
        "changes": _changes(record),
        "command": _clip(ident.command, 2000),
        "git_commit": ident.git_commit,
        "git_dirty": ident.git_dirty,
        "trace": trace,
        "media": media,
    }


# --- the model ------------------------------------------------------------------------


def _sum(values: Iterable[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def totals(episodes: Sequence[dict]) -> dict:
    """Absolute numbers across every episode of a row."""
    scored = [e for e in episodes if e["success"] is not None]
    wins = [e for e in scored if e["success"]]
    steps = [e["steps"] for e in episodes]
    win_steps = [e["steps"] for e in wins]
    return {
        "tasks": len(episodes),
        "distinct_tasks": len({e["task"] for e in episodes}),
        "apps": len({e["app"] for e in episodes}),
        "scored": len(scored),
        "successes": len(wins),
        "mistakes": len(scored) - len(wins),
        "steps": sum(steps),
        "steps_mean": statistics.fmean(steps) if steps else None,
        "steps_median": statistics.median(steps) if steps else None,
        "steps_mean_success": statistics.fmean(win_steps) if win_steps else None,
        "tokens_in": _sum(e["tokens_in"] for e in episodes),
        "tokens_out": _sum(e["tokens_out"] for e in episodes),
    }


def breakdowns(episodes: Sequence[dict]) -> dict[str, list[dict]]:
    """Per dimension, one entry per value: tasks, successes, mistakes and
    steps, most tasks first. ``layout`` is keyed by app and layout (a todo
    kanban board and a calendar agenda are different layouts)."""
    groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for e in episodes:
        groups["app"][e["app"]].append(e)
        groups["layout"][f"{e['app']}|{e['layout']}"].append(e)
        groups["theme"][e["theme"]].append(e)
        groups["device"][e["device"]].append(e)
    out = {}
    for dim in DIMENSIONS:
        entries = []
        for value, eps in groups[dim].items():
            t = totals(eps)
            entry = {
                "value": value,
                "tasks": t["tasks"],
                "successes": t["successes"],
                "mistakes": t["mistakes"],
                "success_rate": t["successes"] / t["scored"] if t["scored"] else None,
                "steps_mean": t["steps_mean"],
            }
            if dim == "layout":
                entry["app"], entry["value"] = value.split("|", 1)
            if dim == "device":
                entry["viewport"] = device_viewport(value)
            entries.append(entry)
        out[dim] = sorted(entries, key=lambda x: (-x["tasks"], str(x["value"])))
    return out


@functools.lru_cache(maxsize=None)
def device_viewport(name: str) -> Optional[list[int]]:
    """``[width, height]`` from ``config/device/<name>.yaml``, if it exists."""
    from open_apps import config_dir

    path = config_dir() / "device" / f"{name}.yaml"
    try:
        viewport = (B.read_yaml(path) or {}).get("viewport")
    except Exception:  # noqa: BLE001 -- a missing or odd device file just has no size
        return None
    return [int(v) for v in viewport] if isinstance(viewport, list) else None


def _flatten(node: Any, prefix: str = "") -> list[list]:
    """``[[dotted key, value], ...]``; lists of scalars stay one value."""
    if isinstance(node, dict):
        out = []
        for k, v in node.items():
            out += _flatten(v, f"{prefix}.{k}" if prefix else str(k))
        return out
    if isinstance(node, list) and any(isinstance(v, (dict, list)) for v in node):
        out = []
        for i, v in enumerate(node):
            out += _flatten(v, f"{prefix}[{i}]")
        return out
    if isinstance(node, list):
        node = ", ".join(map(str, node))
    elif isinstance(node, str):
        node, _ = B.scrub_text(node)
    return [[prefix, node]]


# Sections of a run config the model page shows, in order: what the agent
# is, what it saw, and how the episode was bounded. Keys outside these
# (paths, wandb, per-job ports) say where a run was, not what it was.
_RUN_SECTIONS = (
    ("Agent", "agent"),
    ("Environment", "browsergym_env_args"),
    ("Device", "device"),
)
_RUN_KEYS = (
    "task_termination",
    "suite_version",
    "benchmark",
    "seed",
    "record_video",
)


def config_sections(
    agent_config: Optional[dict] = None, run_config: Optional[dict] = None
) -> list[dict]:
    """The launch config as ``[{"name", "items": [[key, value], ...]}]``,
    redacted like a bundle's agent.yaml. ``agent_config`` is a bundle's
    agent.yaml; ``run_config`` a run's whole saved config.yaml."""
    sections = []
    run = run_config or {}
    agent = agent_config if agent_config is not None else run.get("agent")
    for name, key in _RUN_SECTIONS:
        node = agent if key == "agent" else run.get(key)
        if not isinstance(node, dict) or not node:
            continue
        if key == "browsergym_env_args":
            # The env args carry the device too; it has its own section.
            node = {k: v for k, v in node.items() if k != "device"}
        redacted, _ = B.redact(node)
        if key == "agent":
            redacted = B.label_models(redacted)
        sections.append({"name": name, "items": _flatten(redacted)})
    rest = {k: run[k] for k in _RUN_KEYS if run.get(k) is not None}
    if rest:
        sections.append({"name": "Run", "items": _flatten(B.redact(rest)[0])})
    return sections


def sweep_command(record: Any) -> Optional[str]:
    """The sweep an episode came from, from its one-episode command: the
    ``launch_agent.py --config-name=config_parallel_tasks`` that reruns one
    cell becomes the ``launch_parallel_agents.py`` that runs them all, minus
    the cell's own overrides (its variation and task_name). None when the
    record has no command or was not a sweep job."""
    import shlex

    command = record.identity.command
    if not command:
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    head = ["uv", "run", "launch_agent.py", "--config-name=config_parallel_tasks"]
    if words[: len(head)] != head:
        return None
    cell = {str(v).lstrip("+") for v in record.identity.variation}
    kept = [
        w
        for w in words[len(head) :]
        if not w.startswith("task_name=") and w.lstrip("+") not in cell
    ]
    return _clip(shlex.join(["uv", "run", "launch_parallel_agents.py", *kept]), 2000)


def _read_config(path: Optional[Path]) -> Optional[dict]:
    if not path:
        return None
    try:
        from omegaconf import OmegaConf

        data = OmegaConf.to_container(OmegaConf.load(path), resolve=False)
    except Exception:  # noqa: BLE001 -- an unreadable config just isn't shown
        return None
    return data if isinstance(data, dict) else None


def model_details(
    records: Sequence[Any],
    rows: Sequence[dict],
    agent_config: Optional[dict] = None,
    config_paths: Sequence[Optional[Path]] = (),
    exp_dirs: Sequence[Optional[Path]] = (),
) -> dict:
    """Everything a row's model page shows, from its records and their
    scorer rows (same order). ``config_paths`` / ``exp_dirs`` are the
    preview's: each record's run config.yaml and exp dir, for the config
    table and for screenshots and recordings."""
    exp_dirs = list(exp_dirs) or [None] * len(records)
    episodes = [
        episode_entry(i, rec, row, find_media(exp))
        for i, (rec, row, exp) in enumerate(zip(records, rows, exp_dirs))
    ]
    # The config of the latest run; how many distinct agent configs the
    # row's episodes came from says whether that is the whole story.
    run_config = next(
        (c for c in map(_read_config, reversed(list(config_paths))) if c), None
    )
    hashes = {r.identity.agent_config_hash for r in records} - {None}
    commits = Counter(r.identity.git_commit for r in records if r.identity.git_commit)
    return {
        "totals": totals(episodes),
        "breakdowns": breakdowns(episodes),
        "config": config_sections(agent_config, run_config),
        "config_variants": len(hashes),
        "git_commit": commits.most_common(1)[0][0] if commits else None,
        # The latest episode's sweep; else its one-episode command.
        "command": next(
            (c for c in map(sweep_command, reversed(list(records))) if c),
            next((e["command"] for e in reversed(episodes) if e["command"]), None),
        ),
        "episodes": episodes,
    }
