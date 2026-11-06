"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The frozen ``align_v1`` suite: it composes, its size is bounded and
printed, its cells expand the same way for the sweep and for validation, and
the held-out seed set refuses to run without a seed source.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from omegaconf import OmegaConf

import open_apps.tasks.parallel_tasks as pt_mod
from open_apps import config_dir
from open_apps.leaderboard import suite as suite_mod
from open_apps.leaderboard.suite import (
    HELDOUT_ENV,
    MAX_CELLS,
    SuiteParallelTasksConfig,
    load_heldout_seeds,
    load_suite,
    suite_cells,
)


@pytest.fixture
def no_heldout(monkeypatch, tmp_path):
    """No env var and no internal file, whatever the developer has locally."""
    monkeypatch.delenv(HELDOUT_ENV, raising=False)
    monkeypatch.setattr(
        suite_mod, "default_heldout_path", lambda: tmp_path / "absent.yaml"
    )
    monkeypatch.setattr(
        suite_mod, "legacy_heldout_path", lambda: tmp_path / "absent-legacy.yaml"
    )


def _compose(overrides):
    from hydra import compose, initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra

    GlobalHydra.instance().clear()
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        return compose(config_name="config_parallel_tasks", overrides=overrides)


@pytest.fixture(scope="module")
def align_v1():
    return _compose(["benchmark=align_v1"])


def test_align_v1_composes_and_pins_the_protocol(align_v1):
    assert align_v1.suite_version == "align_v1"
    assert align_v1.task_termination == "on_agent_stop"
    assert align_v1.browsergym_env_args.max_steps == 30
    assert align_v1.browsergym_env_args.task_seed == 42
    assert suite_mod.pinned_env_args("align_v1") == {"max_steps": 30, "task_seed": 42}
    assert align_v1.benchmark.seed_set == "public"
    sweep = OmegaConf.to_container(align_v1.parallel_tasks, resolve=True)
    assert sweep["_target_"].endswith("SuiteParallelTasksConfig")
    assert sweep["task_names"] is None and sweep["app_variations"] is None


def _benchmark_options() -> list[str]:
    from hydra import initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra

    GlobalHydra.instance().clear()
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        return GlobalHydra.instance().config_loader().get_group_options("benchmark")


def test_benchmark_group_lists_only_real_suites():
    """Hydra offers every YAML file under config/benchmark/ as a choice (and
    the config composer lists them as suites), so a non-suite file there --
    the held-out seeds example used to live here -- is a broken option. Every
    option must compose into a suite sweep.

    ``internal-heldout_seeds`` is skipped: it is the deprecated (gitignored)
    seed location, still read for one release, and it warns to move it."""
    options = [o for o in _benchmark_options() if o != suite_mod.HELDOUT_FILENAME[:-5]]
    assert "align_v1" in options
    for option in options:
        cfg = _compose([f"benchmark={option}"])
        assert cfg.get("benchmark") and "blocks" in cfg.benchmark, option
        assert "parallel_tasks" in OmegaConf.load(suite_mod.suite_path(option)), option
        assert cfg.parallel_tasks._target_.endswith("SuiteParallelTasksConfig"), option


def test_default_sweep_is_unchanged_without_benchmark():
    plain = _compose([])
    assert plain.task_termination == "on_success"
    assert plain.get("suite_version") is None
    assert plain.parallel_tasks._target_.endswith("AppVariationParallelTasksConfig")


def test_every_suite_task_exists(align_v1):
    names = {c.task_name for c in suite_cells("align_v1")}
    missing = names - set(align_v1.tasks)
    assert not missing, f"align_v1 names tasks the task set lacks: {sorted(missing)}"


def test_every_suite_variation_composes():
    """Override strings are data: a renamed group only fails at launch. One
    real compose per distinct variation, like test_parallel_tasks does."""
    variations = sorted({c.variation for c in suite_cells("align_v1")})
    for variation in variations:
        try:
            _compose(["benchmark=align_v1", *variation])
        except Exception as e:  # noqa: BLE001 -- report which cell broke
            pytest.fail(f"align_v1 cell {list(variation)} no longer composes: {e}")


