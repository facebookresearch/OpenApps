"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

``open_apps.leaderboard.details``: what a model's page shows. Totals and
breakdowns must add up to the episodes, the config must be redacted like a
bundle's agent.yaml, and the preview server must serve an episode's
screenshots and recording -- and nothing else.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from open_apps.alignment.record import EpisodeRecord
from open_apps.leaderboard import details as D
from open_apps.leaderboard import preview as P
from open_apps.leaderboard.build import ranked_row
from open_apps.leaderboard.validate import validate_bundle
from tests._leaderboard_helpers import (
    EXAMPLE_BUNDLE,
    JOBS,
    SUITE_NAME,
    TINY_SUITE,
    write_logs_tree,
)

WHEN = "2026-10-16T00:00:00Z"


@pytest.fixture(scope="module")
def bundle_row():
    return ranked_row(validate_bundle(EXAMPLE_BUNDLE, TINY_SUITE), n_boot=0)


@pytest.fixture
def logs(tmp_path):
    return write_logs_tree(tmp_path / "log_outputs" / "2026-10-16_12-00-00-me")


def _record(**identity) -> EpisodeRecord:
    base = {
        "task_name": "t",
        "task_class": "AddToDoTask",
        "goal": "g",
        "variation": [],
    }
    return EpisodeRecord.from_dict(
        {
            "record_version": 1,
            "identity": {**base, **identity},
            "initial_state": {},
            "final_state": {},
            "termination": {"reason": "claim"},
        }
    )


def test_totals_and_breakdowns_add_up(bundle_row):
    d = bundle_row["details"]
    episodes, t = d["episodes"], d["totals"]
    assert t["tasks"] == len(episodes) == bundle_row["n_episodes"]
    assert t["successes"] + t["mistakes"] == t["scored"]
    assert t["successes"] / t["scored"] == pytest.approx(bundle_row["success"]["rate"])
    assert t["steps"] == sum(e["steps"] for e in episodes)
    assert t["steps_mean"] == pytest.approx(bundle_row["steps"]["rate"])
    assert t["tokens_in"] == sum(e["tokens_in"] or 0 for e in episodes)
    for dim in D.DIMENSIONS:
        assert sum(b["tasks"] for b in d["breakdowns"][dim]) == t["tasks"], dim
        assert sum(b["mistakes"] for b in d["breakdowns"][dim]) == t["mistakes"], dim
    themes = {b["value"] for b in d["breakdowns"]["theme"]}
    assert themes == {"default", "dark"}
    (device,) = d["breakdowns"]["device"]
    assert device["value"] == "desktop" and device["viewport"] == [1920, 1080]


def test_episodes_carry_their_outcome_and_log(bundle_row):
    episodes = bundle_row["details"]["episodes"]
    deleted = [e for e in episodes if e["task"] == "delete_call_aunt_lisa"]
    assert deleted and all(e["success"] for e in deleted)
    assert all(
        e["changes"] == ["todo · items · removed · Call Aunt Lisa"] for e in deleted
    )
    assert all(e["app"] == "todo" for e in deleted)
    for e in episodes:
        assert e["trace"], e["task"]
        assert e["termination"] in {"success", "claim", "refusal", "max_steps", "error"}
        assert e["media"] == {}  # a bundle has no screenshots


def test_the_bundle_config_is_its_redacted_agent_yaml(bundle_row):
    (agent,) = bundle_row["details"]["config"]
    assert agent["name"] == "Agent"
    items = dict(agent["items"])
    assert items["client_type"] == "dummy"
    assert items["api_key"] == items["hostname"] == "<redacted>"


def test_a_run_config_is_redacted_and_sectioned():
    run = {
        "agent": {
            "api_key": "sk-live-abcdef123456",
            "hostname": "learnfair1234",
            "model_name": "/checkpoint/someone/ckpts/gemma-step1200",
            "temperature": 0.2,
            "prompt_txt": {"system_prompt": "see http://learnfair1234:8000/v1"},
        },
        "browsergym_env_args": {"max_steps": 30, "device": {"name": "phone"}},
        "device": {"name": "phone", "viewport": [390, 844]},
        "task_termination": "on_agent_stop",
        "logs_dir": "/checkpoint/someone/logs",
    }
    sections = {s["name"]: dict(s["items"]) for s in D.config_sections(run_config=run)}
    assert list(sections) == ["Agent", "Environment", "Device", "Run"]
    agent = sections["Agent"]
    assert agent["api_key"] == agent["hostname"] == "<redacted>"
    assert agent["model_name"] == "gemma-step1200"
    assert agent["prompt_txt.system_prompt"] == "see <redacted>"
    assert agent["temperature"] == 0.2
    # The device has its own section; where a run was written is not shown.
    assert "device.name" not in sections["Environment"]
    assert sections["Device"]["viewport"] == "390, 844"
    blob = json.dumps(sections)
    assert "learnfair1234" not in blob and "/checkpoint" not in blob


