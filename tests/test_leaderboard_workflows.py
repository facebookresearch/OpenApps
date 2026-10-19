"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The leaderboard CI's trust model, as structure a refactor cannot quietly
undo: a PR's bundles are judged by the base branch's code, the job that
writes to the PR never checks out or runs PR code, and the comment it posts
is inert text. See the header of .github/workflows/leaderboard.yml.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"
CHECK = WORKFLOWS / "leaderboard.yml"
COMMENT = WORKFLOWS / "leaderboard-comment.yml"
STATIC = WORKFLOWS / "static.yml"


def _load(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    # PyYAML reads the bare key `on` as the boolean True.
    doc["on"] = doc.pop(True, doc.get("on"))
    return doc


@pytest.fixture(scope="module")
def check():
    return _load(CHECK)


@pytest.fixture(scope="module")
def comment():
    return _load(COMMENT)


def _uses(step: dict, action: str) -> bool:
    return str(step.get("uses", "")).split("@")[0] == action


def _checkouts(job: dict) -> list[dict]:
    return [s for s in job["steps"] if _uses(s, "actions/checkout")]


def _write_scopes(perms) -> list[str]:
    if perms in ("write-all",):
        return ["*"]
    return [k for k, v in (perms or {}).items() if v == "write"]


def test_check_runs_on_pull_request_never_pull_request_target(check):
    assert set(check["on"]) == {"pull_request"}
    assert "pull_request_target" not in CHECK.read_text()


def test_check_never_has_write_permissions(check):
    assert _write_scopes(check.get("permissions")) == []
    for name, job in check["jobs"].items():
        assert _write_scopes(job.get("permissions")) == [], name


def test_validate_checks_out_code_from_the_base_ref(check):
    job = check["jobs"]["validate"]
    code, data = _checkouts(job)
    assert code["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert "repository" not in code["with"]  # the base repo
    assert code["with"]["path"] == "base"
    for step in (code, data):
        assert step["with"]["persist-credentials"] is False


def test_validate_checks_out_only_the_prs_leaderboard_data(check):
    _, data = _checkouts(check["jobs"]["validate"])
    w = data["with"]
    assert w["ref"] == "${{ github.event.pull_request.head.sha }}"
    assert w["path"] == "pr"
    # Non-cone mode: exactly these patterns, not the top-level files cone
    # mode would add (pyproject.toml, launch scripts...).
    assert w["sparse-checkout-cone-mode"] is False
    assert w["sparse-checkout"].split() == [
        "/leaderboard/submissions/",
        "/leaderboard/demonstrations/",
    ]


def test_validate_runs_code_only_from_the_base_checkout(check):
    for step in check["jobs"]["validate"]["steps"]:
        run = step.get("run", "")
        if re.search(r"\b(uv|python3?|pip)\b", run):
            assert step.get("working-directory") == "base", step["name"]
        # Never run anything that lives in the PR checkout.
        assert not re.search(r"(^|\s)(\./)?pr/\S+\.(py|sh)\b", run), step["name"]
        assert "cd pr" not in run, step["name"]


def test_validate_publishes_summary_and_artifact(check, comment):
    steps = check["jobs"]["validate"]["steps"]
    assert any("GITHUB_STEP_SUMMARY" in s.get("run", "") for s in steps)
    [upload] = [s for s in steps if _uses(s, "actions/upload-artifact")]
    [download] = [
        s
        for s in comment["jobs"]["comment"]["steps"]
        if _uses(s, "actions/download-artifact")
    ]
    assert upload["with"]["name"] == download["with"]["name"] == "leaderboard-comment"


def test_code_changes_with_submissions_fail_with_a_clear_message(check):
    job = check["jobs"]["separate-code"]
    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert "split submissions from code changes" in run
    assert "exit 1" in run
    classify = "\n".join(s.get("run", "") for s in check["jobs"]["scope"]["steps"])
    for path in (
        "src/open_apps/(leaderboard|alignment)/",
        "config/benchmark/",
        r"\.github/workflows/leaderboard",
    ):
        assert path in classify
    for path in (
        "src/open_apps/leaderboard/**",
        "src/open_apps/alignment/**",
        "config/benchmark/**",
    ):
        assert path in check["on"]["pull_request"]["paths"]


def test_verifications_travel_alone_and_are_code_owned(check):
    """Verified status lives in leaderboard/verifications.yaml: a PR touching
    it must touch nothing else, and CODEOWNERS makes a maintainer approve it.
    The old label/base-branch diff is gone (a PR can edit this workflow, so
    it never enforced anything)."""
    scope = check["jobs"]["scope"]
    classify = "\n".join(s.get("run", "") for s in scope["steps"])
    assert "grep -qxF 'leaderboard/verifications.yaml' changed.txt" in classify
    assert (
        scope["outputs"]["verifications"]
        == "${{ steps.classify.outputs.verifications }}"
    )
    assert scope["outputs"]["others"] == "${{ steps.classify.outputs.others }}"
    job = check["jobs"]["verifications-only"]
    assert job["needs"] == "scope" and job.get("permissions") == {}
    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert '[ "$VERIFICATIONS" = true ] && [ "${OTHERS:-0}" != 0 ]' in run
    assert "exit 1" in run

    text = CHECK.read_text()
    for gone in (
        "verified-by-maintainer",
        "--base-dir",
        "--maintainer-approved",
        "labeled",
    ):
        assert gone not in text, gone
    owners = (WORKFLOWS.parent / "CODEOWNERS").read_text().splitlines()
    rules = [
        line.split() for line in owners if line.strip() and not line.startswith("#")
    ]
    assert [
        "/leaderboard/verifications.yaml",
        "@facebookresearch/openapps-maintainers",
    ] in rules


def test_header_says_pr_ci_is_advisory():
    header = CHECK.read_text().split("name: Leaderboard")[0]
    assert "ADVISORY" in header
    assert "static.yml" in header and "verifications.yaml" in header
    assert "pull_request_target" not in CHECK.read_text()


def test_comment_workflow_follows_the_check_by_name(check, comment):
    assert comment["on"] == {
        "workflow_run": {"workflows": [check["name"]], "types": ["completed"]}
    }


def test_comment_workflow_never_checks_out_or_runs_pr_code(comment):
    assert comment.get("permissions") == {}
    [job] = comment["jobs"].values()
    assert _write_scopes(job["permissions"]) == ["pull-requests"]
    for step in job["steps"]:
        assert not _uses(step, "actions/checkout"), step
        run = step.get("run", "")
        assert "uv " not in run and "open_apps" not in run and "pip " not in run
        assert "git " not in run


def _comment_script() -> str:
    run = next(
        s["run"]
        for s in _load(COMMENT)["jobs"]["comment"]["steps"]
        if "<<'PY'" in s.get("run", "")
    )
    return run.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


@pytest.fixture(scope="module")
def fenced():
    namespace: dict = {"__name__": "leaderboard_comment"}
    exec(compile(_comment_script(), str(COMMENT), "exec"), namespace)  # noqa: S102 -- our own workflow
    return namespace["fenced"]


def test_comment_body_is_one_inert_block(fenced):
    hostile = "ok\n```\n@maintainers <img src=x onerror=alert(1)> [x](javascript:alert(1))\n````more"
    out = fenced(hostile)
    fence = out.split("text\n", 1)[0]
    assert set(fence) == {"`"} and len(fence) == 5  # longer than the longest run (4)
    assert out.endswith("\n" + fence)
    # The fence opens once and closes once: nothing inside can terminate it.
    assert out.count(fence) == 2


def test_comment_body_is_capped(fenced):
    out = fenced("x" * 100_000, limit=1000)
    assert len(out) < 1200 and "truncated" in out


def test_comment_body_drops_control_characters(fenced):
    assert "\x1b" not in fenced("a\x1b[31mred\x1b[0m​")


def test_docs_build_generates_leaderboard_json():
    """Generated on deploy from main's code, never copied from a commit."""
    run = "\n".join(s.get("run", "") for s in _load(STATIC)["jobs"]["deploy"]["steps"])
    assert (
        "open_apps.leaderboard.build --lenient --out docs/assets/leaderboard/leaderboard.json"
        in run
    )
    assert "cp leaderboard/leaderboard.json" not in run
    gitignore = (Path(__file__).parents[1] / ".gitignore").read_text().splitlines()
    assert "docs/assets/leaderboard/leaderboard.json" in gitignore


def _validate_steps(check):
    return {s["name"]: s for s in check["jobs"]["validate"]["steps"]}


def test_untrusted_output_is_bracketed_by_stop_commands(check):
    """validate/build print record-derived text into the log: workflow
    commands are switched off around it with a per-run random token."""
    steps = _validate_steps(check)
    for name in ("Validate the changed bundles", "Build the would-be leaderboard"):
        run = steps[name]["run"]
        assert 'echo "::stop-commands::$token"' in run, name
        assert 'echo "::$token::"' in run, name
        assert "secrets.token_hex" in run or "openssl rand" in run, name


def _fenced_source(text: str) -> str:
    start = text.index("def fenced(")
    end = text.index("\n\n", start)
    return "\n".join(line.strip() for line in text[start:end].splitlines())


def test_job_summary_uses_the_comment_workflows_fence(check):
    summary = _validate_steps(check)["Write the summary and the comment artifact"][
        "run"
    ]
    comment = _comment_script()
    assert _fenced_source(summary) == _fenced_source(comment)
    assert "GITHUB_STEP_SUMMARY" in summary and "fenced(" in summary
    # No hand-rolled ``` fence around untrusted output any more.
    assert "echo '```'" not in summary
