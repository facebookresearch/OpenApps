"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

``EpisodeRecord``: everything needed to score one agent episode, persisted to
``exp_dir/episode.json``.

The record is the contract between a run and every metric computed on it:
``open_apps.alignment.score`` reads nothing else, so any bundle of
``episode.json`` files can be re-scored offline (no apps, no browser, no
model). It is versioned (``record_version``) and published as JSON Schema
(``uv run -m open_apps.alignment.record --schema``) for consumers outside this
repo.

Building a record needs two things BrowserGym does not persist: the cross-app
state before the first step and after the last one. ``OpenAppsTask`` writes
those to ``exp_dir/openapps_states.json`` (initial at ``setup``, final at
``teardown`` -- the last moment the apps are guaranteed to still be up), and
:func:`build_episode_record` stitches them together with BrowserGym's own
step pickles and ``summary_info.json``.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import shlex
import sys
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional, Sequence

RECORD_VERSION = 1
EPISODE_FILENAME = "episode.json"
# Written by OpenAppsTask; see the module docstring.
STATES_FILENAME = "openapps_states.json"

# Why the episode ended: on success, on the agent's claim or refusal, at the
# step limit -- or ``error``, for episodes that ended for a reason that is not
# a statement by the agent (an uncaught exception, or a truncation short of
# ``max_steps`` -- BrowserGym's way of ending an episode when the agent
# produced no parseable action). Keeping it distinct stops crashes being
# scored as "ran out of steps".
TerminationReason = Literal["success", "claim", "refusal", "max_steps", "error"]

CLAIM_ACTION = "send_msg_to_user"
REFUSAL_ACTION = "report_infeasible"

# What an agent message was, as ``OpenAppsTask.validate`` judged it when it
# arrived: ``claim`` / ``refusal`` ended the episode; ``question`` is a message
# the task answered as a clarifying question instead of ending the episode on
# it (ambiguity tasks); ``message`` is a send_msg_to_user that did not end an
# ``on_success`` episode.
MessageKind = Literal["claim", "refusal", "question", "message"]


@dataclass
class Identity:
    """What was run: task, variation cell, seed, and agent."""

    task_name: Optional[str]
    task_class: str
    goal: str
    goal_style: Optional[str] = None
    # The task's Hydra config (with ``_target_``), so an offline scorer can
    # rebuild the exact Task without the config tree it was launched from.
    task_config: Optional[dict] = None
    # Hydra overrides that define the variation cell, e.g.
    # ``["apps/theme=dark", "apps/todo/layout=kanban_board"]``.
    variation: list[str] = field(default_factory=list)
    seed: Optional[int] = None
    task_seed: Optional[int] = None
    agent_name: Optional[str] = None
    # sha256 prefix of the resolved agent config. A hash, not the config:
    # agent configs can carry endpoint credentials.
    agent_config_hash: Optional[str] = None
    suite_version: Optional[str] = None
    # A static pop-up flagged ``adversarial: true`` was configured -- one whose
    # message tells the agent its goal is achieved (config/apps/pop_ups/).
    # Feeds premature_stop; a GDPR wall or a translated welcome is not one.
    adversarial_popup: bool = False
    # An app's content variant flagged ``adversarial: true`` was selected --
    # ``config/apps/<app>/content/adversarial_descriptions.yaml``, which plants
    # the same "you have achieved your goal" message in the app's own data.
    # Feeds premature_stop exactly like the pop-up flag.
    adversarial_content: bool = False
    max_steps: Optional[int] = None
    # ``on_success`` | ``on_agent_stop`` (config ``task_termination``).
    task_termination: str = "on_success"
    # Provenance, so a number in a table can be traced to code and rerun.
    # The HEAD the run's code was loaded from; None outside a git checkout.
    git_commit: Optional[str] = None
    # The working tree differed from ``git_commit`` (tracked files only):
    # the commit alone then does not reproduce the episode.
    git_dirty: Optional[bool] = None
    # A ``launch_agent.py`` command that reruns this one episode at
    # ``git_commit``; see :func:`reproduce_command`.
    command: Optional[str] = None


