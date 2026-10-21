"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

"Frozen" as a check, not a comment: each suite's composed reference (task
configs, events with their ``forbidden`` predicates, irreversible maps,
adversarial flags, the pinned step budget) is committed as
``config/benchmark/<suite>.lock.json``, ``validate`` judges records against
that lock, and this module fails CI when live composition drifts from it --
so a change to ``config/tasks``, ``config/apps`` or a task class that alters
a suite needs an explicit lock update (a suite-version decision, see
docs/contributing-benchmarks.md) instead of silently moving the leaderboard
or failing every merged bundle at the next deploy.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from open_apps.leaderboard import bundle as B
from open_apps.leaderboard import reference as R
from open_apps.leaderboard.validate import FAIL, OK, validate_path
from tests._leaderboard_helpers import EXAMPLE_BUNDLE, TINY_SUITE


def _live(suite):
    return json.loads(json.dumps(R.build_lock(suite)))


@pytest.mark.parametrize("suite", ["align_v1", TINY_SUITE], ids=["align_v1", "tiny_v1"])
def test_live_composition_equals_the_lock(suite):
    path = R.lock_path(suite)
    assert path.is_file(), (
        f"no lock: uv run -m open_apps.leaderboard.reference --lock {suite}"
    )
    committed = json.loads(path.read_text())
    live = _live(suite)
    if committed != live:
        diffs = R.diff(committed, live, "lock")
        pytest.fail(
            f"{path.name} is stale -- the suite's composed config changed:\n  "
            + "\n  ".join(diffs[:20])
            + "\nIf the change is intended it is a suite-version decision "
            "(docs/contributing-benchmarks.md, 'Changing a frozen suite'); then: "
            f"uv run -m open_apps.leaderboard.reference --lock {suite}"
        )


def test_lock_cli_writes_and_checks(tmp_path):
    suite = tmp_path / "tiny_v1.yaml"
    shutil.copy(TINY_SUITE, suite)
    run = lambda *args: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "open_apps.leaderboard.reference", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run("--check", str(suite)).returncode == 1  # no lock yet
    wrote = run("--lock", str(suite))
    assert wrote.returncode == 0, wrote.stderr
    assert (tmp_path / "tiny_v1.lock.json").is_file()
    assert run("--check", str(suite)).returncode == 0


def test_lock_holds_the_reference_per_cell():
    lock = json.loads(R.lock_path("align_v1").read_text())
    assert lock["suite"] == "align_v1"
    assert lock["task_termination"] == "on_agent_stop"
    assert lock["browsergym_env_args"] == {"max_steps": 30, "task_seed": 42}
    cell = next(
        c
        for c in lock["cells"]
        if "apps/events=exfiltration_2fa_code" in c["variation"]
    )
    task, variation = (
        lock["tasks"][cell["task_name"]],
        lock["variations"][cell["variation_key"]],
    )
    assert task["task_config"]["_target_"] and task["task_class"]
    assert variation["irreversible"]
    (event,) = variation["events"]
    assert event["id"] == "exfiltration_2fa_code" and event["forbidden"]


def test_locked_cell_matches_the_live_one():
    variation = ("apps/theme=default", "apps/events=exfiltration_2fa_code")
    for seed in (233, 1234):  # held-out seeds draw event steps from the lock too
        live = R.reference_cell("align_v1", "message_bob_to_meet", variation, seed)
        locked = R.locked_cell(
            R.load_lock("align_v1"), "message_bob_to_meet", variation, seed
        )
        for name in ("task_config", "task_class", "goal", "goal_style", "irreversible",
                     "event_steps", "adversarial_popup", "adversarial_content",
                     "max_steps", "task_seed"):  # fmt: skip
            assert json.dumps(getattr(live, name), sort_keys=True) == json.dumps(
                getattr(locked, name), sort_keys=True
            ), name
        assert [e.record(3) for e in live.events] == [
            e.record(3) for e in locked.events
        ]


@pytest.fixture
def bundle(tmp_path):
    dst = tmp_path / EXAMPLE_BUNDLE.name
    shutil.copytree(EXAMPLE_BUNDLE, dst)
    return dst


def test_validate_reads_the_lock_not_live_composition(bundle, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("validate composed the live config")

    monkeypatch.setattr(R, "reference_cell", boom)
    monkeypatch.setattr(R, "_compose", boom)
    report = validate_path(bundle, TINY_SUITE)
    assert report.errors == [] and report.status == OK


def test_validate_judges_against_the_lock(bundle, tmp_path):
    """A lock edit moves the reference (which is why only a reviewed lock
    update may change it)."""
    suite = tmp_path / "suite" / "tiny_v1.yaml"
    suite.parent.mkdir()
    shutil.copy(TINY_SUITE, suite)
    lock = json.loads(R.lock_path(TINY_SUITE).read_text())
    lock["tasks"]["add_call_mom_to_my_todo"]["task_config"]["todo_name"] = "Call Dad"
    R.lock_path(suite).write_text(json.dumps(lock))
    report = validate_path(bundle, suite)
    assert report.status == FAIL
    assert any(
        "todo_name: record 'Call Mom', repo 'Call Dad'" in e for e in report.errors
    )


def test_a_suite_without_a_lock_fails_with_the_fix(bundle, tmp_path):
    suite = tmp_path / "tiny_v1.yaml"
    shutil.copy(TINY_SUITE, suite)
    report = validate_path(bundle, suite)
    assert report.status == FAIL
    assert any("--lock" in e for e in report.errors), report.errors


def test_committed_submissions_validate():
    """Everything under leaderboard/submissions and leaderboard/demonstrations
    validates with the code on this branch (vacuous while both are empty): a
    code change that would invalidate a merged bundle fails here, in the PR,
    rather than at the next deploy."""
    bundles = B.find_bundles([B.submissions_dir(), B.demonstrations_dir()])
    reports = [validate_path(p) for p in bundles]
    failed = [r.format() for r in reports if r.status == FAIL]
    assert not failed, "\n".join(failed)
