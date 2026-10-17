"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

``open_apps.leaderboard.pack`` over a fixture sweep: the bundle it writes
validates, finished runs without ``episode.json`` are rebuilt, secrets never
reach agent.yaml, re-runs are resolved by a fixed rule, and the committed
example bundle is what ``pack`` produces today.
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys

import pytest
import yaml

from open_apps.leaderboard import bundle as B
from open_apps.leaderboard.pack import pack
from open_apps.leaderboard.validate import OK, PARTIAL, validate_bundle
from tests._leaderboard_helpers import (
    DUMMY_AGENT,
    EXAMPLE_BUNDLE,
    FIXED_DATE,
    JOBS,
    REBUILT_JOB,
    TINY_SUITE,
    make_example_bundle,
    write_logs_tree,
)


def _lines(path):
    with gzip.open(path / B.EPISODES_FILE, "rt") as fh:
        return [json.loads(line) for line in fh]


@pytest.fixture
def logs(tmp_path):
    return write_logs_tree(tmp_path / "logs")


def test_pack_writes_a_valid_bundle(logs, tmp_path):
    out = tmp_path / "example__dummy__tiny_v1"
    result = pack([logs], org="example", agent="dummy", out=out, date=FIXED_DATE)
    assert result.n_episodes == len(JOBS)
    assert result.skipped == [] and result.duplicates == []
    assert sorted(p.name for p in out.iterdir()) == [
        B.AGENT_FILE,
        B.EPISODES_FILE,
        B.META_FILE,
    ]
    report = validate_bundle(out, suite=TINY_SUITE)
    assert report.errors == [] and report.status == OK


def test_meta_is_filled_from_the_runs(logs, tmp_path):
    out = pack([logs], org="example", agent="dummy", out=tmp_path / "b", date=FIXED_DATE).out
    meta = yaml.safe_load((out / B.META_FILE).read_text())
    agent = yaml.safe_load((out / B.AGENT_FILE).read_text())
    assert meta["suite"] == "tiny_v1"  # from the records' suite_version
    assert meta["model"] == "dummy"
    assert meta["date"] == FIXED_DATE
    assert meta["n_episodes"] == len(JOBS)
    assert meta["verified"] is False
    assert meta["agent_config_hash"] == B.agent_config_hash(agent)


def test_agent_yaml_is_the_run_config_with_secrets_redacted(logs, tmp_path):
    out = pack([logs], org="example", agent="dummy", out=tmp_path / "b", date=FIXED_DATE).out
    agent = yaml.safe_load((out / B.AGENT_FILE).read_text())
    assert agent["api_key"] == agent["hostname"] == B.REDACTED
    # Everything else is copied exactly -- including look-alikes.
    assert agent["max_tokens"] == DUMMY_AGENT["max_tokens"]
    assert {k: v for k, v in agent.items() if k not in ("api_key", "hostname")} == {
        k: v for k, v in DUMMY_AGENT.items() if k not in ("api_key", "hostname")
    }
    meta = yaml.safe_load((out / B.META_FILE).read_text())
    assert sorted(meta["redacted"]) == ["api_key", "hostname"]
    assert "sk-not-a-real-key" not in (out / B.AGENT_FILE).read_text()


def test_finished_run_without_episode_json_is_rebuilt(logs, tmp_path):
    from open_apps.alignment.record import EPISODE_FILENAME

    job = next(j for j in JOBS if j[0] == REBUILT_JOB)
    assert not list((logs / str(REBUILT_JOB)).rglob(EPISODE_FILENAME))
    out = pack([logs], org="example", agent="dummy", out=tmp_path / "b").out
    rebuilt = [
        line
        for line in _lines(out)
        if line["identity"]["task_name"] == job[2] and line["identity"]["variation"] == job[3]
    ]
    assert len(rebuilt) == 1
    ident = rebuilt[0]["identity"]
    # Identity came from the job's config.yaml, the way the launcher builds it.
    assert ident["suite_version"] == "tiny_v1" and ident["seed"] == 233
    assert ident["agent_name"] == "dummy"
    assert rebuilt[0]["termination"]["reason"] == "success"