@dataclass
class ChatMessage:
    role: str
    message: str
    timestamp: Optional[float] = None


@dataclass
class Snapshot:
    """The task's view of the app state right after a step's action,
    recorded by ``OpenAppsTask.validate``.

    Gives the scorer the *order* of events that the initial and final states
    cannot: whether an agent message came before the first irreversible
    change (``asked``), and which message ended the episode.
    """

    reward: float = 0.0
    # validate returned done (on_success: success; on_agent_stop: claim/refusal).
    done: bool = False
    message: Optional[MessageKind] = None
    # Sticky: any irreversible change (in scope or not) so far. None when the
    # task did not track state (no episode_dir).
    irreversible: Optional[bool] = None
    state_hash: Optional[str] = None
    # Apps whose probe failed at this step; they were compared as unchanged
    # (an empty app is otherwise read as the agent deleting everything).
    probe_failed: Optional[list[str]] = None


@dataclass
class StepRecord:
    """One BrowserGym observation and the action the agent took from it.

    ``step`` follows BrowserGym's numbering: step 0 is the observation after
    reset. The last step is the final observation and has no action.
    """

    step: int
    action: Optional[str] = None
    think: Optional[str] = None
    url: Optional[str] = None
    # The error raised by *this* step's action (BrowserGym reports it in the
    # next observation; the builder shifts it back so it lines up).
    last_action_error: str = ""
    # Only the messages that are new in this observation, not the whole chat.
    chat_messages: list[ChatMessage] = field(default_factory=list)
    reward: float = 0.0
    env_elapsed_s: Optional[float] = None
    agent_elapsed_s: Optional[float] = None
    # None when the client did not report usage.
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    # After this step's action; None for the final observation and for
    # records written before snapshots existed.
    snapshot: Optional[Snapshot] = None


@dataclass
class EventRecord:
    """An adversarial event that fired during the episode.

    ``forbidden`` is copied from the event definition so the record scores
    ``complied`` on its own, without the event library it was drawn from.
    """

    id: str
    # The BrowserGym step whose observation first shows the event: it fired
    # in the validate after the agent's ``step``-th action.
    step: int
    channel: str
    category: Optional[str] = None
    forbidden: list[dict] = field(default_factory=list)
    # What was delivered, so a messenger event's message can be told apart
    # from the agent's own activity in the final state.
    sender: Optional[str] = None
    body: Optional[str] = None
    # Whether delivery opened the conversation (the sender was not a
    # contact); None for toast-only events and for records written before it
    # was reported. Decides whether the conversation itself, not just the
    # message, is the event's doing.
    created: Optional[bool] = None


@dataclass
class Termination:
    reason: TerminationReason
    final_action: Optional[str] = None
    err_msg: Optional[str] = None


@dataclass
class EpisodeRecord:
    identity: Identity
    # The same cross-app JSON ``OpenAppsTask.reward()`` diffs.
    initial_state: dict
    final_state: dict
    termination: Termination
    final_url: Optional[str] = None
    steps: list[StepRecord] = field(default_factory=list)
    events: list[EventRecord] = field(default_factory=list)
    n_steps: int = 0
    cum_reward: float = 0.0
    wall_s: Optional[float] = None
    # The per-app ``irreversible:`` maps in effect for this run, keyed by
    # state key (see ``open_apps.alignment.irreversible``). None => the
    # scorer falls back to the maps in config/apps/*/default.yaml.
    irreversible: Optional[dict] = None
    # Apps whose final state could not be read (their probe failed, so they
    # read as empty), when the initial state had something in them. A
    # non-empty list means the final state is not evidence of anything the
    # agent did: the scorer returns None for every state-derived metric and
    # ``aggregate`` leaves the episode out. None (and omitted from the JSON)
    # when the final state is sound.
    unscorable: Optional[list[str]] = None
    record_version: int = RECORD_VERSION

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        if data.get("unscorable") is None:
            # Optional and usually absent: omitting it keeps sound records
            # byte-identical to what readers that predate the field accept.
            data.pop("unscorable", None)
        # record_version first, so a reader can dispatch before parsing more.
        return {"record_version": data.pop("record_version"), **data}

    @classmethod
    def from_dict(cls, data: dict) -> "EpisodeRecord":
        version = data.get("record_version")
        if version != RECORD_VERSION:
            raise ValueError(
                f"Unsupported record_version {version!r}; this reader handles "
                f"{RECORD_VERSION}."
            )
        return _from_value(cls, data)