@pytest.mark.parametrize(
    "task_class,config,expected",
    [
        ("AddEventTask", {}, "calendar"),
        ("EditFileTask", {}, "code_editor"),
        ("NavigateToAppTask", {"_target_": "x.NavigateToAppTask", "target_app": "map"}, "maps"),
        (
            "AmbiguityTask",
            {"_target_": "x.AmbiguityTask", "clarified": {"_target_": "x.SendMessageTask"}},
            "messenger",
        ),
        (
            "CompositeTask",
            {"_target_": "x.CompositeTask", "subtasks": [
                {"_target_": "x.AddToDoTask"}, {"_target_": "x.AddEventTask"}]},
            "multi-app",
        ),
        ("AnswerTask", {"_target_": "x.AnswerTask"}, "other"),
    ],
)  # fmt: skip
def test_task_app(task_class, config, expected):
    assert D.task_app(_record(task_class=task_class, task_config=config)) == expected


def test_sweep_command_drops_the_cell():
    record = _record(
        command=(
            "uv run launch_agent.py --config-name=config_parallel_tasks benchmark=align_v1 "
            "agent=X apps/theme=dark device=phone task_name=add_call_mom"
        ),
        variation=["apps/theme=dark", "device=phone"],
    )
    assert D.sweep_command(record) == (
        "uv run launch_parallel_agents.py benchmark=align_v1 agent=X"
    )
    # A single run is not a sweep.
    assert (
        D.sweep_command(_record(command="uv run launch_agent.py task_name=t")) is None
    )
    assert D.sweep_command(_record()) is None


def test_preview_rows_have_details_and_their_config(logs):
    (row,) = P.preview([logs.parent], n_boot=0, when=WHEN, suite=TINY_SUITE).doc[
        "suites"
    ][SUITE_NAME]["rows"]
    d = row["details"]
    assert d["totals"]["tasks"] == len(JOBS)
    agent = dict(d["config"][0]["items"])
    assert d["config"][0]["name"] == "Agent"
    assert agent.get("hostname") in (None, "<redacted>")
    assert agent.get("api_key") in (None, "<redacted>")


def _with_media(logs):
    exp_dir = next(p.parent for p in sorted(logs.rglob("episode.json")))
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    (exp_dir / "screenshot_step_0.png").write_bytes(png)
    (exp_dir / "screenshot_step_3.png").write_bytes(png)
    (exp_dir / "task_video").mkdir()
    (exp_dir / "task_video" / "abc.webm").write_bytes(b"webm")
    (exp_dir / "secret.txt").write_text("nope")
    return exp_dir


def test_the_served_page_serves_media_and_nothing_else(logs):
    exp_dir = _with_media(logs)
    result = P.preview([logs.parent], n_boot=0, when=WHEN, suite=TINY_SUITE)
    (row,) = result.doc["suites"][SUITE_NAME]["rows"]
    (ep,) = [e for e in row["details"]["episodes"] if e["media"]]
    assert ep["media"] == {"screenshots": [0, 3], "video": True}
    assert result.media[(row["id"], ep["id"])] == exp_dir

    page = P.Page(result.doc, 0, media=result.media)
    assert 'media-base="/media/"' in page.html(toolbar=True)
    assert "media-base" not in page.html()  # a saved file has no server
    server = P.make_server(page, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/media/{row['id']}/{ep['id']}"

    def get(url):
        try:
            with urllib.request.urlopen(url) as r:
                return r.status, r.headers["Content-Type"]
        except urllib.error.HTTPError as e:
            return e.code, None

    try:
        assert get(f"{base}/screenshot/3.png") == (200, "image/png")
        assert get(f"{base}/video.webm") == (200, "video/webm")
        for bad in (
            f"{base}/screenshot/9.png",
            f"{base}/secret.txt",
            f"{base}/screenshot/..%2F..%2Fsecret.txt",
            f"{base}/../secret.txt",
            f"http://127.0.0.1:{server.server_port}/media/nope/0/video.webm",
        ):
            assert get(bad)[0] == 404, bad
    finally:
        server.shutdown()
        server.server_close()
