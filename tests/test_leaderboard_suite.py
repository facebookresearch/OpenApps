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
    monkeypatch.setattr(suite_mod, "default_heldout_path", lambda: tmp_path / "absent.yaml")


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
    assert align_v1.benchmark.seed_set == "public"
    sweep = OmegaConf.to_container(align_v1.parallel_tasks, resolve=True)
    assert sweep["_target_"].endswith("SuiteParallelTasksConfig")
    assert sweep["task_names"] is None and sweep["app_variations"] is None


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
    assert any(o.startswith("apps/theme=") and o != "apps/theme=default" for o in overrides)
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
    assert by_block == {"core": 90, "phrasing": 27, "events": 20, "ambiguity": 20}
    assert len(cells) == 157


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
    assert {c.suite_version for c in configs} == {"align_v1"}


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


def test_heldout_reads_the_internal_file(monkeypatch, tmp_path):
    monkeypatch.delenv(HELDOUT_ENV, raising=False)
    internal = tmp_path / "internal-heldout_seeds.yaml"
    internal.write_text("seeds: [7]\n")
    monkeypatch.setattr(suite_mod, "default_heldout_path", lambda: internal)
    assert load_heldout_seeds() == [7]


def test_example_seeds_are_placeholders_and_internal_file_is_ignored():
    example = config_dir() / "benchmark" / "heldout_seeds.example.yaml"
    assert OmegaConf.load(example).seeds == [1, 2]
    rel = "config/benchmark/internal-heldout_seeds.yaml"
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", rel], cwd=config_dir().parent, check=False
    ).returncode
    assert ignored == 0, f"{rel} must be gitignored"


def test_unknown_seed_set_is_refused():
    with pytest.raises(ValueError, match="seed_set"):
        suite_cells(load_suite("align_v1"), seed_set="private")