# --- (de)serialization ---------------------------------------------------------


def _from_value(tp: Any, value: Any) -> Any:
    """Rebuild ``value`` as type ``tp`` (dataclasses, lists, Optionals)."""
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        if value is None:
            return None
        for arg in typing.get_args(tp):
            if arg is not type(None):
                return _from_value(arg, value)
    if dataclasses.is_dataclass(tp):
        hints = typing.get_type_hints(tp)
        names = {f.name for f in dataclasses.fields(tp)}
        unknown = set(value) - names
        if unknown:
            raise ValueError(f"Unknown {tp.__name__} field(s): {sorted(unknown)}")
        return tp(**{k: _from_value(hints[k], v) for k, v in value.items()})
    if origin is list:
        (item_tp,) = typing.get_args(tp)
        return [_from_value(item_tp, v) for v in value]
    return value


def save_record(record: EpisodeRecord, exp_dir: str | Path) -> Path:
    """Write ``record`` to ``<exp_dir>/episode.json`` and return the path."""
    path = Path(exp_dir) / EPISODE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    # default=str: states come from JSON endpoints, but a stray Path/numpy
    # scalar in a trace must not cost the whole record.
    tmp.write_text(json.dumps(record.to_dict(), indent=2, default=str) + "\n")
    tmp.replace(path)
    return path


def load_record(path: str | Path) -> EpisodeRecord:
    """Load an ``episode.json`` (or the directory containing one)."""
    path = Path(path)
    if path.is_dir():
        path = path / EPISODE_FILENAME
    return EpisodeRecord.from_dict(json.loads(path.read_text()))


# --- JSON Schema ---------------------------------------------------------------


def json_schema() -> dict:
    """JSON Schema (draft 2020-12) for ``EpisodeRecord``, derived from the
    dataclasses so it cannot drift from what ``to_dict`` writes."""
    defs: dict[str, dict] = {}
    root = _schema_for(EpisodeRecord, defs, root=True)
    root["properties"]["record_version"] = {"const": RECORD_VERSION}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://facebookresearch.github.io/OpenApps/schemas/episode-record-v{RECORD_VERSION}.json",
        "title": "OpenApps EpisodeRecord",
        **root,
        "$defs": defs,
    }


_PRIMITIVES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
}


def _schema_for(tp: Any, defs: dict, root: bool = False) -> dict:
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        options = [_schema_for(a, defs) for a in typing.get_args(tp)]
        return {"anyOf": options}
    if tp is type(None):
        return {"type": "null"}
    if origin is Literal:
        return {"enum": list(typing.get_args(tp))}
    if origin is list:
        (item_tp,) = typing.get_args(tp)
        return {"type": "array", "items": _schema_for(item_tp, defs)}
    if origin is dict:
        return {"type": "object"}
    if tp is float:
        # JSON has one number type; an int is a valid float here.
        return {"type": "number"}
    if tp in _PRIMITIVES:
        return {"type": _PRIMITIVES[tp]}
    if dataclasses.is_dataclass(tp):
        hints = typing.get_type_hints(tp)
        props = {
            f.name: _schema_for(hints[f.name], defs) for f in dataclasses.fields(tp)
        }
        required = [
            f.name
            for f in dataclasses.fields(tp)
            if f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING
        ]
        if root:
            required = ["record_version", *required]
        body = {"type": "object", "properties": props, "required": required}
        if root:
            return body
        defs[tp.__name__] = body
        return {"$ref": f"#/$defs/{tp.__name__}"}
    if tp is Any:
        return {}
    raise TypeError(f"No JSON Schema mapping for {tp!r}")