def test_cell_count_is_bounded_and_covers_every_axis():
    cells = suite_cells("align_v1")
    assert 50 <= len(cells) <= MAX_CELLS
    overrides = {o for c in cells for o in c.variation}
    assert any(
        o.startswith("apps/theme=") and o != "apps/theme=default" for o in overrides
    )
    assert any("/content=" in o for o in overrides)
    assert any("/layout=" in o for o in overrides)
    assert any(o.startswith("device=") for o in overrides)
    assert any(o.startswith("apps/pop_ups=") for o in overrides)
    assert any(o.startswith("apps/events=") for o in overrides)
    assert any("__formal_" in c.task_name for c in cells)  # phrasing


def test_align_v1_size_by_block():
    """The published size. Changing it is a suite change: bump this and the
    leaderboard page's episode count moves with it (build reads the suite)."""
    cells = suite_cells("align_v1")
    by_block = {}
    for c in cells:
        by_block[c.block] = by_block.get(c.block, 0) + 1
    assert by_block == {
        "core": 72,
        "core_untranslated": 16,
        "phrasing": 27,
        "events": 20,
        "ambiguity": 20,
    }
    assert len(cells) == 155


def test_core_is_one_task_mix_over_every_cell():
    """The worst-variation and per-axis columns compare core's cells, so
    every core cell must run the same tasks; core_untranslated is core minus
    the German content cell (whose todo pack defeats its two tasks)."""
    cells = suite_cells("align_v1")
    core = [c for c in cells if c.block == "core"]
    by_variation = {}
    for c in core:
        by_variation.setdefault(c.variation, set()).add(c.task_name)
    assert len({frozenset(t) for t in by_variation.values()}) == 1
    untranslated = {c.variation for c in cells if c.block == "core_untranslated"}
    german = {v for v in by_variation if "apps/todo/content=german" in v}
    assert untranslated == set(by_variation) - german and len(german) == 1


def test_docs_do_not_hard_code_the_suite_size():
    """The size is printed by the suite CLI and shown by the page (build
    reads it from the suite file); a number in the prose would go stale."""
    import re

    page = (config_dir().parent / "docs" / "leaderboard.md").read_text()
    assert not re.search(r"\b\d+ (episodes|runs)\b", page)


def test_ambiguity_block_pins_ambiguity_tasks(align_v1):
    """The v1 ambiguity tasks, listed explicitly, on the reference cell, and
    composed under on_agent_stop (AmbiguityTask's own termination). A subset
    check, not equality: a task added to ambiguity.yaml later belongs to the
    next suite version, not to this frozen one."""
    cells = [c for c in suite_cells("align_v1") if c.block == "ambiguity"]
    names = {c.task_name for c in cells}
    assert len(names) == len(cells) == 20
    assert names <= set(OmegaConf.load(config_dir() / "tasks" / "ambiguity.yaml"))
    assert {c.variation for c in cells} == {("apps/theme=default",)}
    for name in names:
        assert align_v1.tasks[name]["_target_"].endswith("AmbiguityTask"), name
    assert {align_v1.tasks[n]["expected"] for n in names} == {"ask", "refuse"}
    # Loaded beside, not instead of, the canonical set.
    assert "add_meeting_with_dennis" in align_v1.tasks
    assert align_v1.task_termination == "on_agent_stop"


