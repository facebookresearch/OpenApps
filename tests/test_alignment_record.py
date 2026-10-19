"""Tests for ``open_apps.alignment.record`` -- the persisted episode record.

Offline: no web app, no browser. The builder is exercised against a real
BrowserGym experiment directory written with BrowserGym's own ``StepInfo``
pickling, so a change in what the loop persists breaks here rather than in a
cluster run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from open_apps.alignment.record import (
    EPISODE_FILENAME,
    RECORD_VERSION,
    STATES_FILENAME,
    EpisodeRecord,
    build_episode_record,
    infer_termination,
    json_schema,
    load_record,
    record_from_steps,
    save_record,
)

FIXTURES = Path(__file__).parent / "fixtures" / "alignment"


def _fixture_record(name: str = "aligned_conjunction") -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())["record"]


def test_round_trip_dict_and_json(tmp_path):
    raw = _fixture_record()
    record = EpisodeRecord.from_dict(raw)
    assert record.record_version == RECORD_VERSION == 1
    assert record.identity.task_class == "MarkToDoDoneTask"
    assert record.steps[1].action.startswith("send_msg_to_user")
    assert record.to_dict() == raw

    path = save_record(record, tmp_path)
    assert path == tmp_path / EPISODE_FILENAME
    assert load_record(path) == record
    # record_version leads the file so a reader can dispatch before parsing.
    assert next(iter(json.loads(path.read_text()))) == "record_version"


def test_unknown_version_is_rejected():
    raw = _fixture_record()
    raw["record_version"] = 2
    with pytest.raises(ValueError, match="record_version"):
        EpisodeRecord.from_dict(raw)


def test_schema_validates_every_fixture():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    for path in sorted(FIXTURES.glob("*.json")):
        jsonschema.validate(json.loads(path.read_text())["record"], schema)


def test_schema_rejects_bad_records():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json_schema()
    raw = _fixture_record()
    bad_version = dict(raw, record_version=2)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_version, schema)
    bad_reason = json.loads(json.dumps(raw))
    bad_reason["termination"]["reason"] = "gave_up"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_reason, schema)
    missing = {k: v for k, v in raw.items() if k != "final_state"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing, schema)


def test_schema_cli_prints_the_schema():
    out = subprocess.run(
        [sys.executable, "-m", "open_apps.alignment.record", "--schema"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    schema = json.loads(out)
    assert schema == json_schema()
    assert schema["properties"]["record_version"]["const"] == 1


# --- builder -----------------------------------------------------------------


def _step_infos(
    actions, *, chats, errors, terminated=False, truncated=False, reward=0.0
):
    """BrowserGym StepInfos as the loop leaves them: one per observation; the
    last one (the observation after the final action) carries no action."""
    from browsergym.experiments.loop import StepInfo

    infos = []
    for i, action in enumerate(list(actions) + [None]):
        info = StepInfo(step=i)
        info.obs = {
            "url": f"http://localhost:5001/page{i}",
            "chat_messages": chats[i],
            "last_action_error": errors[i],
        }
        info.action = action
        info.agent_info = {"think": f"thought {i}"} if action else {}
        info.stats = (
            {
                "step_elapsed": 0.5,
                "agent_elapsed": 1.0,
                "input_tokens": 100 + i,
                "output_tokens": 10,
            }
            if action
            else {}
        )
        info.profiling.env_start = 100.0 + i * 2
        info.profiling.env_stop = 100.5 + i * 2
        info.profiling.agent_start = 100.5 + i * 2
        info.profiling.agent_stop = 101.5 + i * 2
        infos.append(info)
    infos[-1].terminated = terminated
    infos[-1].truncated = truncated
    infos[-1].reward = reward
    return infos


GREETING = {
    "role": "assistant",
    "message": "Hi! I am your UI assistant.",
    "timestamp": 1.0,
}
GOAL = {"role": "user", "message": "Mark it done", "timestamp": 1.1}


def _identity():
    from open_apps.alignment.record import Identity

    return Identity(
        task_name="t", task_class="MarkToDoDoneTask", goal="Mark it done", max_steps=10
    )


def test_record_from_steps_shifts_errors_and_diffs_chat():
    claim = {"role": "assistant", "message": "Done!", "timestamp": 2.0}
    infos = _step_infos(
        ["click('1')", "send_msg_to_user('Done!')"],
        chats=[[GREETING, GOAL], [GREETING, GOAL], [GREETING, GOAL, claim]],
        errors=["", "TimeoutError: click", ""],
        terminated=True,
        reward=1.0,
    )
    summary = {
        "n_steps": 2,
        "cum_reward": 1.0,
        "err_msg": None,
        "terminated": True,
        "truncated": False,
    }
    record = record_from_steps(
        infos,
        summary,
        identity=_identity(),
        initial_state={"todo": []},
        final_state={"todo": [{"title": "x"}]},
        final_url="http://localhost:5001/todo",
    )
    assert [s.action for s in record.steps] == [
        "click('1')",
        "send_msg_to_user('Done!')",
        None,
    ]
    # The error raised by step 0's action is reported by step 1's observation.
    assert record.steps[0].last_action_error == "TimeoutError: click"
    assert record.steps[1].last_action_error == ""
    # Only messages new since the previous observation are kept per step.
    assert [m.message for m in record.steps[0].chat_messages] == [
        GREETING["message"],
        GOAL["message"],
    ]
    assert record.steps[1].chat_messages == []
    assert [m.message for m in record.steps[2].chat_messages] == ["Done!"]
    assert record.steps[0].think == "thought 0"
    assert record.steps[0].url == "http://localhost:5001/page0"
    assert (record.steps[0].tokens_in, record.steps[0].tokens_out) == (100, 10)
    assert record.steps[2].tokens_in is None
    assert record.n_steps == 2
    assert record.wall_s == pytest.approx(104.5 - 100.0)
    assert record.termination.reason == "claim"
    assert record.termination.final_action == "send_msg_to_user('Done!')"
    assert record.events == []


@pytest.mark.parametrize(
    "actions,summary,max_steps,expected",
    [
        (["click('1')"], {"terminated": True, "cum_reward": 1.0}, 10, "success"),
        (
            ["send_msg_to_user('ok')"],
            {"terminated": True, "cum_reward": 1.0},
            10,
            "claim",
        ),
        (
            ["send_msg_to_user('ok')"],
            {"terminated": True, "cum_reward": 0.0},
            10,
            "claim",
        ),
        (
            ["report_infeasible('no')"],
            {"terminated": True, "cum_reward": 0.0},
            10,
            "refusal",
        ),
        (["click('1')"] * 3, {"truncated": True, "cum_reward": 0.0}, 3, "max_steps"),
        # Truncated well short of the budget: the agent produced no action.
        (["click('1')"], {"truncated": True, "cum_reward": 0.0}, 10, "error"),
        (["click('1')"], {"err_msg": "boom", "cum_reward": 0.0}, 10, "error"),
    ],
)
def test_infer_termination(actions, summary, max_steps, expected):
    summary = {"n_steps": len(actions), **summary}
    reason, final_action = infer_termination(actions, summary, max_steps)
    assert reason == expected
    assert final_action == actions[-1]


def test_build_from_real_exp_dir(tmp_path):
    """Write the exp dir the way BrowserGym does, then build from it."""
    infos = _step_infos(
        ["click('1')", "click('2')"],
        chats=[[GREETING, GOAL]] * 3,
        errors=["", "", ""],
        truncated=True,
    )
    for info in infos:
        info.save_step_info(tmp_path, save_screenshot=False)
    (tmp_path / "summary_info.json").write_text(
        json.dumps(
            {
                "n_steps": 2,
                "cum_reward": 0.0,
                "err_msg": None,
                "terminated": False,
                "truncated": True,
            }
        )
    )
    states = {
        "initial_state": {"todo": [{"id": 0, "title": "a", "done": 0}]},
        "final_state": {"todo": []},
        "final_url": "http://localhost:5001/todo",
    }
    (tmp_path / STATES_FILENAME).write_text(json.dumps(states))

    from open_apps.alignment.record import Identity

    identity = Identity(
        task_name="t", task_class="DeleteToDoTask", goal="g", max_steps=2
    )
    record = build_episode_record(
        tmp_path, identity=identity, irreversible={"todo": {}}
    )
    assert record.initial_state == states["initial_state"]
    assert record.final_state == states["final_state"]
    assert record.final_url == "http://localhost:5001/todo"
    assert record.termination.reason == "max_steps"
    assert len(record.steps) == 3
    assert record.irreversible == {"todo": {}}


def test_build_uses_fallback_final_state_when_teardown_missed(tmp_path):
    infos = _step_infos(
        ["click('1')"],
        chats=[[GREETING]] * 2,
        errors=["", ""],
        terminated=True,
        reward=1.0,
    )
    for info in infos:
        info.save_step_info(tmp_path, save_screenshot=False)
    (tmp_path / "summary_info.json").write_text(
        json.dumps(
            {"n_steps": 1, "cum_reward": 1.0, "terminated": True, "truncated": False}
        )
    )
    (tmp_path / STATES_FILENAME).write_text(json.dumps({"initial_state": {"todo": []}}))

    from open_apps.alignment.record import Identity

    record = build_episode_record(
        tmp_path,
        identity=Identity(task_name="t", task_class="X", goal="g"),
        fallback_final_state={"todo": [1]},
    )
    assert record.final_state == {"todo": [1]}
    assert record.termination.reason == "success"


# --- unscorable final states ---------------------------------------------------------


def _write_exp_dir(tmp_path, states: dict) -> None:
    infos = _step_infos(["click('1')"], chats=[[GREETING, GOAL]] * 2, errors=["", ""], truncated=True)
    for info in infos:
        info.save_step_info(tmp_path, save_screenshot=False)
    (tmp_path / "summary_info.json").write_text(
        json.dumps({"n_steps": 1, "cum_reward": 0.0, "terminated": False, "truncated": True})
    )
    (tmp_path / STATES_FILENAME).write_text(json.dumps(states))


_SEEDED = {
    "todo": [{"id": 0, "title": "a", "done": 0}],
    "calendar": [{"id": 1, "title": "standup", "date": "2025-07-01"}],
    "codeeditor": {"type": "folder", "children": []},
    "online_shop": [],
}


def test_a_failed_final_probe_marks_those_apps_unscorable(tmp_path):
    final = {**_SEEDED, "todo": [], "calendar": []}
    _write_exp_dir(
        tmp_path,
        {"initial_state": _SEEDED, "final_state": final, "final_probe_failed": ["todo", "online_shop"]},
    )
    record = build_episode_record(tmp_path, identity=_identity())
    # online_shop failed too, but it had nothing to lose; calendar was emptied
    # by a probe that worked, so it is taken at its word.
    assert record.unscorable == ["todo"]


def test_a_sound_final_probe_leaves_the_record_scorable_and_the_json_unchanged(tmp_path):
    _write_exp_dir(
        tmp_path,
        {"initial_state": _SEEDED, "final_state": {**_SEEDED, "todo": []}, "final_probe_failed": []},
    )
    record = build_episode_record(tmp_path, identity=_identity())
    assert record.unscorable is None
    assert "unscorable" not in record.to_dict()


def test_unscorable_round_trips():
    from open_apps.alignment.record import EpisodeRecord

    raw = json.loads((FIXTURES / "aligned_conjunction.json").read_text())["record"]
    record = EpisodeRecord.from_dict(raw)
    record.unscorable = ["todo"]
    assert EpisodeRecord.from_dict(record.to_dict()).unscorable == ["todo"]


def test_records_without_a_probe_report_are_judged_by_the_failure_signature(tmp_path):
    from open_apps.alignment.record import unscorable_apps

    # The code editor serves an object; [] in its place is a failed probe,
    # so every app that went from content to nothing at that moment is suspect.
    dead = {"todo": [], "calendar": [], "codeeditor": [], "online_shop": []}
    assert unscorable_apps(_SEEDED, dead) == ["calendar", "codeeditor", "todo"]
    # Without the signature an emptied app is the agent's doing.
    assert unscorable_apps(_SEEDED, {**_SEEDED, "todo": []}) == []
    # The launcher's fallback probe can report too.
    _write_exp_dir(tmp_path, {"initial_state": _SEEDED})
    record = build_episode_record(
        tmp_path,
        identity=_identity(),
        fallback_final_state=lambda: ({**_SEEDED, "calendar": []}, ["calendar"]),
    )
    assert record.final_state["calendar"] == [] and record.unscorable == ["calendar"]


def test_build_reuses_a_loaded_exp_result(tmp_path, monkeypatch):
    from browsergym.experiments import loop

    _write_exp_dir(tmp_path, {"initial_state": _SEEDED, "final_state": _SEEDED})
    result = loop.ExpResult(tmp_path)
    steps = result.steps_info  # what the launcher already loaded
    monkeypatch.setattr(loop, "ExpResult", lambda *a, **k: pytest.fail("reloaded the exp dir"))
    record = build_episode_record(tmp_path, identity=_identity(), exp_result=result)
    assert len(record.steps) == len(steps)


# --- agent identity -------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent,expected",
    [
        ({"model_pretty_name": "gemma-4-31B-computer-use", "model_name": "/ckpt/x"}, "gemma-4-31B-computer-use"),
        # A local checkpoint path must not leak a filesystem layout into
        # public records and leaderboards: the basename names the model.
        ({"model_name": "/checkpoint/someone/runs/42/gemma-ft"}, "gemma-ft"),
        ({"model_name": "~/models/qwen-local/"}, "qwen-local"),
        ({"model_name": "checkpoints/run3/final"}, "final"),
        # A hub id (org/name) is a name, not a path.
        ({"model_name": "google/gemma-4-31B-it"}, "google/gemma-4-31B-it"),
        ({"agent_name": "scripted"}, "scripted"),
    ],
)
def test_agent_name_never_falls_back_to_a_filesystem_path(agent, expected):
    from omegaconf import OmegaConf

    from open_apps.alignment.record import identity_from_config
    from open_apps.tasks.tasks import AddToDoTask

    config = OmegaConf.create({"task_name": None, "agent": agent})
    task = AddToDoTask(goal="g", todo_name="x", is_done=False)
    assert identity_from_config(config, task).agent_name == expected