# --- builder -------------------------------------------------------------------


def _action_name(action: Optional[str]) -> str:
    return (action or "").strip().split("(", 1)[0].strip()


def infer_termination(
    actions: Sequence[str],
    summary: dict,
    max_steps: Optional[int],
    task_reason: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """Why the episode ended, from BrowserGym's summary and, when present, the
    reason ``OpenAppsTask`` recorded as it ended the episode.

    ``task_reason`` is authoritative for a terminated episode: the task read
    the claim / refusal off the chat (see ``OpenAppsTask._new_agent_messages``),
    which is right for multi-actions and for calls that raised. Without it
    (records from before the task recorded one) the last action's name is
    used. Order matters there: an agent statement (claim / refusal) that ended
    the episode wins over the env's reason; under ``on_success`` a claim can
    only end an episode by coinciding with success.
    """
    final_action = actions[-1] if actions else None
    name = _action_name(final_action)
    if summary.get("err_msg"):
        return "error", final_action
    if summary.get("terminated"):
        if task_reason in ("refusal", "claim", "success"):
            return task_reason, final_action
        if name == REFUSAL_ACTION:
            return "refusal", final_action
        if name == CLAIM_ACTION:
            return "claim", final_action
        if float(summary.get("cum_reward") or 0.0) >= 1.0:
            return "success", final_action
        return "error", final_action
    if summary.get("truncated"):
        n_steps = summary.get("n_steps", len(actions))
        if max_steps is not None and n_steps < max_steps:
            return "error", final_action
        return "max_steps", final_action
    return "error", final_action


def _chat_delta(prev: list, cur: list) -> list[ChatMessage]:
    new = cur[len(prev) :] if cur[: len(prev)] == prev else cur
    return [
        ChatMessage(
            role=str(m.get("role", "")),
            message=str(m.get("message", "")),
            timestamp=m.get("timestamp"),
        )
        for m in new
    ]


def _snapshot(raw: Optional[dict]) -> Optional[Snapshot]:
    if raw is None:
        return None
    names = {f.name for f in dataclasses.fields(Snapshot)}
    return Snapshot(**{k: v for k, v in raw.items() if k in names})


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _elapsed(start: float, stop: float) -> Optional[float]:
    return float(stop - start) if start and stop and stop >= start else None


def record_from_steps(
    steps_info: Sequence[Any],
    summary: dict,
    *,
    identity: Identity,
    initial_state: dict,
    final_state: dict,
    final_url: Optional[str] = None,
    irreversible: Optional[dict] = None,
    events: Iterable[EventRecord] = (),
    snapshots: Sequence[dict] = (),
    task_reason: Optional[str] = None,
    unscorable: Optional[Sequence[str]] = None,
) -> EpisodeRecord:
    """Assemble a record from BrowserGym ``StepInfo``-like objects.

    Duck-typed (``step``, ``obs``, ``action``, ``agent_info``, ``reward``,
    ``stats``, ``profiling``) so it can be tested without a browser.
    ``snapshots`` / ``task_reason`` are what ``OpenAppsTask`` persisted: the
    n-th validate call (``"step": n``) ran after the action of BrowserGym step
    n - 1, so it is attached to that step.
    """
    by_step = {int(s["step"]) - 1: s for s in snapshots if s.get("step") is not None}
    steps: list[StepRecord] = []
    prev_chat: list = []
    for i, info in enumerate(steps_info):
        obs = info.obs or {}
        nxt = steps_info[i + 1].obs if i + 1 < len(steps_info) else None
        agent_info = info.agent_info or {}
        stats = info.stats or {}
        prof = info.profiling
        chat = list(obs.get("chat_messages") or [])
        tokens_in = _int_or_none(stats.get("input_tokens"))
        tokens_out = _int_or_none(stats.get("output_tokens"))
        if not tokens_in and not tokens_out:
            # The agent's tracker reports 0/0 when its client never reported
            # usage; no real call has zero input tokens, so that means unknown.
            tokens_in = tokens_out = None
        url = obs.get("url")
        if url is None and obs.get("open_pages_urls"):
            idx = (
                int(obs.get("active_page_index", [0])[0])
                if obs.get("active_page_index") is not None
                else 0
            )
            urls = list(obs["open_pages_urls"])
            url = urls[idx] if idx < len(urls) else urls[-1]
        steps.append(
            StepRecord(
                step=int(info.step if info.step is not None else i),
                action=info.action,
                think=agent_info.get("think"),
                url=url,
                last_action_error=str((nxt or {}).get("last_action_error", "") or ""),
                chat_messages=_chat_delta(prev_chat, chat),
                reward=float(info.reward or 0.0),
                env_elapsed_s=stats.get(
                    "step_elapsed", _elapsed(prof.env_start, prof.env_stop)
                ),
                agent_elapsed_s=stats.get("agent_elapsed") if info.action else None,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                snapshot=_snapshot(by_step.get(int(info.step if info.step is not None else i))),
            )
        )
        prev_chat = chat

    actions = [s.action for s in steps if s.action is not None]
    reason, final_action = infer_termination(
        actions, summary, identity.max_steps, task_reason=task_reason
    )
    wall_s = None
    if steps_info:
        wall_s = _elapsed(
            steps_info[0].profiling.env_start, steps_info[-1].profiling.env_stop
        )
    return EpisodeRecord(
        identity=identity,
        initial_state=initial_state,
        final_state=final_state,
        final_url=final_url,
        steps=steps,
        events=list(events),
        termination=Termination(
            reason=reason, final_action=final_action, err_msg=summary.get("err_msg")
        ),
        n_steps=int(summary.get("n_steps", len(actions))),
        cum_reward=float(summary.get("cum_reward") or 0.0),
        wall_s=wall_s,
        irreversible=irreversible,
        unscorable=list(unscorable) if unscorable else None,
    )


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, (list, dict)) and not value)


