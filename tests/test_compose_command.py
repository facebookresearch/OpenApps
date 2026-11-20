"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for rendering and parsing launch commands (``open_apps.compose.command``).

The cases live in ``tests/fixtures/compose/command_cases.json`` so the
docs-site JavaScript port can be held to exactly the same expectations. The
Hydra check at the bottom is what keeps the fixtures honest: every override a
rendered command passes must actually compose against the real config tree.
"""

import json
import shlex
from dataclasses import replace
from pathlib import Path

import pytest
from hydra import compose

from open_apps.compose.catalog import build_catalog, hydra_context
from open_apps.compose.command import (
    CommandError,
    Selection,
    normalize,
    parse,
    render,
    resolve_target,
    run_count,
)

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "compose" / "command_cases.json").read_text()
)
CASES = FIXTURES["cases"]
PARSE_CASES = FIXTURES["parse_cases"]
ERRORS = FIXTURES["errors"]


@pytest.fixture(scope="module")
def catalog():
    return build_catalog()


def _ids(cases):
    return [c["name"] for c in cases]


# --------------------------------------------------------------------------
# Fixture round trips
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_render(catalog, case):
    assert render(Selection.from_dict(case["selection"]), catalog) == case["command"]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_parse(catalog, case):
    assert parse(case["command"], catalog) == Selection.from_dict(case["selection"])


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_parse_render_round_trip(catalog, case):
    selection = Selection.from_dict(case["selection"])
    assert parse(render(selection, catalog), catalog) == selection


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_fixture_selections_are_normalized(catalog, case):
    """Fixtures hold the canonical form, so the JS port can compare directly."""
    selection = Selection.from_dict(case["selection"])
    assert normalize(selection, catalog) == selection
    assert selection.to_dict() == case["selection"]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_fixture_labels_match_the_catalog(catalog, case):
    selection = Selection.from_dict(case["selection"])
    entries = {
        t.name: t
        for t in catalog.tasks_in(selection.choices.get("tasks", ["all_tasks"]))
    }
    for key, labels in case["labels"].items():
        values = selection.choices[key]
        if key == "task_name":
            expected = [entries[v].label for v in values]
        else:
            expected = [catalog.groups[key].option(v).label for v in values]
        assert labels == expected, key


@pytest.mark.parametrize("case", PARSE_CASES, ids=_ids(PARSE_CASES))
def test_parse_hand_written_commands(catalog, case):
    selection = parse(case["command"], catalog)
    assert selection == Selection.from_dict(case["selection"])
    assert render(selection, catalog) == case["rendered"]


@pytest.mark.parametrize("case", PARSE_CASES, ids=_ids(PARSE_CASES))
def test_render_parse_keeps_every_token(catalog, case):
    """Nothing typed is dropped: every token of the input survives a round
    trip, unless it was a default the target applies anyway."""
    rendered = shlex.split(render(parse(case["command"], catalog), catalog))
    selection = parse(case["command"], catalog)
    for token in selection.extra:
        assert token in rendered


@pytest.mark.parametrize("case", ERRORS, ids=_ids(ERRORS))
def test_inexpressible_selections_raise(catalog, case):
    with pytest.raises(CommandError, match=case["match"]):
        render(Selection.from_dict(case["selection"]), catalog)


# --------------------------------------------------------------------------
# Target selection and normalization
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "selection,target",
    [
        ({}, "single"),
        ({"choices": {"apps/theme": ["dark", "mono"]}}, "sweep"),
        ({"choices": {"task_name": ["a", "b"]}}, "sweep"),
        ({"choices": {"task_name": ["all"]}}, "sweep"),
        ({"choices": {"agent": ["dummy", "claude_4_sonnet"]}}, "sweep"),
        ({"repeats": 3}, "sweep"),
        ({"max_parallel": 2}, "sweep"),
        ({"choices": {"mode": ["slurm_cluster"]}, "repeats": 3}, "sweep"),
        # launch_agent.py runs in this process; SLURM is the sweep launcher's.
        ({"choices": {"mode": ["slurm_cluster"]}}, "sweep"),
        (
            {"choices": {"mode": ["slurm_cluster"], "apps/theme": ["dark", "mono"]}},
            "sweep",
        ),
        ({"target": "sweep"}, "sweep"),
        # Combined task groups are one inventory, not a sweep axis.
        ({"choices": {"tasks": ["all_tasks", "openbanking"]}}, "single"),
        (
            {"choices": {"tasks": ["all_tasks", "openbanking"], "agent": ["a", "b"]}},
            "sweep",
        ),
    ],
)
def test_resolve_target(catalog, selection, target):
    assert resolve_target(Selection.from_dict(selection), catalog) == target


def test_there_is_one_interface_for_parallel_runs(catalog):
    """Single run and sweep: the worker pools are not targets any more."""
    assert set(catalog.targets) == {"single", "sweep"}
    for target in catalog.targets.values():
        assert "conduct" not in target.command


@pytest.mark.parametrize(
    "legacy,upgraded",
    [
        # Worker pool -> the sweep; its count over two agents -> 3 repeats each.
        (
            {"target": "pool", "choices": {"agent": ["dummy", "claude_4_sonnet"]}, "count": 6},
            {"choices": {"agent": ["dummy", "claude_4_sonnet"]}, "repeats": 3},
        ),
        # A count that does not divide still covers every run it asked for.
        (
            {"target": "pool", "choices": {"agent": ["dummy", "claude_4_sonnet"]}, "count": 5},
            {"choices": {"agent": ["dummy", "claude_4_sonnet"]}, "repeats": 3},
        ),
        # SLURM pool -> the sweep on SLURM; sbatch options have nowhere to go.
        (
            {
                "target": "slurm_pool",
                "choices": {"agent": ["gemma-4-2B-computer-use"]},
                "sbatch_args": ["--qos=q"],
                "max_parallel": 4,
            },
            {"choices": {"mode": ["slurm_cluster"], "agent": ["gemma-4-2B-computer-use"]}},
        ),
        # A forced pool with one agent and nothing else is a sweep of one job.
        ({"target": "pool", "choices": {"agent": ["claude_4_sonnet"]}},
         {"target": "sweep", "choices": {"agent": ["claude_4_sonnet"]}}),
    ],
)
def test_old_worker_pool_links_load_as_the_sweep(catalog, legacy, upgraded):
    sel = Selection.from_dict(legacy)
    if "slurm_cluster" in sel.choices.get("mode", []):
        sel = replace(sel, max_parallel=None)  # the UI drops it on SLURM
    assert normalize(sel, catalog).to_dict() == upgraded
    render(sel, catalog)  # and it renders


def test_max_parallel_is_refused_on_slurm(catalog):
    sel = Selection.from_dict({"choices": {"mode": ["slurm_cluster"]}, "max_parallel": 4})
    with pytest.raises(CommandError, match="max_parallel"):
        render(sel, catalog)


@pytest.mark.parametrize("script", ["./scripts/conduct.sh", "sbatch scripts/conduct_slurm.sh"])
def test_a_worker_pool_command_points_at_the_sweep(catalog, script):
    with pytest.raises(CommandError, match="launch_parallel_agents.py"):
        parse(f"AGENTS=dummy {script}", catalog)


def test_unknown_target_is_an_error(catalog):
    with pytest.raises(CommandError, match="unknown target"):
        render(Selection(target="cluster"), catalog)


def test_normalize_drops_defaults_of_the_resolved_target(catalog):
    sel = Selection.from_dict({"choices": {"agent": ["default"], "seed": [233]}})
    assert normalize(sel, catalog) == Selection()
    # In a sweep the agent default is config_parallel_tasks' `dummy` instead.
    sweep = Selection.from_dict({"target": "sweep", "choices": {"agent": ["dummy"]}})
    assert normalize(sweep, catalog).choices == {}
    sweep_default = Selection.from_dict(
        {"target": "sweep", "choices": {"agent": ["default"]}}
    )
    assert normalize(sweep_default, catalog).choices == {"agent": ["default"]}


def test_sweep_app_defaults_come_from_config_yaml(catalog):
    """create_configs composes each cell's apps against config.yaml, where
    apps/theme is null -- not config_parallel_tasks' `default`. So in a sweep
    `apps/theme=default` is a real choice and must not be normalized away."""
    sel = Selection.from_dict(
        {"target": "sweep", "choices": {"apps/theme": ["default"]}}
    )
    assert normalize(sel, catalog).choices == {"apps/theme": ["default"]}


def test_selection_json_round_trip():
    data = {
        "target": "sweep",
        "choices": {"agent": ["dummy"], "seed": [3]},
        "repeats": 2,
        "max_parallel": 1,
        "extra": ["a.b=c"],
        "env": ["X=1"],
    }
    assert Selection.from_dict(data).to_dict() == data
    assert Selection().to_dict() == {}


@pytest.mark.parametrize(
    "selection,runs",
    [
        ({}, 1),
        (
            {"choices": {"apps/theme": ["dark", "mono"], "task_name": ["a", "b", "c"]}},
            6,
        ),
        ({"choices": {"agent": ["dummy", "claude_4_sonnet"]}}, 2),
        ({"choices": {"agent": ["dummy", "claude_4_sonnet"]}, "repeats": 3}, 6),
        (
            {
                "choices": {
                    "agent": ["dummy", "claude_4_sonnet"],
                    "apps/theme": ["dark", "mono"],
                    "task_name": ["a", "b", "c"],
                },
                "repeats": 2,
            },
            24,  # 2 themes x 3 tasks x 2 agents x 2 repeats
        ),
        ({"choices": {"mode": ["slurm_cluster"]}}, 1),
        ({"choices": {"mode": ["slurm_cluster"], "agent": ["a", "b"]}}, 2),
    ],
)
def test_run_count(catalog, selection, runs):
    assert run_count(Selection.from_dict(selection), catalog) == runs


def test_run_count_expands_all_tasks(catalog):
    sel = Selection.from_dict(
        {"choices": {"tasks": ["longer_horizon"], "task_name": ["all"]}}
    )
    assert run_count(sel, catalog) == len(catalog.tasks["longer_horizon"])


def test_run_count_expands_all_tasks_of_combined_groups(catalog):
    sel = Selection.from_dict(
        {"choices": {"tasks": ["all_tasks", "openbanking"], "task_name": ["all"]}}
    )
    expected = len(catalog.tasks["all_tasks"]) + len(catalog.tasks["openbanking"])
    assert run_count(sel, catalog) == expected


# --------------------------------------------------------------------------
# Combined task groups: one quoted word, on every target
# --------------------------------------------------------------------------

COMBINED = "tasks=[all_tasks,openbanking]"


@pytest.mark.parametrize(
    "selection",
    [
        {"choices": {"tasks": ["all_tasks", "openbanking"]}},
        {"choices": {"tasks": ["openbanking", "all_tasks"]}},  # catalog order wins
        {"choices": {"tasks": ["all_tasks", "openbanking"], "task_name": ["all"]}},
        {
            "choices": {
                "tasks": ["all_tasks", "openbanking"],
                "apps/theme": ["dark", "mono"],
            }
        },
        {"target": "sweep", "choices": {"tasks": ["all_tasks", "openbanking"]}},
        {
            "choices": {
                "tasks": ["all_tasks", "openbanking"],
                "agent": ["dummy", "claude_4_sonnet"],
            }
        },
        {
            "choices": {
                "tasks": ["all_tasks", "openbanking"],
                "mode": ["slurm_cluster"],
                "agent": ["dummy"],
            }
        },
    ],
    ids=json.dumps,
)
def test_combined_task_groups_render_as_one_word(catalog, selection):
    sel = Selection.from_dict(selection)
    command = render(sel, catalog)
    assert f"'{COMBINED}'" in command
    assert COMBINED in shlex.split(command)
    assert parse(command, catalog) == normalize(sel, catalog)
    assert render(parse(command, catalog), catalog) == command


def test_combined_task_groups_are_not_an_error_with_a_forced_single_run(catalog):
    sel = Selection(target="single", choices={"tasks": ["all_tasks", "openbanking"]})
    assert render(sel, catalog) == f"uv run launch_agent.py '{COMBINED}'"


# --------------------------------------------------------------------------
# Every rendered command composes
# --------------------------------------------------------------------------

# Every full composition of config.yaml loads the shop's 999-product content
# pack (~3 s). Appending the chrome-only pack keeps this check fast; it is
# only skipped for a selection that picks a shop content pack itself.
_FAST = ["apps/onlineshop/content=default"]


def _overrides_after_script(command: str) -> tuple[str, list[str]]:
    tokens = shlex.split(command)
    while "=" in tokens[0] and not tokens[0].startswith("-"):
        tokens = tokens[1:]  # leading VAR=value assignments
    for i, token in enumerate(tokens):
        if token.endswith((".py", ".sh")):
            return token, tokens[i + 1 :]
    raise AssertionError(command)


def _launcher_overrides(script: str, overrides: list[str]) -> list[str]:
    """What Hydra composes for ``overrides``: the sweep launcher first turns
    its ``agents=a,b`` selector into ``agent=a`` plus
    ``parallel_tasks.agent_configs`` (Hydra has no ``agents`` key)."""
    if script == "launch_parallel_agents.py":
        from open_apps.parallel_launcher import normalize_cli_args

        return normalize_cli_args(overrides)
    return overrides


def _compose_ok(config_name: str, overrides: list[str]) -> None:
    fast = [] if any("apps/onlineshop/content" in o for o in overrides) else _FAST
    compose(config_name=config_name, overrides=[*overrides, *fast])


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_rendered_overrides_compose(catalog, case):
    selection = Selection.from_dict(case["selection"])
    script, overrides = _overrides_after_script(render(selection, catalog))
    overrides = _launcher_overrides(script, overrides)
    with hydra_context():
        if script == "launch_agent.py":
            _compose_ok("config", overrides)
        elif script == "launch_parallel_agents.py":
            # The sweep config itself, then what create_configs does per cell.
            cfg = compose(
                config_name="config_parallel_tasks", overrides=[*overrides, *_FAST]
            )
            names = cfg.parallel_tasks.task_names
            first = next(iter(cfg.tasks)) if names == "all" else names[0]
            for cell in cfg.parallel_tasks.app_variations:
                _compose_ok("config", [*cell, f"task_name={first}"])
            # Every agent of an agents=a,b sweep composes too.
            for agent in cfg.parallel_tasks.get("agent_configs") or []:
                _compose_ok("config", [f"agent={agent}"])
        else:
            raise AssertionError(f"unexpected script {script}")


COMBINED_CASES = [
    c for c in CASES if len(c["selection"].get("choices", {}).get("tasks", [])) > 1
]


@pytest.mark.parametrize("case", COMBINED_CASES, ids=_ids(COMBINED_CASES))
def test_combined_commands_compose_the_union(catalog, case):
    """`tasks=[a,b]` composes exactly the union of the groups' task maps, and
    the selected task is in it -- on whichever primary config the target
    composes."""
    selection = Selection.from_dict(case["selection"])
    script, overrides = _overrides_after_script(render(selection, catalog))
    overrides = _launcher_overrides(script, overrides)
    config_name = (
        "config_parallel_tasks" if script == "launch_parallel_agents.py" else "config"
    )
    union = {t.name for t in catalog.tasks_in(selection.choices["tasks"])}
    with hydra_context():
        cfg = compose(config_name=config_name, overrides=[*overrides, *_FAST])
    configs = cfg.get("parallel_tasks", {}).get("task_configs")
    if configs:
        # With agents=a,b the launcher reads tasks=[a,b] as task files run
        # side by side, and parallel_tasks.task_names picks across them.
        assert list(configs) == selection.choices["tasks"]
        with hydra_context():
            union_cfg = compose(
                config_name="config", overrides=[f"tasks=[{','.join(configs)}]", *_FAST]
            )
        cfg = union_cfg
    assert set(cfg.tasks) == union
    for name in selection.choices.get("task_name", []):
        assert name == "all" or name in cfg.tasks


def test_the_env_name_pattern_has_one_home():
    """``NAME=value`` is recognised by one pattern (command.py) everywhere in
    Python -- parse, the server's env check and its argv split -- and the JS
    port spells the same one, so they cannot drift apart."""
    from open_apps.compose import command, server

    assert server.ENV_ASSIGNMENT is command.ENV_ASSIGNMENT
    package = Path(command.__file__).parent
    for path in package.glob("*.py"):
        if path.name != "command.py":
            assert command.ENV_NAME not in path.read_text(), path.name
    js = (package.parents[2] / "docs/assets/composer/composer.js").read_text()
    assert f"const ENV_ASSIGNMENT = /^({command.ENV_NAME})=([\\s\\S]*)$/;" in js


# --------------------------------------------------------------------------
# A frozen suite (the leaderboard's `benchmark` group; temp config tree)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bench(benchmark_config_dir):
    return build_catalog(benchmark_config_dir)


SUITE = Selection(choices={"benchmark": ["align_v1"], "agent": ["claude_4_sonnet"]})
SUITE_COMMAND = (
    "uv run launch_parallel_agents.py benchmark=align_v1 agent=claude_4_sonnet"
)


def test_a_benchmark_renders_as_its_sweep(bench):
    """The suite pins parallel_tasks, so nothing about cells or task names is
    spelled out (SuiteParallelTasksConfig refuses them)."""
    assert render(SUITE, bench) == SUITE_COMMAND
    assert resolve_target(SUITE, bench) == "sweep"
    slurm = replace(SUITE, choices={**SUITE.choices, "mode": ["slurm_cluster"]})
    assert render(slurm, bench) == (
        "uv run launch_parallel_agents.py benchmark=align_v1 mode=slurm_cluster "
        "agent=claude_4_sonnet"
    )


def test_a_benchmark_command_parses_back(bench):
    assert parse(SUITE_COMMAND, bench) == normalize(SUITE, bench)
    assert render(parse(SUITE_COMMAND, bench), bench) == SUITE_COMMAND


def test_a_benchmark_runs_only_as_a_sweep(bench):
    with pytest.raises(CommandError, match="benchmark runs only as a sweep"):
        render(replace(SUITE, target="single"), bench)


def test_a_benchmark_on_another_script_stays_an_extra(bench):
    sel = parse("uv run launch_agent.py benchmark=align_v1", bench)
    assert sel.extra == ["benchmark=align_v1"]
    assert "benchmark" not in sel.choices


@pytest.mark.parametrize(
    "choices",
    [
        {"apps/theme": ["dark"]},
        {"apps/theme": ["dark", "mono"]},
        {"task_name": ["navigate_to_todo"]},
        {"task_name": ["all"]},
    ],
)
def test_a_benchmark_refuses_app_and_task_axes(bench, choices):
    sel = replace(SUITE, choices={**SUITE.choices, **choices})
    with pytest.raises(CommandError, match="frozen suite pins"):
        render(sel, bench)


def test_a_benchmark_run_count_is_the_suites(bench):
    assert run_count(SUITE, bench) is None  # set by the suite, not the selection
