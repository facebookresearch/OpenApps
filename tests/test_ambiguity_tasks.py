"""The ambiguity set (``config/tasks/ambiguity.yaml``).

* every task composes through Hydra (``tasks=ambiguity``) into an
  ``AmbiguityTask`` exactly as the launcher builds it;
* its ``premise`` -- why the goal is ambiguous -- holds against the default
  content, read off the real apps, so a content edit cannot silently make a
  task unambiguous;
* its clarified goal is achievable and not already met;
* the set stays out of ``all_tasks`` (paper and default sweeps unchanged).
"""

from __future__ import annotations

import copy
import re

import pytest
import yaml
from hydra.utils import instantiate

from open_apps.alignment.changes import COLLECTIONS
from open_apps.alignment.irreversible import resolve_app
from open_apps.alignment.predicates import _values, value_matches
from open_apps.tasks.tasks import AMBIGUITY_EXPECTED, AmbiguityTask, EditFileTask, Task
from tests._events_server import CONFIG_DIR, compose, start_apps, state_of

TASKS_DIR = CONFIG_DIR / "tasks"
RAW = yaml.safe_load((TASKS_DIR / "ambiguity.yaml").read_text())
NAMES = sorted(RAW)


@pytest.fixture(scope="module")
def cfg():
    return compose(["tasks=ambiguity"])


@pytest.fixture(scope="module")
def initial(tmp_path_factory) -> dict:
    _, client = start_apps(tmp_path_factory.mktemp("ambiguity"))
    return state_of(client)


def _task(cfg, name) -> AmbiguityTask:
    return instantiate(cfg.tasks[name])


def test_the_set_has_about_twenty_tasks_of_both_kinds():
    expected = [RAW[n]["expected"] for n in NAMES]
    assert 18 <= len(NAMES) <= 25
    assert set(expected) == set(AMBIGUITY_EXPECTED)


def test_ambiguity_is_not_in_all_tasks():
    all_tasks = yaml.safe_load((TASKS_DIR / "all_tasks.yaml").read_text())
    assert "ambiguity" not in all_tasks["defaults"]
    keys = set(compose([]).tasks)
    assert keys and not keys & set(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_every_task_composes_like_the_launcher_builds_it(cfg, name):
    task = _task(cfg, name)
    assert isinstance(task, AmbiguityTask) and task.goal
    if task.expected == "ask":
        assert isinstance(task.clarified, Task) and task.user_reply
    else:
        assert task.clarified is None and task.user_reply is None
    # task_id is the BrowserGym registration key: unique across the set.
    assert len({_task(cfg, n).task_id for n in NAMES}) == len(NAMES)


def _rows(state: dict, app: str) -> list:
    if app == "messenger":
        return list(state.get("messenger") or [])
    coll = next(c for c in COLLECTIONS if c.app == app)
    return coll.items(state)


def _count_ok(spec, n: int) -> bool:
    if isinstance(spec, int):
        return n == spec
    m = re.fullmatch(r">=(\d+)", str(spec))
    assert m, f"bad count {spec!r}"
    return n >= int(m.group(1))


@pytest.mark.parametrize("name", NAMES)
def test_premise_holds_against_the_default_content(cfg, initial, name):
    task = _task(cfg, name)
    assert task.premise, f"{name} declares no premise"
    for check in task.premise:
        if "goal_lacks" in check:
            assert not re.search(check["goal_lacks"], task.goal), (name, check)
            continue
        app = resolve_app(check["app"])
        assert app, (name, check)
        rows = [
            r
            for r in _rows(initial, app)
            if all(
                any(value_matches(exp, v) for v in _values(r, str(k)))
                for k, exp in (check.get("where") or {}).items()
            )
        ]
        assert _count_ok(check["count"], len(rows)), (name, check, len(rows))


def _with_file_line(state: dict, path: str, line: str) -> dict:
    out = copy.deepcopy(state)
    stack = [out["codeeditor"]]
    while stack:
        node = stack.pop()
        for child in node.get("children") or []:
            if child.get("type") == "file" and child.get("path") == path:
                child["content"] = line + "\n" + child.get("content", "")
                return out
            stack.append(child)
    raise AssertionError(f"no file {path}")


@pytest.mark.parametrize("name", NAMES)
def test_clarified_goal_is_achievable_and_not_already_met(cfg, initial, name):
    task = _task(cfg, name)
    if task.expected == "refuse":
        # The safe outcome is the untouched state.
        assert task.check_if_task_is_complete(initial, copy.deepcopy(initial))
        return
    assert not task.check_if_task_is_complete(initial, copy.deepcopy(initial))
    if isinstance(task.clarified, EditFileTask):
        done = _with_file_line(initial, task.clarified.file_path, "# reviewed")
    else:
        done = task.get_target_state(initial)
    assert task.check_if_task_is_complete(initial, done)


def test_launcher_runs_ambiguity_tasks_on_agent_stop():
    from open_apps.launcher import AgentLauncher

    launcher = AgentLauncher.__new__(AgentLauncher)  # no ports, no apps
    launcher.config = compose(["tasks=ambiguity", f"task_name={NAMES[0]}"])
    launcher.web_app_url = "http://testserver"
    env_args = launcher.setup_browsergym_task()
    # Recorded in the run config (EpisodeRecord identity) and handed to the task.
    assert launcher.config.task_termination == "on_agent_stop"
    assert env_args.task_kwargs["task_termination"] == "on_agent_stop"
