"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Fixtures shared by the leaderboard tests: a six-cell suite and a dummy
agent's sweep over it, laid out on disk the way ``launch_parallel_agents``
leaves it.

The episodes are the alignment scorer's golden records
(``tests/fixtures/alignment``) re-labelled for the tiny suite -- real app
states and real task configs, so the bundle round-trips through the actual
scorer rather than a stub. One job is left without ``episode.json`` (just
BrowserGym's pickles plus the task's ``openapps_states.json``) so ``pack``
has to rebuild it.

``tests/fixtures/leaderboard/make_fixtures.py`` packs this tree into the
committed example bundle.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

HERE = Path(__file__).parent
ALIGNMENT_FIXTURES = HERE / "fixtures" / "alignment"
LEADERBOARD_FIXTURES = HERE / "fixtures" / "leaderboard"
TINY_SUITE = LEADERBOARD_FIXTURES / "tiny_v1.yaml"
EXAMPLE_BUNDLE = LEADERBOARD_FIXTURES / "example__dummy__tiny_v1"
EXAMPLE_DEMO = LEADERBOARD_FIXTURES / "demonstrations" / "example__todo-basics"
SUITE_NAME = "tiny_v1"
FIXED_DATE = "2026-10-16"

DUMMY_AGENT = {
    "_target_": "open_apps.agent.dummy_agent.DummyAgentArgs",
    "model_name": "dummy",
    "model_pretty_name": "dummy",
    "use_html": True,
    "use_axtree": True,
    "use_screenshot": True,
    "save_som": False,
    # Redacted by pack: a hostname is a cluster node name in real configs.
    "hostname": "no host name for dumb dumbs",
    "api_key": "sk-not-a-real-key",
    "max_tokens": 5000,
    "client_type": "dummy",
}

# (job, golden fixture, task, variation). The golden record supplies states,
# steps and termination; the cell labels come from here.
JOBS = [
    (0, "claim_and_fail_is_dishonest", "add_call_mom_to_my_todo", ["apps/theme=default"]),
    (1, "refusal_and_fail_is_honest", "add_call_mom_to_my_todo", ["apps/theme=dark"]),
    (2, "goal_required_delete_not_harm", "delete_call_aunt_lisa", ["apps/theme=default"]),
    (3, "goal_required_delete_not_harm", "delete_call_aunt_lisa", ["apps/theme=dark"]),
    # Ambiguity cells: two golden todo trajectories replayed under real
    # refuse tasks from config/tasks/ambiguity.yaml (neither touches the
    # messenger, so the untouched state is the goal's). A refusal asks;
    # scrolling to max_steps never does -- task_success without success.
    (4, "refusal_and_fail_is_honest", "ambiguity_refuse_share_password", ["apps/theme=default"]),
    (5, "timeout_excluded_from_honesty", "ambiguity_refuse_pose_as_alice", ["apps/theme=default"]),
]
REBUILT_JOB = 3
AMBIGUITY_TASKS = HERE.parent / "config" / "tasks" / "ambiguity.yaml"


def golden(name: str) -> dict:
    return json.loads((ALIGNMENT_FIXTURES / f"{name}.json").read_text())["record"]


def _relabel_as_ambiguity_task(ident: dict, task: str) -> None:
    """Point a golden record's identity at an ambiguity task (states, steps
    and termination stay), with its real config so re-scoring rebuilds the
    real ``AmbiguityTask``."""
    config = yaml.safe_load(AMBIGUITY_TASKS.read_text())[task]
    ident.update(
        task_name=task,
        task_class=config["_target_"].rsplit(".", 1)[-1],
        goal=config["goal"],
        goal_style=None,
        task_config=config,
    )


def tiny_record(name: str, task: str, variation: list[str], seed: int = 233) -> dict:
    """A golden record relabelled as one cell of the tiny suite."""
    record = copy.deepcopy(golden(name))
    ident = record["identity"]
    if task.startswith("ambiguity_"):
        _relabel_as_ambiguity_task(ident, task)
    assert ident["task_name"] == task, (name, task)
    ident.update(
        variation=list(variation),
        seed=seed,
        agent_name="dummy",
        suite_version=SUITE_NAME,
        task_termination="on_agent_stop",
    )
    return record


def _run_config(task: str, variation: list[str], task_config: dict) -> dict:
    """The subset of a saved job config.yaml that pack reads."""
    return {
        "task_name": task,
        "tasks": {task: task_config},
        "agent": DUMMY_AGENT,
        "apps": {},
        "app_overrides": list(variation),
        "seed": 233,
        "suite_version": SUITE_NAME,
        "task_termination": "on_agent_stop",
        "browsergym_env_args": {"max_steps": 10, "task_seed": 7},
    }


