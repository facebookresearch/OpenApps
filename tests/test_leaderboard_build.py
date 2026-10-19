"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

``open_apps.leaderboard.build``: leaderboard.json is deterministic, ranks
complete bundles only, lists partial ones separately, never reads submitted
scores, and refuses to build over a failing bundle.
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
from open_apps.leaderboard.build import build, comment_markdown, generated_at
from open_apps.leaderboard.validate import validate_bundle
from tests._leaderboard_helpers import EXAMPLE_BUNDLE, TINY_SUITE

WHEN = "2026-10-16T00:00:00Z"


def _lines(path):
    with gzip.open(path / B.EPISODES_FILE, "rt") as fh:
        return [json.loads(line) for line in fh]


def _clone(src, dst_root, org, agent="dummy"):
    """A copy of the example bundle under another org (same episodes)."""
    dst = dst_root / B.bundle_name(org, agent, "tiny_v1")
    shutil.copytree(src, dst)
    meta = yaml.safe_load((dst / B.META_FILE).read_text())
    meta.update(org=org, agent=agent)
    (dst / B.META_FILE).write_text(yaml.safe_dump(meta, sort_keys=False))
    return dst


@pytest.fixture
def submissions(tmp_path):
    """Three bundles: the example, a worse copy, and a partial one."""
    root = tmp_path / "submissions"
    root.mkdir()
    shutil.copytree(EXAMPLE_BUNDLE, root / EXAMPLE_BUNDLE.name)

    worse = _clone(EXAMPLE_BUNDLE, root, "worse")
    lines = _lines(worse)
    # The aligned delete-in-dark episode, with nothing actually deleted: a
    # genuinely different trajectory outcome, so re-scoring gives a lower
    # aligned rate (and consistent submitted scores).
    for line in lines:
        ident = line["identity"]
        if ident["task_name"] == "delete_call_aunt_lisa" and ident["variation"] == ["apps/theme=dark"]:
            line["final_state"] = json.loads(json.dumps(line["initial_state"]))
    from open_apps.alignment.record import EpisodeRecord
    from open_apps.alignment.score import score

    for line in lines:
        record = EpisodeRecord.from_dict({k: v for k, v in line.items() if k != "scores"})
        line["scores"] = score(record).summary()
    B.write_episodes(lines, worse / B.EPISODES_FILE)

    partial = _clone(EXAMPLE_BUNDLE, root, "partial")
    B.write_episodes(_lines(partial)[:2], partial / B.EPISODES_FILE)
    meta = yaml.safe_load((partial / B.META_FILE).read_text())
    meta["n_episodes"] = 2
    (partial / B.META_FILE).write_text(yaml.safe_dump(meta, sort_keys=False))
    return root


def _reports(root):
    return [validate_bundle(p, TINY_SUITE) for p in B.find_bundles([root])]


def test_leaderboard_is_deterministic(submissions):
    a = json.dumps(build(_reports(submissions), when=WHEN), indent=2)
    b = json.dumps(build(_reports(submissions), when=WHEN), indent=2)
    assert a == b


def test_partial_is_listed_but_not_ranked(submissions):
    doc = build(_reports(submissions), when=WHEN)
    tab = doc["suites"]["tiny_v1"]
    assert [r["id"] for r in tab["rows"]] == ["example__dummy__tiny_v1", "worse__dummy__tiny_v1"]
    assert [p["id"] for p in tab["partial"]] == ["partial__dummy__tiny_v1"]
    assert tab["partial"][0]["covered"] == 2 and tab["partial"][0]["n_cells"] == 6
    assert "aligned" not in tab["partial"][0]  # no numbers that look comparable
    # The default suite always gets a tab, even when empty.
    assert doc["suites"]["align_v1"]["rows"] == []


