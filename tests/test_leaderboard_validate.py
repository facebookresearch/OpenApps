"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

``open_apps.leaderboard.validate``: a good bundle passes; missing cells make
it ``partial``; a tampered score, a schema violation, a leaked secret or a
cell outside the suite fail it. Offline -- the example bundle is re-scored by
the real scorer from its records alone.
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
from open_apps.leaderboard.validate import FAIL, OK, PARTIAL, validate_bundle
from tests._leaderboard_helpers import EXAMPLE_BUNDLE, TINY_SUITE


@pytest.fixture
def bundle(tmp_path):
    """A writable copy of the committed example bundle."""
    dst = tmp_path / EXAMPLE_BUNDLE.name
    shutil.copytree(EXAMPLE_BUNDLE, dst)
    return dst


def _lines(path):
    with gzip.open(path / B.EPISODES_FILE, "rt") as fh:
        return [json.loads(line) for line in fh]


def _rewrite(path, lines):
    B.write_episodes(lines, path / B.EPISODES_FILE)


def _edit_meta(path, **changes):
    meta = yaml.safe_load((path / B.META_FILE).read_text())
    meta.update(changes)
    (path / B.META_FILE).write_text(yaml.safe_dump(meta, sort_keys=False))


def test_good_bundle_passes(bundle):
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert report.errors == []
    assert report.status == OK
    assert (report.n_records, report.n_expected, report.missing) == (4, 4, [])
    # Rows come from the re-scored records, labelled by bundle, not model.
    assert len(report.rows) == 4
    assert {r["agent"] for r in report.rows} == {"example__dummy"}


def test_missing_cells_make_it_partial(bundle):
    lines = _lines(bundle)
    _rewrite(bundle, lines[1:])
    _edit_meta(bundle, n_episodes=3)
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert report.errors == []
    assert report.status == PARTIAL
    assert len(report.missing) == 1
    task, cell, seed = report.missing[0]
    ident = lines[0]["identity"]
    assert (task, seed) == (ident["task_name"], ident["seed"])
    assert "PARTIAL" in report.format() and "missing:" in report.format()


def test_tampered_score_fails(bundle):
    lines = _lines(bundle)
    victim = next(line for line in lines if not line["scores"]["aligned"])
    victim["scores"]["aligned"] = True
    _rewrite(bundle, lines)
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert report.status == FAIL
    assert any("aligned: submitted True, scorer says False" in e for e in report.errors)


def test_tampered_bool_as_int_fails(bundle):
    """``True == 1`` in Python; the comparison must not let that through."""
    lines = _lines(bundle)
    lines[0]["scores"]["harm"] = int(lines[0]["scores"]["harm"])
    _rewrite(bundle, lines)
    assert validate_bundle(bundle, suite=TINY_SUITE).status == FAIL


def test_tampered_state_fails_rescore(bundle):
    """Editing the evidence (not the score) is caught the same way."""
    lines = _lines(bundle)
    victim = next(line for line in lines if line["scores"]["success"])
    victim["final_state"] = victim["initial_state"]
    _rewrite(bundle, lines)
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert report.status == FAIL
    assert any("success: submitted True, scorer says False" in e for e in report.errors)


def test_episode_schema_violation_fails(bundle):
    lines = _lines(bundle)
    lines[0]["termination"]["reason"] = "gave_up"
    del lines[1]["final_state"]
    _rewrite(bundle, lines)
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert report.status == FAIL
    assert any("termination.reason" in e for e in report.errors)
    assert any("missing required 'final_state'" in e for e in report.errors)


def test_missing_scores_fails(bundle):
    lines = _lines(bundle)
    del lines[0]["scores"]
    _rewrite(bundle, lines)
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert any("missing the 'scores' object" in e for e in report.errors)


def test_meta_schema_violation_fails(bundle):
    _edit_meta(bundle, verified=True, surprise="field")
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert report.status == FAIL
    assert any("$.verified" in e for e in report.errors)
    assert any("unexpected key 'surprise'" in e for e in report.errors)


def test_dir_name_must_match_meta(bundle):
    renamed = bundle.parent / "someone__dummy__tiny_v1"
    bundle.rename(renamed)
    report = validate_bundle(renamed, suite=TINY_SUITE)
    assert any("does not match meta" in e for e in report.errors)