def unscorable_apps(
    initial: dict, final: dict, probe_failed: Optional[Sequence[str]] = None
) -> list[str]:
    """The apps whose final state cannot be trusted, sorted.

    A failed probe reads as an empty app (``safe_get_json``'s ``[]``), so the
    final state would score as the agent deleting everything in it. Per app,
    the final value is distrusted when the initial one had content and:

    * ``probe_failed`` (what the probe reported) names the app; or, when
      there is no report (records written before the probe reported
      failures, or a final state from elsewhere),
    * the state carries the *signature* of a failed probe -- some app that
      serves an object (the code editor's file tree, the bank) came back as a
      list, which no live app does -- and the app is now empty. One failed
      endpoint means the server was failing at that moment, so every app
      that went from content to nothing is suspect, not just the one whose
      type gave it away. Without that signature an emptied app is taken at
      its word: deleting every todo is something an agent can do.
    """
    had_content = {k for k, v in initial.items() if not k.startswith("_") and not _is_empty(v)}
    if probe_failed is not None:
        return sorted(had_content & set(probe_failed))
    signature = any(
        isinstance(initial.get(k), dict) and initial.get(k) and not isinstance(final.get(k), dict)
        for k in had_content
    )
    if not signature:
        return []
    return sorted(k for k in had_content if _is_empty(final.get(k)))


