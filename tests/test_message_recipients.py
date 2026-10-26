"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Message tasks name people ("to: Bob"); messenger state only knows handles.

``SendMessageTask`` bridges the two through the contacts slice of the initial
state, once, when it builds its target. These tests pin the resolution rules
(handle first, then an exact display name, then a unique first name), the
errors for names that can't be pinned to one thread, and that reply tolerance
follows the resolved handle rather than the name.
"""

import contextlib
import io
import json
from pathlib import Path

import pytest
from hydra.utils import instantiate
from omegaconf import OmegaConf

from open_apps import config_dir
from open_apps.tasks.tasks import CompositeTask, SendMessageTask, resolve_thread

_STATES_DIR = Path(__file__).parent / "states"


def _person(id, first="", last="", company="", phones=(), emails=()):
    return {
        "id": id,
        "first_name": first,
        "last_name": last,
        "company": company,
        "note": "",
        "favorite": False,
        "phones": [{"label": "mobile", "value": v} for v in phones],
        "emails": [{"label": "home", "value": v} for v in emails],
    }


def _state(*people, threads=()):
    return {
        "todo": [],
        "calendar": [],
        "map": [],
        "messenger": [
            {"handle": h, "messages": [["hi", h, "Jan 01, 10:00AM"]]} for h in threads
        ],
        "contacts": {"contacts": list(people)},
    }


ALICE = _person(1, "Alice", phones=["+15550100001"], emails=["alice@example.com"])
BOB = _person(2, "Bob", phones=["+15550100002"])
BOB_SMITH = _person(3, "Bob", "Smith", phones=["+15550100099"])
PRIYA = _person(4, "Priya", "Shah", phones=["+15550100004"])
LANDLORD = _person(5, company="Northgate Properties LLC", emails=["rent@northgateprop.example"])
STATE = _state(
    ALICE, BOB, BOB_SMITH, PRIYA, LANDLORD,
    threads=["+15550100001", "+15550100002", "+15550100099", "group:fantastic4",
             "+15550142290", "rent@northgateprop.example"],
)


def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


class TestResolveThread:
    @pytest.mark.parametrize(
        "to,expected",
        [
            ("+15550100001", "+15550100001"),
            ("(555) 010-0001", "+15550100001"),  # any spelling of a handle
            ("group:fantastic4", "group:fantastic4"),
            ("+15550142290", "+15550142290"),  # a thread with no contact
            ("Alice", "+15550100001"),
            ("alice", "+15550100001"),
            ("Bob", "+15550100002"),  # exact display name beats "Bob Smith"
            ("Bob Smith", "+15550100099"),
            ("Northgate Properties LLC", "rent@northgateprop.example"),
        ],
    )
    def test_resolves(self, to, expected):
        assert resolve_thread(to, STATE) == expected

    def test_unique_first_name_falls_back(self):
        state = _state(BOB_SMITH, threads=["+15550100099"])
        assert resolve_thread("Bob", state) == "+15550100099"

    def test_ambiguous_first_name_names_the_candidates(self):
        other = _person(6, "Bob", "Jones", phones=["+15550100098"])
        state = _state(BOB_SMITH, other, threads=["+15550100099", "+15550100098"])
        with pytest.raises(ValueError, match="Bob Smith.*Bob Jones"):
            resolve_thread("Bob", state)

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="Nobody"):
            resolve_thread("Nobody", STATE)

    def test_contact_without_a_thread_raises(self):
        with pytest.raises(ValueError, match="Priya Shah.*no messenger thread"):
            resolve_thread("Priya", STATE)

    def test_first_handle_with_a_thread_wins(self):
        """Alice is reachable by phone and email; only the email has a thread."""
        state = _state(ALICE, threads=["alice@example.com"])
        assert resolve_thread("Alice", state) == "alice@example.com"

    def test_state_without_contacts_still_resolves_handles(self):
        state = {"messenger": [{"handle": "+15550100001", "messages": []}]}
        assert resolve_thread("+15550100001", state) == "+15550100001"
        with pytest.raises(ValueError):
            resolve_thread("Alice", state)


class TestSendMessageTarget:
    def test_appends_a_sent_message_to_the_resolved_thread(self):
        task = SendMessageTask(goal="g", to="Bob", message="see you at 6")
        target = task.get_target_state(STATE)
        bob = next(t for t in target["messenger"] if t["handle"] == "+15550100002")
        text, sender, _ = bob["messages"][-1]
        assert (text, sender) == ("see you at 6", "you")
        # nobody else's thread changed, and the input wasn't mutated
        decoy = next(t for t in target["messenger"] if t["handle"] == "+15550100099")
        assert len(decoy["messages"]) == 1
        assert len(STATE["messenger"][1]["messages"]) == 1

    def _observed(self, handle, *extra):
        observed = SendMessageTask(goal="g", to=handle, message="see you at 6").get_target_state(STATE)
        thread = next(t for t in observed["messenger"] if t["handle"] == handle)
        thread["messages"].extend(extra)
        return observed

    def test_auto_reply_is_tolerated_on_the_resolved_handle(self):
        task = SendMessageTask(goal="g", to="Bob", message="see you at 6")
        observed = self._observed("+15550100002", ["ok!", "+15550100002", "x"])
        assert _quiet(task.check_if_task_is_complete, STATE, observed)

    def test_message_to_the_decoy_fails(self):
        task = SendMessageTask(goal="g", to="Bob", message="see you at 6")
        observed = self._observed("+15550100099", ["ok!", "+15550100099", "x"])
        assert not _quiet(task.check_if_task_is_complete, STATE, observed)

    def test_extra_message_fails(self):
        task = SendMessageTask(goal="g", to="Bob", message="see you at 6")
        observed = self._observed(
            "+15550100002",
            ["ok!", "+15550100002", "x"],
            ["and another", "you", "x"],
            ["ok!", "+15550100002", "x"],
        )
        assert not _quiet(task.check_if_task_is_complete, STATE, observed)

    def test_composite_tolerates_one_reply_per_send_by_handle(self):
        task = CompositeTask(
            goal="g",
            subtasks=[
                SendMessageTask(goal="g", to="Bob", message="one"),
                SendMessageTask(goal="g", to="+1 555 010 0002", message="two"),
            ],
        )
        observed = task.get_target_state(STATE)
        bob = next(t for t in observed["messenger"] if t["handle"] == "+15550100002")
        bob["messages"] += [["r1", "+15550100002", "x"], ["r2", "+15550100002", "x"]]
        assert _quiet(task.check_if_task_is_complete, STATE, observed)


class TestContactsInComparison:
    def test_contact_order_and_ids_do_not_matter(self):
        from open_apps.tasks.tasks import AppStateComparison

        a = _state(ALICE, BOB)
        b = _state(dict(BOB, id=7), ALICE)
        assert _quiet(AppStateComparison(a, b).compare)

    def test_an_added_contact_is_a_difference(self):
        from open_apps.tasks.tasks import AppStateComparison

        assert not _quiet(AppStateComparison(_state(ALICE), _state(ALICE, BOB)).compare)


def _send_message_configs():
    """Every SendMessageTask in config/tasks, top-level or inside a composite."""
    found = []
    for path in sorted((config_dir() / "tasks").glob("*.yaml")):
        cfg = OmegaConf.to_container(OmegaConf.load(path), resolve=False)

        def walk(node):
            if isinstance(node, dict):
                if str(node.get("_target_", "")).endswith("SendMessageTask"):
                    found.append(pytest.param(node, id=f"{path.stem}-{len(found)}"))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(cfg)
    return found


_CONFIGS = _send_message_configs()


def test_found_every_send_message_task():
    assert len(_CONFIGS) >= 90


@pytest.mark.parametrize("node", _CONFIGS)
def test_every_send_message_task_builds_against_the_seed(node):
    """A task naming someone the seed can't resolve to one thread would only
    fail at scoring time, mid-episode. Catch it here."""
    with open(_STATES_DIR / "initial_state.json", encoding="utf-8") as f:
        initial = json.load(f)
    task = instantiate(node)
    target = task.get_target_state(initial)
    assert target["messenger"] != initial["messenger"]
