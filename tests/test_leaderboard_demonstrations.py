"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Demonstrations (``leaderboard/demonstrations/<contributor>__<topic>/``): the
same EpisodeRecord schema step as submissions, no suite coverage, never
ranked. Training and analysis data, by a person (``agent: human``) or any
agent.
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys

import pytest
import yaml

from open_apps.leaderboard import bundle as B
from open_apps.leaderboard.build import build
from open_apps.leaderboard.validate import (
    DEMONSTRATION,
    FAIL,
    OK,
    validate_bundle,
    validate_path,
)
from tests._leaderboard_helpers import (
    EXAMPLE_BUNDLE,
    EXAMPLE_DEMO,
    make_example_demonstration,
)


@pytest.fixture
def demo(tmp_path):
    """A writable copy of the committed example demonstration."""
    dst = tmp_path / "demonstrations" / EXAMPLE_DEMO.name
    shutil.copytree(EXAMPLE_DEMO, dst)
    return dst


def _lines(path):
    with gzip.open(path / B.EPISODES_FILE, "rt") as fh:
        return [json.loads(line) for line in fh]


def _edit_meta(path, **changes):
    meta = yaml.safe_load((path / B.META_FILE).read_text())
    meta.update(changes)
    (path / B.META_FILE).write_text(yaml.safe_dump(meta, sort_keys=False))


def test_example_demonstration_passes(demo):
    report = validate_path(demo)
    assert report.errors == [], report.format()
    assert report.kind == DEMONSTRATION
    assert report.status == OK
    assert report.n_records == 2
    # No suite, so nothing to cover and nothing missing.
    assert (report.n_expected, report.missing) == (0, [])
    assert "demonstration" in report.format() and "not ranked" in report.format()


def test_committed_example_is_current(tmp_path):
    """tests/fixtures/leaderboard/make_fixtures.py regenerates it."""
    fresh = make_example_demonstration(tmp_path / EXAMPLE_DEMO.name)
    for name in (B.META_FILE, B.EPISODES_FILE):
        assert (fresh / name).read_bytes() == (EXAMPLE_DEMO / name).read_bytes(), (
            f"{EXAMPLE_DEMO.name}/{name} is stale: "
            "uv run python tests/fixtures/leaderboard/make_fixtures.py"
        )


def test_episode_schema_violation_fails(demo):
    lines = _lines(demo)
    lines[0]["termination"]["reason"] = "gave_up"
    B.write_episodes(lines, demo / B.EPISODES_FILE)
    report = validate_path(demo)
    assert report.status == FAIL
    assert any("termination.reason" in e for e in report.errors)


def test_scores_are_optional_but_checked_when_present(demo):
    from open_apps.alignment.record import EpisodeRecord
    from open_apps.alignment.score import score

    lines = _lines(demo)
    for line in lines:
        line["scores"] = score(EpisodeRecord.from_dict(line)).summary()
    B.write_episodes(lines, demo / B.EPISODES_FILE)
    assert validate_path(demo).status == OK

    lines[0]["scores"]["success"] = not lines[0]["scores"]["success"]
    B.write_episodes(lines, demo / B.EPISODES_FILE)
    report = validate_path(demo)
    assert report.status == FAIL
    assert any("scores disagree with the scorer" in e for e in report.errors)


def test_meta_schema_violation_fails(demo):
    _edit_meta(demo, surprise=1, date="yesterday")
    report = validate_path(demo)
    assert report.status == FAIL
    assert any("unexpected key 'surprise'" in e for e in report.errors)
    assert any("$.date" in e for e in report.errors)


def test_dir_name_must_match_meta(demo):
    other = demo.parent / "someone__else"
    demo.rename(other)
    report = validate_path(other)
    assert any("does not match meta" in e for e in report.errors)


def test_optional_agent_yaml_is_hashed_and_redaction_checked(demo):
    agent = {"model_name": "my-model", "api_key": "sk-live-123"}
    (demo / B.AGENT_FILE).write_text(yaml.safe_dump(agent))
    _edit_meta(demo, agent="my-model", agent_config_hash=B.agent_config_hash(agent))
    report = validate_path(demo)
    assert any("unredacted" in e and "api_key" in e for e in report.errors)

    agent["api_key"] = B.REDACTED
    (demo / B.AGENT_FILE).write_text(yaml.safe_dump(agent))
    report = validate_path(demo)
    assert any("hashes to" in e for e in report.errors)  # meta hash is now stale


def test_a_demonstration_is_never_ranked(tmp_path, demo):
    """Dropped into submissions/ by mistake, it fails the submission schema
    (and so the build) rather than appearing in the table."""
    subs = tmp_path / "submissions"
    subs.mkdir()
    shutil.copytree(EXAMPLE_BUNDLE, subs / EXAMPLE_BUNDLE.name)
    shutil.copytree(demo, subs / demo.name)
    reports = [validate_bundle(p) for p in B.find_bundles([subs])]
    stray = next(r for r in reports if r.path.name == demo.name)
    assert stray.status == FAIL
    with pytest.raises(ValueError, match=demo.name):
        build(reports, when="2026-10-16T00:00:00Z")


def test_cli_validates_demonstrations(demo):
    proc = subprocess.run(
        [sys.executable, "-m", "open_apps.leaderboard.validate", str(demo.parent)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"OK      {demo.name}" in proc.stdout
    assert "demonstration (not ranked)" in proc.stdout


def test_committed_demonstrations_validate():
    """Everything under leaderboard/demonstrations passes (vacuous until the
    first one lands)."""
    root = B.demonstrations_dir()
    reports = (
        [validate_path(p) for p in B.find_bundles([root])] if root.is_dir() else []
    )
    assert [r.format() for r in reports if r.status != OK] == []


def test_mini_validator_agrees_with_jsonschema(demo):
    jsonschema = pytest.importorskip("jsonschema")
    schema = B.load_schema("demonstration.schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    meta = yaml.safe_load((demo / B.META_FILE).read_text())
    for candidate in (
        meta,
        dict(meta, kind="submission"),
        dict(meta, agent_config_hash="xyz"),
        dict(meta, agent_config_hash="0123456789ab"),
        {k: v for k, v in meta.items() if k != "topic"},
    ):
        ours = not B.schema_errors(candidate, schema)
        theirs = jsonschema.Draft202012Validator(schema).is_valid(candidate)
        assert ours == theirs, candidate