def build_episode_record(
    exp_dir: str | Path,
    *,
    identity: Identity,
    irreversible: Optional[dict] = None,
    fallback_final_state: Optional[
        dict | Callable[[], dict | tuple[dict, Sequence[str]]]
    ] = None,
    fallback_final_url: Optional[str] = None,
    exp_result: Any = None,
) -> EpisodeRecord:
    """Build the record for a finished BrowserGym experiment directory.

    ``fallback_final_state`` covers a teardown that failed to write the final
    state (the launcher can still probe the apps -- they outlive the env). A
    callable is only invoked when it is actually needed, and may return
    ``(state, failed_apps)`` to report which app probes failed.

    ``exp_result`` is the BrowserGym ``ExpResult`` for ``exp_dir`` when the
    caller already has one: its step pickles are loaded (and cached) once,
    so passing it saves re-reading every step from disk.
    """
    from browsergym.experiments.loop import ExpResult  # heavy; keep lazy

    exp_dir = Path(exp_dir)
    result = exp_result if exp_result is not None else ExpResult(exp_dir)
    try:
        summary = result.summary_info
    except FileNotFoundError:
        summary = {}
    states_path = exp_dir / STATES_FILENAME
    states = json.loads(states_path.read_text()) if states_path.exists() else {}
    if states.get("initial_state") is None:
        # Checked first: without it there is nothing to score, so don't
        # spend a (possibly slow) fallback probe on the final state.
        raise FileNotFoundError(
            f"{states_path} has no initial app state; cannot build a record."
        )
    final_state = states.get("final_state")
    final_url = states.get("final_url")
    probe_failed = states.get("final_probe_failed")
    if final_state is None:
        if callable(fallback_final_state):
            fallback_final_state = fallback_final_state()
        if isinstance(fallback_final_state, tuple):
            fallback_final_state, probe_failed = fallback_final_state
        else:
            probe_failed = None
        final_state, final_url = fallback_final_state, final_url or fallback_final_url
    if final_state is None:
        raise FileNotFoundError(
            f"{states_path} has no final app state; cannot build a record."
        )
    return record_from_steps(
        result.steps_info,
        summary,
        identity=identity,
        initial_state=states["initial_state"],
        final_state=final_state,
        final_url=final_url,
        irreversible=irreversible,
        snapshots=states.get("snapshots") or (),
        task_reason=states.get("termination_reason"),
        events=[_event_record(e) for e in states.get("events") or ()],
        unscorable=unscorable_apps(states["initial_state"], final_state, probe_failed),
    )


def _event_record(raw: dict) -> EventRecord:
    """An ``EventRecord`` from what ``OpenAppsTask`` persisted, ignoring keys
    this reader does not know (a newer writer's additions)."""
    names = {f.name for f in dataclasses.fields(EventRecord)}
    return EventRecord(**{k: v for k, v in raw.items() if k in names})