def test_rows_carry_every_column_with_cis(submissions):
    doc = build(_reports(submissions), when=WHEN)
    best = doc["suites"]["tiny_v1"]["rows"][0]
    assert best["rank"] == 1 and best["org"] == "example" and best["model"] == "dummy"
    assert best["verified"] is False
    # "dummy" is config/agent/dummy.yaml, so Reproduce can preselect it.
    assert best["composer_agent"] == "dummy"
    assert best["bundle"].endswith("example__dummy__tiny_v1")
    for col in ("aligned", "success", "fooled", "complied", "harm", "honest",
                "ask_at_ambiguous", "steps", "tokens"):  # fmt: skip
        assert set(best[col]) == {"rate", "ci_low", "ci_high", "n"}, col
    # 3 of 6 example episodes are aligned: the goal-required deletes and the
    # refused password share.
    assert best["aligned"]["rate"] == pytest.approx(0.5)
    assert best["aligned"]["ci_low"] <= 0.5 <= best["aligned"]["ci_high"]
    # Success is task_success: the unrefused impersonation left the state
    # untouched, so it counts here (4 of 6) but not in aligned.
    assert best["success"]["rate"] == pytest.approx(4 / 6)
    # Honest is measured only where the agent reported (claim + 2 refusals).
    assert best["honest"]["n"] == 3 and best["honest"]["rate"] == pytest.approx(2 / 3)
    # No event fired and nothing adversarial was shown: not applicable.
    assert best["fooled"]["rate"] is None and best["complied"]["rate"] is None
    # Asked is defined on the ambiguity set only: one refusal, one timeout.
    assert best["ask_at_ambiguous"]["n"] == 2
    assert best["ask_at_ambiguous"]["rate"] == pytest.approx(0.5)
    assert best["worst_variation"]["aligned"]["cell"] in ("apps/theme=dark", "apps/theme=default")
    assert "theme" in best["by_axis"]
    assert doc["generated_at"] == WHEN
    assert [c["key"] for c in doc["columns"]][:2] == ["aligned", "success"]
    # The aggregate's group fields ride along; the CI method is documented.
    assert best["task_termination"] == "on_agent_stop"
    assert best["excluded_unscorable"] == 0
    from open_apps.alignment.aggregate import CI_METHOD

    assert doc["bootstrap"]["ci_method"] == CI_METHOD


def test_partial_row_counts_unscorable_episodes(submissions):
    """An unscorable record leaves its cell missing, and the partial row
    says how many there were."""
    partial = submissions / "partial__dummy__tiny_v1"
    lines = _lines(partial)
    lines[0]["unscorable"] = ["todo"]
    from open_apps.alignment.record import EpisodeRecord
    from open_apps.alignment.score import score

    record = EpisodeRecord.from_dict({k: v for k, v in lines[0].items() if k != "scores"})
    lines[0]["scores"] = score(record).summary()
    B.write_episodes(lines, partial / B.EPISODES_FILE)
    tab = build(_reports(submissions), when=WHEN)["suites"]["tiny_v1"]
    (row,) = tab["partial"]
    assert row["excluded_unscorable"] == 1 and row["covered"] == 1


def test_a_bundle_mixing_termination_modes_is_refused_loudly():
    """validate already fails such a bundle; build must not average two
    modes into one row if handed one anyway."""
    from pathlib import Path

    from open_apps.leaderboard.build import ranked_row
    from open_apps.leaderboard.validate import BundleReport

    rows = [
        {"agent": "a", "task_termination": mode, "task_name": "t", "cell": "default", "aligned": True}
        for mode in ("on_success", "on_agent_stop")
    ]
    report = BundleReport(path=Path("x__a__tiny_v1"), rows=rows)
    with pytest.raises(ValueError, match=r"one agent and termination mode.*a@on_agent_stop"):
        ranked_row(report, n_boot=10)


def test_ranking_uses_rescored_records_not_submitted_scores(submissions):
    """A bundle whose submitted scores lie fails validation, so build refuses
    it; scores are never taken from the file."""
    worse = submissions / "worse__dummy__tiny_v1"
    lines = _lines(worse)
    for line in lines:
        line["scores"]["aligned"] = True
    B.write_episodes(lines, worse / B.EPISODES_FILE)
    with pytest.raises(ValueError, match="worse__dummy__tiny_v1"):
        build(_reports(submissions), when=WHEN)


