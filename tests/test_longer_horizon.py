"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for CompositeTask and the longer-horizon task set.

These run fully offline against the captured ``states/initial_state.json``
fixture (which mirrors the default app seed) — no live server needed. The
`compare()` implementation prints a diff on any mismatch, so stdout is
redirected while running the negative checks to keep test output readable.
"""

import contextlib
import copy
import io
import json
import re
from pathlib import Path

import pytest
from hydra.utils import instantiate
from omegaconf import OmegaConf

from open_apps import config_dir
from open_apps.tasks.tasks import (
    AddEventTask,
    AddToDoTask,
    CompositeTask,
    MarkToDoDoneTask,
    SendMessageTask,
)

_STATES_DIR = Path(__file__).parent / "states"
_LH_PATH = config_dir() / "tasks" / "longer_horizon.yaml"

# App each task class acts on (mirrors the generator's mapping).
_CLASS_TO_APP = {
    "AddEventTask": "calendar",
    "RemoveEventTask": "calendar",
    "AddToDoTask": "todo",
    "MarkToDoDoneTask": "todo",
    "DeleteToDoTask": "todo",
    "SendMessageTask": "messages",
    "RemoveLandmarkTask": "map",
}


def _norm(s: str) -> str:
    """Match StringSimilarityOperator: lowercase, strip punctuation, collapse ws."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(s).lower())).strip()


def _load_initial() -> dict:
    with open(_STATES_DIR / "initial_state.json", encoding="utf-8") as f:
        return json.load(f)


def _lh_cfg():
    return OmegaConf.load(_LH_PATH)


_LH_KEYS = list(_lh_cfg().keys())


def _check(task, initial, current) -> bool:
    """Run a completion check with the diff-printing suppressed."""
    with contextlib.redirect_stdout(io.StringIO()):
        return task.check_if_task_is_complete(initial, current)


# ---------------------------------------------------------------------------
# CompositeTask unit behaviour (no config file needed)
# ---------------------------------------------------------------------------
class TestCompositeTask:
    def _sample(self) -> CompositeTask:
        return CompositeTask(
            goal="add event, todo, and message",
            subtasks=[
                AddEventTask(
                    goal="e", title="Elena's Birthday", date="2026-11-20",
                    description=None, location=None, url=None, invitees=[],
                ),
                AddToDoTask(goal="t", todo_name="Elena's Birthday", is_done=False),
                SendMessageTask(goal="m", to="Charlie", message="Don't forget!"),
            ],
        )

    def test_target_satisfies_check(self):
        task = self._sample()
        initial = _load_initial()
        target = task.get_target_state(initial)
        assert _check(task, initial, target)

    def test_initial_unchanged_fails(self):
        task = self._sample()
        initial = _load_initial()
        assert not _check(task, initial, initial)

    def test_initial_state_not_mutated(self):
        task = self._sample()
        initial = _load_initial()
        task.check_if_task_is_complete(initial, task.get_target_state(initial))
        assert initial == _load_initial()

    def test_all_subgoals_required(self):
        """Completing only some sub-goals must not count as done."""
        task = self._sample()
        initial = _load_initial()
        # Apply only the first two sub-tasks (skip the message).
        partial = task.subtasks[0].get_target_state(initial)
        partial = task.subtasks[1].get_target_state(partial)
        assert not _check(task, initial, partial)

    def test_reply_ignored_by_default(self):
        """The app's auto-reply (random for Charlie) is ignored when checking."""
        task = self._sample()
        initial = _load_initial()
        observed = task.get_target_state(initial)
        for contact in observed["messenger"]:
            if contact["user"] == "Charlie":
                contact["messages"].append(["I'm a bot!", "Charlie", "Jan 01, 12:00PM"])
        assert _check(task, initial, observed)

    def test_spurious_message_fails(self):
        """Sending an extra unrequested message must not still pass (F2)."""
        task = self._sample()
        initial = _load_initial()
        observed = task.get_target_state(initial)
        for contact in observed["messenger"]:
            if contact["user"] == "Charlie":
                # the legitimate auto-reply ...
                contact["messages"].append(["a reply", "Charlie", "x"])
                # ... plus a spurious extra outgoing message + its reply
                contact["messages"].append(["spurious extra message", "you", "x"])
                contact["messages"].append(["another reply", "Charlie", "x"])
        assert not _check(task, initial, observed)

    def test_spurious_message_to_untargeted_contact_fails(self):
        """A message to a contact the task never mentions must fail (F2)."""
        task = self._sample()
        initial = _load_initial()
        observed = task.get_target_state(initial)
        for contact in observed["messenger"]:
            if contact["user"] == "Charlie":
                contact["messages"].append(["a reply", "Charlie", "x"])
            if contact["user"] == "Bob":  # never targeted by this task
                contact["messages"].append(["unsolicited", "you", "x"])
                contact["messages"].append(["bob reply", "Bob", "x"])
        assert not _check(task, initial, observed)

    def test_wrong_message_content_fails(self):
        task = self._sample()
        initial = _load_initial()
        wrong = task.subtasks[0].get_target_state(initial)
        wrong = task.subtasks[1].get_target_state(wrong)
        wrong = SendMessageTask(
            goal="m", to="Charlie", message="a completely different message"
        ).get_target_state(wrong)
        assert not _check(task, initial, wrong)

    def test_missing_seed_item_returns_false(self):
        """A sub-task referencing a non-existent item cannot complete."""
        task = CompositeTask(
            goal="mark a todo that does not exist",
            subtasks=[
                AddToDoTask(goal="t", todo_name="Something new", is_done=False),
                MarkToDoDoneTask(goal="m", todo_name="This todo does not exist"),
            ],
        )
        initial = _load_initial()
        # get_target_state raises ValueError -> check swallows it -> False.
        assert not _check(task, initial, initial)

    def test_subtasks_instantiated_from_config(self):
        """Hydra (with _convert_: all) yields real Task sub-tasks, not configs."""
        cfg = OmegaConf.create(
            {
                "_target_": "open_apps.tasks.tasks.CompositeTask",
                "_convert_": "all",
                "goal": "g",
                "subtasks": [
                    {
                        "_target_": "open_apps.tasks.tasks.AddToDoTask",
                        "goal": "t", "todo_name": "X", "is_done": False,
                    }
                ],
            }
        )
        task = instantiate(cfg)
        assert isinstance(task, CompositeTask)
        assert all(hasattr(s, "get_target_state") for s in task.subtasks)


