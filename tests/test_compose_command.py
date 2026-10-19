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
    tasks_option = selection.choices.get("tasks", ["all_tasks"])[0]
    for key, labels in case["labels"].items():
        values = selection.choices[key]
        if key == "task_name":
            expected = [catalog.task(tasks_option, v).label for v in values]
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
        ({"choices": {"agent": ["dummy", "claude_4_sonnet"]}}, "pool"),
        ({"count": 3}, "pool"),
        ({"max_parallel": 2}, "pool"),
        ({"choices": {"mode": ["slurm_cluster"]}, "count": 3}, "slurm_pool"),
        ({"choices": {"mode": ["slurm_cluster"]}}, "slurm_pool"),
        (
            {"choices": {"mode": ["slurm_cluster"], "apps/theme": ["dark", "mono"]}},
            "sweep",
        ),
        ({"target": "sweep"}, "sweep"),
    ],
)
def test_resolve_target(catalog, selection, target):
    assert resolve_target(Selection.from_dict(selection), catalog) == target


def test_the_worker_pool_pill_follows_the_mode_row(catalog):
    """There is no separate SLURM-pool pill: Worker pool + Mode=slurm_cluster
    is the SLURM pool, so forcing ``pool`` never fights the Mode row."""
    forced = Selection.from_dict(
        {
            "target": "pool",
            "choices": {
                "mode": ["slurm_cluster"],
                "agent": ["gemma-4-2B-computer-use"],
            },
        }
    )
    assert resolve_target(forced, catalog) == "slurm_pool"
    assert (
        render(forced, catalog)
        == "AGENTS=gemma-4-2B-computer-use sbatch scripts/conduct_slurm.sh"
    )
    # Auto-selection lands there anyway, so the forced target is redundant.
    assert normalize(forced, catalog).target is None


def test_a_forced_slurm_pool_normalizes_to_the_pool_pill(catalog):
    """Old links (``#target=slurm_pool``) keep working, spelled the new way."""
    sel = Selection.from_dict({"target": "slurm_pool", "choices": {"seed": [1, 2]}})
    with pytest.raises(CommandError):
        render(sel, catalog)  # a pool still cannot vary the seed
    old = Selection.from_dict({"target": "slurm_pool"})
    assert normalize(old, catalog) == Selection(
        choices={"mode": ["slurm_cluster"], "agent": ["default"]}
    )


def test_a_pool_agent_survives_the_switch_to_slurm(catalog):
    """The UI holds the normalized selection. If the pool dropped agent=dummy
    as conduct.sh's default, Mode=Slurm would then render no AGENTS and run
    conduct_slurm.sh's own default -- an agent nobody picked."""
    pool = normalize(
        Selection.from_dict({"target": "pool", "choices": {"agent": ["dummy"]}}),
        catalog,
    )
    assert pool.choices == {"agent": ["dummy"]}
    on_slurm = replace(pool, choices={**pool.choices, "mode": ["slurm_cluster"]})
    assert render(on_slurm, catalog) == "AGENTS=dummy sbatch scripts/conduct_slurm.sh"


@pytest.mark.parametrize("target", ["pool", "slurm_pool"])
def test_worker_pools_always_spell_out_agents(catalog, target):
    """Whatever is (or is not) chosen, the agent on screen is in AGENTS."""
    sel = normalize(Selection(target=target), catalog)
    shown = sel.choices["agent"]
    assert render(sel, catalog).startswith(f"AGENTS={shown[0]} ")


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
        "target": "pool",
        "choices": {"agent": ["dummy"], "seed": [3]},
        "count": 2,
        "max_parallel": 1,
        "extra": ["a.b=c"],
        "env": ["X=1"],
        "sbatch_args": ["--qos=q"],
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
        ({"choices": {"agent": ["dummy", "claude_4_sonnet"]}, "count": 6}, 6),
        ({"choices": {"mode": ["slurm_cluster"]}}, 1),
        ({"choices": {"mode": ["slurm_cluster"], "agent": ["a", "b"]}}, 2),
        ({"choices": {"mode": ["slurm_cluster"], "agent": ["a", "b"]}, "count": 1}, 1),
    ],
)
def test_run_count(catalog, selection, runs):
    assert run_count(Selection.from_dict(selection), catalog) == runs


def test_run_count_expands_all_tasks(catalog):
    sel = Selection.from_dict(
        {"choices": {"tasks": ["longer_horizon"], "task_name": ["all"]}}
    )
    assert run_count(sel, catalog) == len(catalog.tasks["longer_horizon"])


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


def _compose_ok(config_name: str, overrides: list[str]) -> None:
    fast = [] if any("apps/onlineshop/content" in o for o in overrides) else _FAST
    compose(config_name=config_name, overrides=[*overrides, *fast])


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_rendered_overrides_compose(catalog, case):
    selection = Selection.from_dict(case["selection"])
    script, overrides = _overrides_after_script(render(selection, catalog))
    agents = selection.choices.get("agent") or [None]
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
        else:
            # What conduct.sh (and conduct_slurm.sh before it) put in front of
            # the forwarded overrides for each run.
            if script.endswith("conduct_slurm.sh"):
                overrides = ["agent.hostname=node", "use_wandb=True", *overrides]
            for agent in agents:
                injected = ["browsergym_env_args.headless=True", "wandb.group=g"]
                if agent:
                    injected = [f"agent={agent}", *injected, f"wandb.job_type={agent}"]
                _compose_ok("config", [*injected, *overrides])


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


@pytest.mark.parametrize("target", ["single", "pool"])
def test_a_benchmark_runs_only_as_a_sweep(bench, target):
    with pytest.raises(CommandError, match="benchmark runs only as a sweep"):
        render(replace(SUITE, target=target), bench)


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