def test_unfinished_runs_are_skipped_and_show_as_missing(logs, tmp_path):
    (logs / "9" / "2026-10-16_crashed").mkdir(parents=True)
    from open_apps.alignment.record import STATES_FILENAME

    (logs / "9" / "2026-10-16_crashed" / STATES_FILENAME).write_text("{}")
    victim = next((logs / "0").rglob("episode.json"))
    victim.unlink()
    result = pack([logs], org="example", agent="dummy", out=tmp_path / "example__dummy__tiny_v1")
    assert result.n_episodes == len(JOBS) - 1
    reasons = " ".join(r for _, r in result.skipped)
    assert "did not finish" in reasons
    report = validate_bundle(result.out, suite=TINY_SUITE)
    assert report.status == PARTIAL and len(report.missing) == 1, report.format()


def test_rerun_of_a_crash_wins_and_is_reported(logs, tmp_path):
    from tests._leaderboard_helpers import tiny_record

    _, name, task, variation = JOBS[0]
    crashed = tiny_record(name, task, variation)
    crashed["termination"] = {"reason": "error", "final_action": None, "err_msg": "boom"}
    exp = logs / "0" / "2026-10-16_99-99-99_dummy_on_rerun"
    exp.mkdir()
    (exp / "episode.json").write_text(json.dumps(crashed))
    os.utime(exp / "episode.json", (2e9, 2e9))  # newer, but an error
    result = pack([logs], org="example", agent="dummy", out=tmp_path / "b")
    assert result.n_episodes == len(JOBS)
    assert [loser for _, loser in result.duplicates] == [exp]
    assert all(line["termination"]["reason"] != "error" for line in _lines(result.out))


def test_records_outside_the_suite_are_skipped(logs, tmp_path):
    from tests._leaderboard_helpers import tiny_record

    stray = tiny_record(*JOBS[0][1:])
    stray["identity"]["suite_version"] = None
    exp = logs / "7" / "adhoc"
    exp.mkdir(parents=True)
    (exp / "episode.json").write_text(json.dumps(stray))
    result = pack([logs], org="example", agent="dummy", out=tmp_path / "b")
    assert result.n_episodes == len(JOBS)
    assert any("suite_version None" in r for _, r in result.skipped)


def test_mixed_agents_are_refused(logs, tmp_path):
    from tests._leaderboard_helpers import tiny_record

    other = tiny_record(*JOBS[0][1:])
    other["identity"]["agent_name"] = "someone-else"
    other["identity"]["variation"] = ["apps/theme=solarized"]
    exp = logs / "8" / "other"
    exp.mkdir(parents=True)
    (exp / "episode.json").write_text(json.dumps(other))
    with pytest.raises(ValueError, match="several agents"):
        pack([logs], org="example", agent="dummy", out=tmp_path / "b")


def test_bad_slugs_are_refused(logs, tmp_path):
    with pytest.raises(ValueError, match="org"):
        pack([logs], org="my__org", agent="dummy", out=tmp_path / "b")


def test_pack_is_deterministic(logs, tmp_path):
    a = pack([logs], org="example", agent="dummy", out=tmp_path / "a", date=FIXED_DATE).out
    b = pack([logs], org="example", agent="dummy", out=tmp_path / "b", date=FIXED_DATE).out
    for name in (B.META_FILE, B.AGENT_FILE, B.EPISODES_FILE):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_committed_example_bundle_is_current(tmp_path):
    fresh = make_example_bundle(tmp_path / EXAMPLE_BUNDLE.name, tmp_path / "logs")
    for name in (B.META_FILE, B.AGENT_FILE):
        assert (fresh / name).read_text() == (EXAMPLE_BUNDLE / name).read_text(), (
            f"{name} is stale: uv run python tests/fixtures/leaderboard/make_fixtures.py"
        )
    assert _lines(fresh) == _lines(EXAMPLE_BUNDLE), (
        "episodes are stale: uv run python tests/fixtures/leaderboard/make_fixtures.py"
    )


def test_example_bundle_is_not_a_public_submission():
    assert B.submissions_dir() not in EXAMPLE_BUNDLE.parents


def test_cli(logs, tmp_path):
    out = tmp_path / "example__dummy__tiny_v1"
    cmd = [sys.executable, "-m", "open_apps.leaderboard.pack", str(logs)]
    cmd += ["--org", "example", "--agent", "dummy", "--out", str(out)]
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"wrote {out} ({len(JOBS)} episodes)" in proc.stdout
    assert "open_apps.leaderboard.validate" in proc.stdout

    empty = subprocess.run(
        [sys.executable, "-m", "open_apps.leaderboard.pack", str(tmp_path / "nope")]
        + ["--org", "x", "--agent", "y"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert empty.returncode == 1 and "no episode records" in empty.stderr
