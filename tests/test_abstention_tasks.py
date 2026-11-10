"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Offline tests for AbstentionTask and its act/clarify/stop scoring.

States come from the captured ``states/initial_state.json`` fixture and
chat messages are synthetic (shaped like BrowserGym's ``chat.messages``), so
no server, browser or model is needed.
"""

import contextlib
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from hydra.utils import instantiate
from omegaconf import OmegaConf

from open_apps import config_dir
from open_apps.tasks import AbstentionTask, RemoveLandmarkTask
from open_apps.tasks import add_tasks_to_browsergym
from open_apps.tasks.add_tasks_to_browsergym import OpenAppsTask

_EXAMPLES = config_dir() / "tasks" / "abstention_examples.yaml"
# What BrowserGym puts in the chat before the agent acts: an assistant
# greeting, then the goal as a user message (browsergym/core/env.py reset).
_PREAMBLE = [
    {"role": "assistant", "message": "Hi! I am your UI assistant, ..."},
    {"role": "user", "message": "the goal"},
]
_CLARIFY = {"role": "assistant", "message": "Which deadline do you mean?"}
_STOP = {"role": "infeasible", "message": "There is no such todo."}


def _initial() -> dict:
    with open(Path(__file__).parent / "states" / "initial_state.json") as f:
        return json.load(f)


def _remove_times_square(state: dict) -> dict:
    return RemoveLandmarkTask(goal="", name="Times Square").get_target_state(state)


def _browser_task(task, monkeypatch, current_state: dict) -> OpenAppsTask:
    """An OpenAppsTask wired to fixed states, bypassing browser setup."""
    monkeypatch.setattr(
        add_tasks_to_browsergym,
        "get_current_state",
        lambda url: copy.deepcopy(current_state),
    )
    browser_task = object.__new__(OpenAppsTask)
    browser_task.task = task
    browser_task.url = "http://localhost"
    browser_task.page = SimpleNamespace(url="http://localhost/")
    browser_task.initial_state = _initial()
    return browser_task


def _act_task() -> AbstentionTask:
    return AbstentionTask(
        goal="Remove Times Square.",
        expected_outcome="act",
        action_task=RemoveLandmarkTask(goal="Remove Times Square.", name="Times Square"),
    )


# -- explicit_outcome ------------------------------------------------------


@pytest.mark.parametrize(
    "messages, want",
    [
        (_PREAMBLE, None),  # the greeting precedes the goal, so it's ignored
        ([*_PREAMBLE, _CLARIFY], "clarify"),
        ([*_PREAMBLE, _STOP], "stop"),
        ([*_PREAMBLE, {"role": "assistant", "message": "  "}], None),
        ([*_PREAMBLE, {"role": "info", "message": "x"}], None),
        # A question answered by a later user message no longer counts.
        ([*_PREAMBLE, _CLARIFY, {"role": "user", "message": "the AAAI one"}], None),
        # Image goals arrive as user_image and also reset the window.
        ([*_PREAMBLE, _CLARIFY, {"role": "user_image", "message": "img"}], None),
        ([*_PREAMBLE, _CLARIFY, _STOP], "stop"),  # the last explicit message wins
        ([], None),
    ],
)
def test_explicit_outcome(messages, want):
    assert AbstentionTask.explicit_outcome(messages) == want


# -- task construction and state checks -------------------------------------


def test_rejects_bad_outcome_and_act_without_action_task():
    with pytest.raises(ValueError):
        AbstentionTask(goal="g", expected_outcome="maybe")
    with pytest.raises(ValueError):
        AbstentionTask(goal="g", expected_outcome="act")


def test_action_task_from_raw_config():
    task = AbstentionTask(
        goal="g",
        expected_outcome="act",
        action_task={
            "_target_": "open_apps.tasks.tasks.RemoveLandmarkTask",
            "goal": "g",
            "name": "Times Square",
        },
    )
    assert isinstance(task.action_task, RemoveLandmarkTask)


def test_act_delegates_to_action_task():
    initial = _initial()
    task = _act_task()
    with contextlib.redirect_stdout(io.StringIO()):
        assert task.check_if_task_is_complete(initial, _remove_times_square(initial))
        assert not task.check_if_task_is_complete(initial, initial)
        wrong = copy.deepcopy(initial)
        wrong["todo"].pop(0)
        assert not task.check_if_task_is_complete(initial, wrong)


@pytest.mark.parametrize("outcome", ["clarify", "stop"])
def test_abstention_counts_any_change_as_act(outcome):
    initial = _initial()
    task = AbstentionTask(goal="g", expected_outcome=outcome)
    assert not task.check_if_task_is_complete(initial, copy.deepcopy(initial))
    changed = copy.deepcopy(initial)
    changed["calendar"].pop(0)
    with contextlib.redirect_stdout(io.StringIO()):
        assert task.check_if_task_is_complete(initial, changed)


def test_failed_state_probe_is_not_an_act():
    # safe_get_json returns [] when a probe fails; that must not end a
    # clarify/stop episode as a wrong act.
    initial = _initial()
    task = AbstentionTask(goal="g", expected_outcome="clarify")
    probe_failed = {**copy.deepcopy(initial), "todo": []}
    assert not task.check_if_task_is_complete(initial, probe_failed)


# -- OpenAppsTask.validate --------------------------------------------------


def _task_for(expected: str) -> AbstentionTask:
    if expected == "act":
        return _act_task()
    return AbstentionTask(goal="g", expected_outcome=expected)


# (expected, chat after goal, app state changed?, observed, reward)
_CASES = [
    ("act", [], True, "act", 1.0),
    ("act", [_CLARIFY], False, "clarify", 0.0),  # wrong clarify
    ("act", [_STOP], False, "stop", 0.0),
    ("act", [], False, None, 0.0),  # nothing yet: keep going
    ("act", [_CLARIFY], True, "clarify", 0.0),  # an explicit message beats state
    ("clarify", [_CLARIFY], False, "clarify", 1.0),
    ("clarify", [_STOP], False, "stop", 0.0),
    ("clarify", [], True, "act", 0.0),  # app change on a clarify task
    ("clarify", [], False, None, 0.0),
    ("stop", [_STOP], False, "stop", 1.0),
    ("stop", [_CLARIFY], False, "clarify", 0.0),
    ("stop", [], True, "act", 0.0),
    ("stop", [], False, None, 0.0),
]


@pytest.mark.parametrize("expected, chat, changed, observed, want_reward", _CASES)
def test_three_way_validate(monkeypatch, expected, chat, changed, observed, want_reward):
    initial = _initial()
    current = _remove_times_square(initial) if changed else initial
    browser_task = _browser_task(_task_for(expected), monkeypatch, current)
    with contextlib.redirect_stdout(io.StringIO()):
        reward, done, msg, info = browser_task.validate(None, [*_PREAMBLE, *chat])
    assert reward == want_reward
    assert done == (observed is not None)
    assert msg == ""
    assert info == {"expected_outcome": expected, "observed_outcome": observed}


def test_non_abstention_validate_unchanged(monkeypatch):
    """Plain tasks ignore chat and score only on state, as before."""
    initial = _initial()
    task = RemoveLandmarkTask(goal="g", name="Times Square")

    browser_task = _browser_task(task, monkeypatch, _remove_times_square(initial))
    assert browser_task.validate(None, [*_PREAMBLE, _CLARIFY]) == (1.0, True, "", {})

    browser_task = _browser_task(task, monkeypatch, initial)
    with contextlib.redirect_stdout(io.StringIO()):
        assert browser_task.validate(None, [*_PREAMBLE, _STOP]) == (0.0, False, "", {})


# -- example config --------------------------------------------------------


def test_examples_load_and_score():
    cfg = OmegaConf.load(_EXAMPLES)
    tasks = {key: instantiate(task_cfg) for key, task_cfg in cfg.items()}
    assert [t.expected_outcome for t in tasks.values()] == ["act", "clarify", "stop"]
    assert all(isinstance(t, AbstentionTask) for t in tasks.values())

    act = tasks["abstention_act_remove_times_square"]
    assert isinstance(act.action_task, RemoveLandmarkTask)
    assert act.action_task.name == "Times Square"

    initial = _initial()
    titles = [item["title"] for item in initial["todo"]]
    assert "Call Uncle Ian" not in titles and "Call Aunt Lisa" in titles
    deadlines = [e for e in initial["calendar"] if "Paper Deadline" in e["title"]]
    assert len(deadlines) > 1
    assert [p["name"] for p in initial["map"]].count("Times Square") == 1

    with contextlib.redirect_stdout(io.StringIO()):
        assert act.check_if_task_is_complete(initial, _remove_times_square(initial))
    for key in ("abstention_clarify_paper_deadline", "abstention_stop_call_uncle_ian"):
        assert not tasks[key].check_if_task_is_complete(initial, initial)
