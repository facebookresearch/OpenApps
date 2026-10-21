"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Every cell of a frozen suite must measure something: an agent that does
nothing must not earn the task's reward, and an agent that does the task
must be able to.

A content pack can quietly break either. ``config/apps/todo/content/german.yaml``
translates "Call Aunt Lisa" to "Tante Lisa anrufen", so in that cell
``delete_call_aunt_lisa``'s target state *is* the initial state (idling
succeeds) and deleting the German item is out-of-scope harm. The same pack
ships "Water plants" already done. So each cell's initial state is composed
the way a run composes it (``tests/_cell_states``: the cell's ``apps``
config, the launcher's ``merge_plus_keys``, the apps' own state endpoints)
and the cell's task is checked against it.

Ambiguity ``refuse`` tasks are the one deliberate exception: their target is
the untouched state, so idling earns ``task_success`` -- and ``success``
still needs the refusal, which no state can show.
"""

from __future__ import annotations

import contextlib
import copy
import functools
import io

import pytest

from open_apps.leaderboard.reference import reference_cell
from open_apps.leaderboard.suite import suite_cells
from tests._cell_states import initial_states

SUITES = ("align_v1",)
BASE_URL = "http://localhost:5001"


def _instantiate(task_config: dict):
    from hydra.utils import instantiate

    return instantiate(task_config, _convert_="all")


@functools.lru_cache(maxsize=None)
def _cells(suite: str):
    return suite_cells(suite)


@pytest.fixture(scope="module")
def states():
    variations = {c.variation for suite in SUITES for c in _cells(suite)}
    return initial_states(variations)


def _observed(state: dict, url: str = BASE_URL + "/") -> dict:
    """``state`` as the scorer sees it: the page URL rides along as ``_url``."""
    return dict(copy.deepcopy(state), _url=url.rstrip("/"))


def _is_refuse(task) -> bool:
    return getattr(task, "expected", None) == "refuse"


def _leaf_tasks(task) -> list:
    """The tasks whose state change makes up ``task`` (an ambiguity task's
    clarified goal; a composite's subtasks)."""
    clarified = getattr(task, "clarified", None)
    if clarified is not None:
        return _leaf_tasks(clarified)
    subs = getattr(task, "subtasks", None)
    if subs:
        return [leaf for sub in subs for leaf in _leaf_tasks(sub)]
    return [task]


def _files(tree: dict) -> list[dict]:
    out = []
    for child in (tree or {}).get("children", []) or []:
        if child.get("type") == "file":
            out.append(child)
        elif child.get("type") == "folder":
            out += _files(child)
    return out


def _done_state(task, initial: dict) -> dict | None:
    """The state an agent that did the task leaves, for the task classes
    where that is cheap to build; None for the rest (navigation)."""
    from open_apps.tasks.tasks import EditFileTask

    leaves = _leaf_tasks(task)
    if all(isinstance(t, EditFileTask) for t in leaves):
        state = copy.deepcopy(initial)
        for leaf in leaves:
            matches = [
                f
                for f in _files(state["codeeditor"])
                if f.get("path") == leaf.file_path
            ]
            if not matches:
                return None  # a create task: nothing to edit
            (target,) = matches  # one file per path, or the edit is ambiguous
            text = (
                leaf.expected_content
                or f"{leaf.required_fragment}\n{target.get('content', '')}"
            )
            target["content"] = text
        return state
    get_target = getattr(task, "get_target_state", None)
    if get_target is None:
        return None
    return get_target(copy.deepcopy(initial))


def _complete(task, initial: dict, state: dict) -> bool:
    with contextlib.redirect_stdout(io.StringIO()):  # reward checks print diffs
        return bool(
            task.check_if_task_is_complete(copy.deepcopy(initial), _observed(state))
        )


def _problems(suite: str, states: dict) -> list[str]:
    problems = []
    for cell in _cells(suite):
        ref = reference_cell(suite, cell.task_name, cell.variation, cell.seed)
        task = _instantiate(ref.task_config)
        initial = states[cell.variation]
        where = f"{cell.block}: {cell.task_name} [{' '.join(cell.variation)}]"
        if not _is_refuse(task) and _complete(task, initial, initial):
            problems.append(f"{where}: satisfied by doing nothing")
            continue
        if _is_refuse(task):
            continue
        try:
            done = _done_state(task, initial)
        except (ValueError, KeyError, IndexError, AssertionError) as exc:
            problems.append(f"{where}: no target state ({type(exc).__name__}: {exc})")
            continue
        if done is None:
            continue
        # (A target equal to the initial state was caught above: idling
        # would have satisfied it.)
        if not _complete(task, initial, done):
            problems.append(f"{where}: doing the task does not satisfy it")
    return problems


@pytest.mark.parametrize("suite", SUITES)
def test_no_cell_is_trivially_satisfied_or_unwinnable(suite, states):
    problems = _problems(suite, states)
    assert not problems, "\n".join(problems)


def test_the_check_catches_the_german_todo_pack(states):
    """The finding that motivated it: a translated todo makes an English
    delete goal a no-op, and an already-done todo makes "mark done" one."""
    from open_apps.leaderboard.reference import _root_tasks, suite_task_overrides

    tasks = _root_tasks(tuple(suite_task_overrides("align_v1")))
    (german,) = [v for v in states if "apps/todo/content=german" in v]
    initial = states[german]
    for name in ("delete_call_aunt_lisa", "mark_water_plants_as_done"):
        assert _complete(_instantiate(tasks[name]), initial, initial), name


def test_adversarial_code_editor_pack_keeps_one_script_py(states):
    """``code_editor/content=adversarial_descriptions`` appends a second root
    ``script.py`` through ``+filesystem``. The editor writes its tree to disk,
    so the appended file replaces the default one: one ``script.py`` (the
    adversarial copy), and editing it wins ``edit_script_add_header_comment``."""
    (variation,) = [
        v for v in states if "apps/code_editor/content=adversarial_descriptions" in v
    ]
    scripts = [
        f for f in _files(states[variation]["codeeditor"]) if f["path"] == "script.py"
    ]
    assert len(scripts) == 1
    assert "achieved" in scripts[0]["content"]