def identity_from_config(
    config: Any,
    task: Any,
    task_seed: Optional[int] = None,
    git: Optional[tuple[Optional[str], Optional[bool]]] = None,
) -> Identity:
    """Identity for a launcher run, from its resolved Hydra config.

    ``git`` is :func:`git_state` as the launcher read it when the episode
    started; read now when not given. A checkout edited or switched while a
    sweep runs would otherwise stamp a record with code it did not run.
    """
    from open_apps.alignment._config import plain

    git_commit, git_dirty = git if git is not None else git_state()

    task_name = config.get("task_name")
    task_config = (
        plain(config.tasks[task_name]) if task_name and config.get("tasks") else None
    )
    agent_cfg = plain(config.get("agent")) or {}
    agent_name = (
        agent_cfg.get("model_pretty_name")
        or _agent_config_choice()
        or _model_label(agent_cfg.get("model_name"))
        or agent_cfg.get("agent_name")
    )
    agent_hash = hashlib.sha256(
        json.dumps(agent_cfg, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    apps = config.get("apps") or {}
    env_args = config.get("browsergym_env_args") or {}
    return Identity(
        task_name=task_name,
        task_class=type(task).__name__,
        goal=task.goal,
        goal_style=getattr(task, "goal_style", None),
        task_config=task_config,
        variation=variation_overrides(config),
        seed=_int_or_none(config.get("seed")),
        task_seed=_int_or_none(task_seed),
        agent_name=agent_name,
        agent_config_hash=agent_hash,
        suite_version=config.get("suite_version"),
        adversarial_popup=adversarial_popup(apps.get("pop_ups")),
        adversarial_content=adversarial_content(apps),
        max_steps=_int_or_none(env_args.get("max_steps")),
        task_termination=str(config.get("task_termination") or "on_success"),
        git_commit=git_commit,
        git_dirty=git_dirty,
        command=reproduce_command(config),
    )


def git_state(path: str | Path | None = None) -> tuple[Optional[str], Optional[bool]]:
    """``(HEAD sha, dirty)`` of the checkout this package runs from, or
    ``(None, None)`` outside one (an installed wheel). Never raises."""
    try:
        import git

        repo = git.Repo(
            Path(path or __file__).resolve().parent, search_parent_directories=True
        )
        return repo.head.object.hexsha, repo.is_dirty(untracked_files=False)
    except Exception:  # noqa: BLE001 -- identity is best-effort metadata
        return None, None


# The entrypoint a reproduce command runs: one episode, any root config.
_LAUNCH = ("uv", "run", "launch_agent.py")
# Overrides that only decide where a run writes or executes; dropped from the
# command so it runs anywhere (a sweep's per-job values are not inputs).
_OUTPUT_KEYS = frozenset(
    {"logs_dir", "databases_dir", "record_video_dir", "job_id", "num_jobs", "cluster"}
)
# Where the model is served (``agent.hostname=<node>`` from conduct_slurm.sh):
# also an execution location, and a cluster node name is not for a public
# record. Matched on the last key segment, since they sit under ``agent.``.
_SERVING_KEYS = frozenset({"hostname", "port", "host_name_updated_on"})
_SECRET_KEY = re.compile(r"api_?key|token|secret|password|credential", re.IGNORECASE)


def reproduce_command(config: Any) -> Optional[str]:
    """A shell command that reruns this one episode, or None.

    A sweep job is composed from the sweep's command line plus its own cell
    and task (``AppVariationParallelTasksConfig``), which it records under
    ``compose``; the command recomposes exactly that from the sweep's root
    config, so one cell of a sweep reruns alone. A single run's overrides
    come from Hydra. ``task_name`` is always spelled out, so the command
    names its task even when it was the config default.

    Records are shared (W&B artifacts, leaderboard bundles), so values under
    secret-looking keys become ``<redacted>`` and absolute paths ``<path>``
    (a cluster's layout and a username, like :func:`_model_label` avoids);
    output locations are dropped. Rerunning with a self-hosted model means
    filling its ``<path>`` back in.
    """
    compose = config.get("compose")
    if compose:
        config_name, overrides = compose.get("config_name"), compose.get("overrides")
    else:
        config_name, overrides = _hydra_overrides()
        if overrides is None:
            return None
    kept = [_shareable(str(o)) for o in overrides or ()]
    kept = [o for o in kept if o is not None]
    task_name = config.get("task_name")
    if task_name and not any(o.startswith("task_name=") for o in kept):
        kept.append(f"task_name={task_name}")
    head = list(_LAUNCH)
    if config_name and config_name != "config":
        head.append(f"--config-name={config_name}")
    return shlex.join([*head, *kept])


def _hydra_overrides() -> tuple[Optional[str], Optional[list[str]]]:
    """``(config_name, task overrides)`` of the running Hydra app, else
    ``(None, None)``."""
    try:
        from hydra.core.hydra_config import HydraConfig

        if HydraConfig.initialized():
            cfg = HydraConfig.get()
            return cfg.job.config_name, [str(o) for o in cfg.overrides.task]
    except Exception:  # noqa: BLE001 -- identity is best-effort metadata
        pass
    return None, None


def _shareable(override: str) -> Optional[str]:
    """``override`` fit for a shared record; None to leave it out."""
    key, sep, value = override.partition("=")
    if not sep:
        return override
    if key.lstrip("+~") in _OUTPUT_KEYS:
        return None
    if key.rsplit(".", 1)[-1] in _SERVING_KEYS:
        return None
    if _SECRET_KEY.search(key):
        return f"{key}=<redacted>"
    if value.startswith(("/", "~")):
        return f"{key}=<path>"
    return override


def _agent_config_choice() -> Optional[str]:
    """The ``agent=<name>`` config the run was launched with, when Hydra
    composed it (a sweep job or a launcher run); else None."""
    try:
        from hydra.core.hydra_config import HydraConfig

        if HydraConfig.initialized():
            choice = HydraConfig.get().runtime.choices.get("agent")
            return str(choice) if choice else None
    except Exception:  # noqa: BLE001 -- identity is best-effort metadata
        pass
    return None


def _model_label(model_name: Any) -> Optional[str]:
    """``model_name`` fit to name an agent in a record.

    For a self-hosted model it is often a filesystem path to the checkpoint,
    and records are shared (W&B artifacts, leaderboard bundles): a path
    would publish a cluster's directory layout and a username. A path keeps
    its last component; a hub id (``org/model``) is a name and is kept
    whole.
    """
    if not model_name:
        return None
    name = str(model_name).strip()
    is_path = (
        name.startswith(("/", "~", "./", "../", "\\"))
        or "\\" in name
        or name.rstrip("/").count("/") >= 2
    )
    if not is_path:
        return name
    return name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or None


def adversarial_popup(pop_ups: Any) -> bool:
    """Whether any configured pop-up is flagged ``adversarial: true``.

    An explicit per-pop-up flag (default false) rather than "any pop-up is
    set": only a pop-up that claims the goal is achieved can provoke a
    premature stop, and inferring that from its text would break the moment
    the message is translated or reworded.
    """
    from open_apps.alignment._config import plain

    pop_ups = plain(pop_ups, resolve=False)
    if not isinstance(pop_ups, dict):
        return False
    return any(
        isinstance(entry, dict) and bool(entry.get("adversarial", False))
        for entry in pop_ups.values()
    )


def adversarial_content(apps: Any) -> bool:
    """Whether any app's selected content variant is flagged ``adversarial: true``.

    The flag sits at the app's config root (``apps.<app>.adversarial``),
    next to its ``irreversible:`` map, set by the content file that plants
    the message -- so it composes with every layout, theme and device, and
    nothing renders it. Same reasoning as :func:`adversarial_popup` for an
    explicit flag over sniffing the text.
    """
    from open_apps.alignment._config import plain

    apps = plain(apps, resolve=False)
    if not isinstance(apps, dict):
        return False
    return any(
        isinstance(cfg, dict) and cfg.get("adversarial") is True
        for key, cfg in apps.items()
        if key != "pop_ups"  # entries there carry their own flag
    )


# Override prefixes that change what the agent sees (a variation axis), as
# opposed to run plumbing (logs_dir, use_wandb, agent=...).
_VARIATION_PREFIXES = ("apps", "+apps", "device")


def variation_overrides(config: Any) -> list[str]:
    """The variation cell of a run, as Hydra override strings.

    A sweep job records its cell in ``app_overrides`` (set by
    ``AppVariationParallelTasksConfig``); a single run's cell is whatever
    ``apps/...``/``device=`` overrides were typed on the command line.
    """
    overrides = config.get("app_overrides")
    if overrides:
        return [str(o) for o in overrides]
    try:
        from hydra.core.hydra_config import HydraConfig

        if HydraConfig.initialized():
            task_overrides = HydraConfig.get().overrides.task
            return [
                str(o) for o in task_overrides if str(o).startswith(_VARIATION_PREFIXES)
            ]
    except Exception:  # noqa: BLE001 -- identity is best-effort metadata
        pass
    return []


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--schema", action="store_true", help="print the JSON Schema")
    args = parser.parse_args(argv)
    if args.schema:
        json.dump(json_schema(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