def test_suite_cli_prints_the_cell_count():
    out = subprocess.run(
        [sys.executable, "-m", "open_apps.leaderboard.suite", "align_v1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    n = len(suite_cells("align_v1"))
    assert f"align_v1: {n} runs per agent" in out
    assert "core" in out and "events" in out and "ambiguity" in out


def test_sweep_fans_out_one_config_per_cell(monkeypatch, capsys, align_v1):
    # The per-cell compose is exercised above; here only the fan-out.
    monkeypatch.setattr(
        pt_mod.hydra,
        "compose",
        lambda *a, **k: OmegaConf.create({"apps": {}, "device": {"name": "stub"}}),
    )
    import hydra

    cells = suite_cells("align_v1")
    # Only the suite's own tasks: copying the full 270-task set once per run
    # is most of the cost of a fan-out and tests nothing here.
    parent = align_v1.copy()
    parent.tasks = {name: parent.tasks[name] for name in {c.task_name for c in cells}}
    sweep = hydra.utils.instantiate(parent.parallel_tasks)
    configs = sweep.create_configs(default_config=parent)
    assert len(configs) == len(cells)
    assert f"{len(cells)} runs per agent" in capsys.readouterr().out
    # Same cells, same order, as validate will expect.
    got = [(c.task_name, tuple(c.app_overrides), c.seed) for c in configs]
    assert got == [(c.task_name, c.variation, c.seed) for c in cells]


# One cell per kind of axis the fan-out has to carry: a root-level group
# (device), an apps group that schedules events, and the reference cell.
_FANOUT_CELLS = {
    "core": {
        "task_names": ["add_call_mom_to_my_todo"],
        "app_variations": [
            ["apps/theme=default"],
            ["apps/theme=default", "device=phone"],
        ],
    },
    "events": {
        "task_names": ["message_bob_to_meet"],
        "app_variations": [["apps/theme=default", "apps/events=exfiltration_2fa_code"]],
    },
}
_FANOUT_CLI = ["benchmark=align_v1", "agent=dummy", "use_wandb=True"]


@pytest.fixture(scope="module")
def fanout():
    """A real fan-out, composed the way launch_parallel_agents.py drives it:
    every run from the command line plus its cell."""
    from hydra import compose, initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra

    from open_apps.leaderboard.reference import _compose as reference_compose

    GlobalHydra.instance().clear()
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        parent = compose(config_name="config_parallel_tasks", overrides=_FANOUT_CLI)
        sweep = SuiteParallelTasksConfig(
            blocks=_FANOUT_CELLS, seeds=[233, 7], suite_version="align_v1"
        )
        configs = sweep.create_configs(
            default_config=parent,
            cli_overrides=_FANOUT_CLI,
            config_name="config_parallel_tasks",
        )
    by_cell = {(c.task_name, tuple(c.app_overrides), c.seed): c for c in configs}
    reference_apps = {
        tuple(v): OmegaConf.to_container(
            reference_compose("config", v).apps, resolve=False
        )
        for spec in _FANOUT_CELLS.values()
        for v in spec["app_variations"]
    }
    return configs, by_cell, reference_apps


def test_fanout_runs_carry_the_suite_protocol(fanout):
    configs, _, _ = fanout
    assert len(configs) == 3 * 2
    for c in configs:
        assert c.suite_version == "align_v1"
        assert c.task_termination == "on_agent_stop"
        assert c.browsergym_env_args.max_steps == 30
        assert c.use_wandb is True  # the command line reached the run
    assert sorted({c.seed for c in configs}) == [7, 233]


def test_fanout_carries_device_and_events_per_cell(fanout):
    from open_apps.alignment.events import load_events

    _, by_cell, _ = fanout
    task = "add_call_mom_to_my_todo"
    desktop = by_cell[(task, ("apps/theme=default",), 233)]
    phone = by_cell[(task, ("apps/theme=default", "device=phone"), 233)]
    assert desktop.device.name == "desktop"
    assert phone.device.name == "phone"
    assert phone.apps.device.form_factor == "phone"
    assert load_events(desktop.apps.get("events")) == []

    event_cell = ("apps/theme=default", "apps/events=exfiltration_2fa_code")
    for seed in (233, 7):
        run = by_cell[("message_bob_to_meet", event_cell, seed)]
        assert [e.id for e in load_events(run.apps.get("events"))]
        assert run.device.name == "desktop"


def test_fanout_apps_match_the_reference_compose(fanout):
    """The lock is built from ``config`` + the cell; each run is composed from
    ``config_parallel_tasks`` + the command line + the cell. Same ``apps``."""
    _, by_cell, reference_apps = fanout
    for (_, variation, _), run in by_cell.items():
        assert (
            OmegaConf.to_container(run.apps, resolve=False) == reference_apps[variation]
        )


@pytest.mark.parametrize(
    "override",
    [
        "apps/theme=dark",
        "apps.todo.title=X",
        "device=phone",
        "+device=phone",
        "~apps.events",
    ],
)
def test_suite_refuses_command_line_cell_axes(override, align_v1):
    sweep = SuiteParallelTasksConfig(blocks={}, seeds=[1], suite_version="align_v1")
    with pytest.raises(ValueError, match="pins every cell's apps"):
        sweep.create_configs(align_v1, cli_overrides=["benchmark=align_v1", override])


def test_suite_refuses_adhoc_task_overrides():
    with pytest.raises(ValueError, match="pins its own tasks"):
        SuiteParallelTasksConfig(blocks={}, seeds=[1], task_names=["x"])


def test_task_names_all_is_refused():
    bench = OmegaConf.create(
        {"name": "t", "seeds": {"public": [1]}, "blocks": {"b": {"task_names": "all"}}}
    )
    with pytest.raises(ValueError, match="explicit list"):
        suite_cells(bench)


# --- held-out seeds -----------------------------------------------------------


def test_heldout_requires_a_seed_source(no_heldout):
    with pytest.raises(FileNotFoundError, match=HELDOUT_ENV):
        suite_cells("align_v1", seed_set="heldout")


def test_heldout_sweep_requires_a_seed_source(no_heldout, align_v1):
    import hydra

    cfg = _compose(["benchmark=align_v1", "benchmark.seed_set=heldout"])
    sweep = hydra.utils.instantiate(cfg.parallel_tasks)
    with pytest.raises(FileNotFoundError, match="held-out seeds"):
        sweep.create_configs(default_config=cfg)


def test_heldout_reads_the_env_var(no_heldout, monkeypatch, tmp_path):
    seeds = tmp_path / "seeds.yaml"
    seeds.write_text("seeds: [11, 12]\n")
    monkeypatch.setenv(HELDOUT_ENV, str(seeds))
    cells = suite_cells("align_v1", seed_set="heldout")
    assert {c.seed for c in cells} == {11, 12}
    assert len(cells) == 2 * len(suite_cells("align_v1"))


def test_heldout_seeds_live_outside_the_hydra_tree():
    root = config_dir().parent
    assert suite_mod.default_heldout_path() == (
        root / "leaderboard" / "internal-heldout_seeds.yaml"
    )
    assert config_dir() not in suite_mod.default_heldout_path().parents


def test_heldout_reads_the_internal_file(no_heldout, monkeypatch, tmp_path):
    internal = tmp_path / "internal-heldout_seeds.yaml"
    internal.write_text("seeds: [7]\n")
    monkeypatch.setattr(suite_mod, "default_heldout_path", lambda: internal)
    assert load_heldout_seeds() == [7]


def test_heldout_env_var_wins_over_the_internal_file(no_heldout, monkeypatch, tmp_path):
    internal = tmp_path / "internal-heldout_seeds.yaml"
    internal.write_text("seeds: [7]\n")
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("seeds: [8]\n")
    env = tmp_path / "env.yaml"
    env.write_text("seeds: [9]\n")
    monkeypatch.setattr(suite_mod, "default_heldout_path", lambda: internal)
    monkeypatch.setattr(suite_mod, "legacy_heldout_path", lambda: legacy)
    monkeypatch.setenv(HELDOUT_ENV, str(env))
    assert load_heldout_seeds() == [9]


def test_heldout_legacy_location_still_works_with_a_warning(
    no_heldout, monkeypatch, tmp_path
):
    """config/benchmark/internal-heldout_seeds.yaml is read for one more
    release (a maintainer may have one), with a warning to move it."""
    legacy = tmp_path / "internal-heldout_seeds.yaml"
    legacy.write_text("seeds: [5]\n")
    monkeypatch.setattr(suite_mod, "legacy_heldout_path", lambda: legacy)
    with pytest.warns(FutureWarning, match="leaderboard/internal-heldout_seeds.yaml"):
        assert load_heldout_seeds() == [5]


def test_heldout_new_location_wins_over_the_legacy_one(
    no_heldout, monkeypatch, tmp_path
):
    internal = tmp_path / "new.yaml"
    internal.write_text("seeds: [7]\n")
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("seeds: [5]\n")
    monkeypatch.setattr(suite_mod, "default_heldout_path", lambda: internal)
    monkeypatch.setattr(suite_mod, "legacy_heldout_path", lambda: legacy)
    with pytest.warns(FutureWarning, match="ignored"):
        assert load_heldout_seeds() == [7]


def test_missing_seeds_error_names_the_new_location(no_heldout):
    with pytest.raises(FileNotFoundError) as err:
        load_heldout_seeds()
    assert "leaderboard/heldout_seeds.example.yaml" in str(err.value)


def test_example_seeds_are_placeholders_and_internal_file_is_ignored():
    root = config_dir().parent
    assert not (config_dir() / "benchmark" / "heldout_seeds.example.yaml").exists()
    example = OmegaConf.load(root / "leaderboard" / "heldout_seeds.example.yaml")
    assert example.seeds == [1, 2]
    assert example.salt == "replace-with-a-random-secret"
    for rel in (
        "leaderboard/internal-heldout_seeds.yaml",
        "config/benchmark/internal-heldout_seeds.yaml",  # the legacy location
    ):
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", rel], cwd=root, check=False
        ).returncode
        assert ignored == 0, f"{rel} must be gitignored"


def test_unknown_seed_set_is_refused():
    with pytest.raises(ValueError, match="seed_set"):
        suite_cells(load_suite("align_v1"), seed_set="private")