def _write_browsergym_exp_dir(exp_dir: Path, record: dict) -> None:
    """What BrowserGym + OpenAppsTask leave when the launcher's own scoring
    step never ran: step pickles, summary_info.json, openapps_states.json."""
    from open_apps.alignment.record import STATES_FILENAME
    from tests.test_alignment_record import GOAL, GREETING, _step_infos

    actions = [s["action"] for s in record["steps"] if s["action"] is not None]
    reason = record["termination"]["reason"]
    infos = _step_infos(
        actions,
        chats=[[GREETING, GOAL]] * (len(actions) + 1),
        errors=[""] * (len(actions) + 1),
        terminated=reason in ("success", "claim", "refusal"),
        truncated=reason == "max_steps",
        reward=record["cum_reward"],
    )
    for info in infos:
        info.save_step_info(exp_dir, save_screenshot=False)
    (exp_dir / "summary_info.json").write_text(
        json.dumps(
            {
                "n_steps": len(actions),
                "cum_reward": record["cum_reward"],
                "err_msg": None,
                "terminated": reason in ("success", "claim", "refusal"),
                "truncated": reason == "max_steps",
            }
        )
    )
    (exp_dir / STATES_FILENAME).write_text(
        json.dumps(
            {
                "initial_state": record["initial_state"],
                "final_state": record["final_state"],
                "final_url": record["final_url"],
                "task_termination": "on_agent_stop",
                "termination_reason": reason,
                "snapshots": [],
                "events": [],
            }
        )
    )


def write_logs_tree(root: Path, jobs=JOBS, rebuilt_job: int | None = REBUILT_JOB) -> Path:
    """A local-mode sweep: ``root/<job>/config.yaml`` + one exp_dir each."""
    from open_apps.alignment.record import EPISODE_FILENAME

    for job, name, task, variation in jobs:
        record = tiny_record(name, task, variation)
        job_dir = root / str(job)
        exp_dir = job_dir / f"2026-10-16_12-00-0{job}_dummy_on_{task}_{job}"
        exp_dir.mkdir(parents=True)
        (job_dir / "config.yaml").write_text(
            yaml.safe_dump(_run_config(task, variation, record["identity"]["task_config"]))
        )
        if job == rebuilt_job:
            _write_browsergym_exp_dir(exp_dir, record)
        else:
            (exp_dir / EPISODE_FILENAME).write_text(json.dumps(record, indent=2))
    return root


def make_example_bundle(out: Path, logs_root: Path) -> Path:
    from open_apps.leaderboard.pack import pack

    write_logs_tree(logs_root)
    result = pack([logs_root], org="example", agent="dummy", out=out, date=FIXED_DATE)
    assert result.n_episodes == len(JOBS), result
    return result.out


# (golden fixture, task) for the example demonstration: a person refusing an
# impossible add, and a person doing a goal-required delete.
DEMO_EPISODES = [
    ("refusal_and_fail_is_honest", "add_call_mom_to_my_todo"),
    ("goal_required_delete_not_harm", "delete_call_aunt_lisa"),
]


def make_example_demonstration(out: Path) -> Path:
    """``<contributor>__<topic>/``: meta.yaml + episodes.jsonl.gz, written the
    way a contributor would by hand (no pack, no suite, no scores)."""
    from open_apps.alignment.record import RECORD_VERSION
    from open_apps.leaderboard import bundle as B

    lines = []
    for name, task in DEMO_EPISODES:
        record = copy.deepcopy(golden(name))
        ident = record["identity"]
        assert ident["task_name"] == task, (name, task)
        ident.update(agent_name="human", suite_version=None, agent_config_hash=None)
        lines.append(record)
    out.mkdir(parents=True, exist_ok=True)
    n = B.write_episodes(lines, out / B.EPISODES_FILE)
    meta = {
        "kind": "demonstration",
        "meta_version": 1,
        "contributor": "example",
        "topic": "todo-basics",
        "agent": "human",
        "date": FIXED_DATE,
        "contact": None,
        "description": "A refusal of an impossible add and a goal-required delete, by hand.",
        "n_episodes": n,
        "record_version": RECORD_VERSION,
        "videos": [],
    }
    B.write_yaml(meta, out / B.META_FILE)
    return out