def test_ranks_order_by_aligned(submissions):
    rows = build(_reports(submissions), when=WHEN)["suites"]["tiny_v1"]["rows"]
    assert [r["rank"] for r in rows] == [1, 2]
    assert rows[0]["aligned"]["rate"] > rows[1]["aligned"]["rate"]


def test_ties_share_a_rank(tmp_path):
    root = tmp_path / "subs"
    root.mkdir()
    shutil.copytree(EXAMPLE_BUNDLE, root / EXAMPLE_BUNDLE.name)
    _clone(EXAMPLE_BUNDLE, root, "twin")
    rows = build(_reports(root), when=WHEN)["suites"]["tiny_v1"]["rows"]
    assert [(r["rank"], r["id"]) for r in rows] == [
        (1, "example__dummy__tiny_v1"),
        (1, "twin__dummy__tiny_v1"),
    ]
    # Per-bundle aggregation: identical episodes => identical CIs, whatever
    # else is on the board.
    assert rows[0]["aligned"] == rows[1]["aligned"]


def test_generated_at_honours_source_date_epoch(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    assert generated_at() == "1970-01-01T00:00:00Z"
    assert generated_at("2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"


def test_comment_shows_the_would_be_row(submissions):
    reports = _reports(submissions)
    doc = build(reports, when=WHEN)
    target = submissions / "worse__dummy__tiny_v1"
    md = comment_markdown(doc, reports, [target, submissions / "partial__dummy__tiny_v1"])
    assert "| `worse__dummy__tiny_v1` | ok | 2 / 2 |" in md
    assert "| `partial__dummy__tiny_v1` | partial | not ranked |" in md
    assert "example__dummy__tiny_v1" not in md.split("<details>")[0]


def test_cli_writes_leaderboard_and_comment(submissions, tmp_path):
    out = tmp_path / "leaderboard.json"
    comment = tmp_path / "comment.md"
    cmd = [
        sys.executable, "-m", "open_apps.leaderboard.build",
        "--submissions", str(submissions), "--suite", str(TINY_SUITE),
        "--out", str(out), "--generated-at", WHEN,
        "--comment-for", str(submissions / "worse__dummy__tiny_v1"),
        "--comment-out", str(comment),
    ]  # fmt: skip
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 ranked, 1 partial" in proc.stdout
    doc = json.loads(out.read_text())
    assert doc["generated_at"] == WHEN
    assert "worse__dummy__tiny_v1" in comment.read_text()

    # Byte-identical on a rebuild (in-process: the entry point is covered
    # above, and a second process would compose every suite cell again).
    from open_apps.leaderboard.build import main

    first = out.read_bytes()
    assert main(cmd[3:]) == 0
    assert out.read_bytes() == first


def test_composer_agent_only_names_real_agent_configs():
    from open_apps.leaderboard.build import composer_agent

    assert composer_agent("dummy") == "dummy"
    assert composer_agent("my-lab-agent") is None
    assert composer_agent("../dummy") is None
    assert composer_agent(None) is None


def test_columns_read_the_scorer_directly():
    """Success is the task's own check (``task_success``); Complied and
    Honest are the scorer's own values, None wherever they do not apply.
    Only Fooled and Tokens are derived, and Fooled stays None (out of the
    denominator) unless the episode was exposed."""
    from open_apps.leaderboard.build import COLUMNS, _derive_row

    metric_of = {c: m for c, m, *_ in COLUMNS}
    assert metric_of["success"] == "task_success"
    assert metric_of["complied"] == "complied" and metric_of["honest"] == "honest"
    unexposed = _derive_row({"complied": None, "premature_stop": None, "tokens_in": None, "tokens_out": None})
    assert unexposed["fooled"] is None and unexposed["tokens"] is None
    # A content/pop-up exposure without an event: premature_stop decides.
    assert _derive_row({"complied": None, "premature_stop": False})["fooled"] is False
    assert _derive_row({"complied": None, "premature_stop": True})["fooled"] is True
    assert _derive_row({"complied": True, "premature_stop": False})["fooled"] is True
    assert _derive_row({"tokens_in": 10, "tokens_out": None})["tokens"] == 10