def test_edited_agent_yaml_fails(bundle):
    agent = yaml.safe_load((bundle / B.AGENT_FILE).read_text())
    agent["temperature"] = 0.0
    (bundle / B.AGENT_FILE).write_text(yaml.safe_dump(agent))
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert any("hashes to" in e for e in report.errors)


def test_leaked_secret_fails(bundle):
    agent = yaml.safe_load((bundle / B.AGENT_FILE).read_text())
    agent["api_key"] = "sk-live-123"
    (bundle / B.AGENT_FILE).write_text(yaml.safe_dump(agent))
    _edit_meta(bundle, agent_config_hash=B.agent_config_hash(agent))
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert any("unredacted" in e and "api_key" in e for e in report.errors)


def test_duplicate_and_foreign_cells_fail(bundle):
    lines = _lines(bundle)
    foreign = json.loads(json.dumps(lines[0]))
    foreign["identity"]["variation"] = ["apps/theme=solarized"]
    _rewrite(bundle, [*lines, lines[0], foreign])
    _edit_meta(bundle, n_episodes=6)
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert report.status == FAIL
    assert any("submitted 2 times" in e for e in report.errors)
    assert any("not a tiny_v1 cell" in e for e in report.errors)


def test_records_from_another_suite_fail(bundle):
    lines = _lines(bundle)
    lines[0]["identity"]["suite_version"] = None
    _rewrite(bundle, lines)
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert any("not run with benchmark=tiny_v1" in e for e in report.errors)


def test_cli_exit_codes(bundle, tmp_path):
    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "open_apps.leaderboard.validate", *map(str, args)],
            capture_output=True,
            text=True,
        )

    ok = run(bundle, "--suite", TINY_SUITE)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "OK" in ok.stdout and "4/4 suite cells covered" in ok.stdout

    # A directory of bundles is accepted too.
    assert run(bundle.parent, "--suite", TINY_SUITE).returncode == 0

    lines = _lines(bundle)
    _rewrite(bundle, lines[1:])
    _edit_meta(bundle, n_episodes=3)
    partial = run(bundle, "--suite", TINY_SUITE)
    assert partial.returncode == 0 and "PARTIAL" in partial.stdout
    assert run(bundle, "--suite", TINY_SUITE, "--strict").returncode == 1

    lines[1]["scores"]["steps"] = 999
    _rewrite(bundle, lines[1:])
    failed = run(bundle, "--suite", TINY_SUITE)
    assert failed.returncode == 1 and "FAIL" in failed.stdout

    assert run(tmp_path / "nothing-here").returncode == 1


# --- the schemas themselves --------------------------------------------------


def test_committed_episode_schema_is_current():
    committed = json.loads((B.schema_dir() / "episode.schema.json").read_text())
    assert committed == B.episode_schema(), (
        "leaderboard/schema/episode.schema.json is stale: "
        "uv run -m open_apps.leaderboard.bundle --write-schemas"
    )


def test_mini_validator_agrees_with_jsonschema(bundle):
    """The hand-rolled checker must accept and reject what jsonschema does."""
    jsonschema = pytest.importorskip("jsonschema")
    meta_schema = B.load_schema("meta.schema.json")
    episode_schema = B.load_schema("episode.schema.json")
    for schema in (meta_schema, episode_schema):
        jsonschema.Draft202012Validator.check_schema(schema)

    meta = yaml.safe_load((bundle / B.META_FILE).read_text())
    bad_metas = [
        dict(meta, verified=True),
        dict(meta, date="16/10/2026"),
        dict(meta, agent_config_hash="xyz"),
        {k: v for k, v in meta.items() if k != "org"},
        dict(meta, verified={"date": "2026-10-17", "seeds_hash": "abc", "aligned": 0.5, "delta": -0.1}),
    ]
    for candidate in [meta, *bad_metas]:
        ours = not B.schema_errors(candidate, meta_schema)
        theirs = jsonschema.Draft202012Validator(meta_schema).is_valid(candidate)
        assert ours == theirs, candidate

    record = {k: v for k, v in _lines(bundle)[0].items() if k != "scores"}
    broken = json.loads(json.dumps(record))
    broken["steps"][0]["step"] = "zero"
    for candidate in (record, broken, dict(record, record_version=2)):
        ours = not B.schema_errors(candidate, episode_schema)
        theirs = jsonschema.Draft202012Validator(episode_schema).is_valid(candidate)
        assert ours == theirs


def test_mini_validator_refuses_unknown_keywords():
    with pytest.raises(ValueError, match="not supported"):
        B.schema_errors({}, {"type": "object", "oneOf": []})
