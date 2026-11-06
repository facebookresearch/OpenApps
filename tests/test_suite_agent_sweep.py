"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

``benchmark=<suite> agents=a,b``: the native agent sweep over a frozen suite
(what scripts/benchmark_sweep.py launches for models sharing one server).
"""

import pytest
from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra

from open_apps import config_dir
from open_apps.leaderboard.suite import SuiteParallelTasksConfig
from open_apps.parallel_launcher import normalize_cli_args
from open_apps.tasks.parallel_tasks import single_run_overrides

AGENTS = ["gemma-4-31B-computer-use", "gemma-4-31B-coords"]
BLOCKS = {
    "core": {
        "task_names": ["add_call_mom_to_my_todo"],
        "app_variations": [["apps/theme=default"], ["apps/theme=dark"]],
    }
}


@pytest.fixture(scope="module")
def configs():
    cli = normalize_cli_args(
        ["benchmark=align_v1", f"agents={','.join(AGENTS)}", "agent.hostname=node-7"]
    )
    GlobalHydra.instance().clear()
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        parent = compose(config_name="config_parallel_tasks", overrides=cli)
        sweep = SuiteParallelTasksConfig(
            blocks=BLOCKS,
            seeds=[233],
            suite_version="align_v1",
            agent_configs=list(parent.parallel_tasks.agent_configs),
        )
        return sweep.create_configs(
            default_config=parent, cli_overrides=cli, config_name="config_parallel_tasks"
        )


def test_every_cell_runs_once_per_agent(configs):
    assert len(configs) == 2 * len(AGENTS)
    cells = {(c.agent_config_name, tuple(c.app_overrides)) for c in configs}
    assert len(cells) == len(configs)
    assert {c.agent_config_name for c in configs} == set(AGENTS)


def test_agent_runs_keep_the_suite_protocol_and_shared_host(configs):
    for c in configs:
        assert c.suite_version == "align_v1"
        assert c.task_termination == "on_agent_stop"
        assert c.seed == 233
        # one server for both: the command-line hostname reaches every agent
        assert c.agent.hostname == "node-7"
    pretty = {c.agent_config_name: c.agent.model_pretty_name for c in configs}
    assert pretty["gemma-4-31B-coords"] != pretty["gemma-4-31B-computer-use"]


def test_agent_runs_record_a_single_run_reproduction(configs):
    for c in configs:
        overrides = list(c.compose.overrides)
        assert not any("agent_configs" in o for o in overrides)
        assert [o for o in overrides if o.startswith("agent=")] == [
            f"agent={c.agent_config_name}"
        ]
        assert overrides[-1] == f"task_name={c.task_name}"


def test_suite_refuses_a_task_file_selection():
    with pytest.raises(ValueError, match="pins its own tasks"):
        SuiteParallelTasksConfig(blocks=BLOCKS, seeds=[1], task_configs=["todo"])
    with pytest.raises(ValueError, match="no agent configs"):
        SuiteParallelTasksConfig(blocks=BLOCKS, seeds=[1], agent_configs=[])


def test_single_run_overrides_drops_selectors_and_placeholder_agent():
    got = single_run_overrides(
        ['++parallel_tasks.agent_configs=["a", "b"]', "agent=a", "x=1", "agent=b"], "t"
    )
    assert got == ["x=1", "agent=b", "task_name=t"]