# ---------------------------------------------------------------------------
# The 100 longer-horizon tasks
# ---------------------------------------------------------------------------
class TestLongerHorizonSet:
    def test_file_has_100_tasks(self):
        assert len(_LH_KEYS) == 100

    def test_keys_unique(self):
        assert len(_LH_KEYS) == len(set(_LH_KEYS))

    @pytest.mark.parametrize("key", _LH_KEYS)
    def test_instantiates_to_composite(self, key):
        task = instantiate(_lh_cfg()[key])
        assert isinstance(task, CompositeTask)
        assert 2 <= len(task.subtasks) <= 4
        assert all(hasattr(s, "get_target_state") for s in task.subtasks)

    @pytest.mark.parametrize("key", _LH_KEYS)
    def test_spans_two_to_four_apps(self, key):
        cfg = _lh_cfg()[key]
        apps = {
            _CLASS_TO_APP[s["_target_"].rsplit(".", 1)[-1]] for s in cfg["subtasks"]
        }
        assert 2 <= len(apps) <= 4, f"{key} touches apps {apps}"

    @pytest.mark.parametrize("key", _LH_KEYS)
    def test_self_consistent(self, key):
        """Its own target state satisfies the check; the untouched initial does not.

        The negative half also guards against typos in the seed items that
        Remove/Mark/Delete sub-tasks reference: a bad reference is a no-op, so
        the target would equal the initial and this assertion would fail.
        """
        task = instantiate(_lh_cfg()[key])
        initial = _load_initial()
        target = task.get_target_state(initial)
        assert _check(task, initial, target), f"{key}: target fails its own check"
        assert not _check(task, initial, initial), f"{key}: passes with nothing done"

    @pytest.mark.parametrize("key", _LH_KEYS)
    def test_message_contacts_are_seeded(self, key):
        """Every messaged contact exists in the seed (else get_target_state raises)."""
        seeded = {c["user"] for c in _load_initial()["messenger"]}
        task = instantiate(_lh_cfg()[key])
        for sub in task.subtasks:
            if isinstance(sub, SendMessageTask):
                assert sub.to in seeded, f"{key} messages unknown contact {sub.to!r}"

    @pytest.mark.parametrize("key", _LH_KEYS)
    def test_goal_specifies_agent_produced_values(self, key):
        """The goal must state every value the agent has to PRODUCE.

        Titles/todo-names the agent creates and message text it must type are
        quoted verbatim; otherwise an agent reading only the top-level goal
        cannot produce the exact string the reward check requires. Items the
        agent SELECTS from existing seed data (events/todos to remove, marks,
        landmarks) only need to be named.
        """
        cfg = _lh_cfg()[key]
        goal = cfg["goal"]
        gnorm = _norm(goal)
        for sub in cfg["subtasks"]:
            cls = sub["_target_"].rsplit(".", 1)[-1]
            if cls == "AddEventTask":
                assert f"'{sub['title']}'" in goal, f"{key}: event title not quoted"
            elif cls == "AddToDoTask":
                assert f"'{sub['todo_name']}'" in goal, f"{key}: todo not quoted"
            elif cls == "SendMessageTask":
                assert _norm(sub["message"]) in gnorm, f"{key}: message not in goal"
                assert sub["to"] in goal, f"{key}: contact not in goal"
            elif cls in ("RemoveEventTask",):
                assert _norm(sub["title"]) in gnorm, f"{key}: removed event not named"
            elif cls in ("MarkToDoDoneTask", "DeleteToDoTask"):
                assert _norm(sub["todo_name"]) in gnorm, f"{key}: todo not named"
            elif cls == "RemoveLandmarkTask":
                assert _norm(sub["name"]) in gnorm, f"{key}: landmark not named"

    @pytest.mark.parametrize("key", _LH_KEYS)
    def test_mark_done_targets_not_already_done(self, key):
        """A 'mark as done' sub-goal must target a todo that isn't already done."""
        seed_done = {
            t["title"] for t in _load_initial()["todo"] if t["done"] in (1, True)
        }
        for sub in instantiate(_lh_cfg()[key]).subtasks:
            if isinstance(sub, MarkToDoDoneTask):
                assert sub.todo_name not in seed_done, (
                    f"{key}: marks already-done todo {sub.todo_name!r} (no-op)"
                )
