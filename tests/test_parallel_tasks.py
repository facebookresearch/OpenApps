"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for AppVariationParallelTasksConfig task-name resolution and fan-out.

These run fully offline and fast: the expensive ``hydra.compose`` call that
``create_configs`` uses to build per-variation app configs is monkeypatched
with a lightweight stub, so no real Hydra composition happens. We only assert
on how many configs are generated and which task names they cover.
"""

import open_apps.tasks.parallel_tasks as pt_mod
import pytest
from omegaconf import OmegaConf

from open_apps import config_dir
from open_apps.tasks.parallel_tasks import (
    ALL_TASKS,
    AppVariationParallelTasksConfig,
)

_LH_PATH = config_dir() / "tasks" / "longer_horizon.yaml"


def _fake_default_config(task_names: list[str]) -> OmegaConf:
    """Minimal stand-in for the composed root config passed to create_configs."""
    return OmegaConf.create(
        {
            "tasks": {name: {"_target_": "x"} for name in task_names},
            "task_name": None,
            "apps": {},
            "app_overrides": None,
        }
    )


def _stub_compose(monkeypatch) -> None:
    """Replace hydra.compose with a stub returning a fresh trivial config.

    Fresh per call: create_configs keeps and edits each composed config, so a
    shared object would make every run the last one.
    """
    monkeypatch.setattr(
        pt_mod.hydra, "compose", lambda *a, **k: OmegaConf.create({"apps": {"stub": True}})
    )


# --------------------------------------------------------------------------
# _resolve_task_names
# --------------------------------------------------------------------------


def test_resolve_all_returns_every_task():
    names = ["task_a", "task_b", "task_c"]
    cfg = AppVariationParallelTasksConfig(app_variations=[[]], task_names=ALL_TASKS)
    assert cfg._resolve_task_names(_fake_default_config(names)) == names


def test_resolve_subset_returns_only_listed():
    cfg = AppVariationParallelTasksConfig(
        app_variations=[[]], task_names=["task_b"]
    )
    resolved = cfg._resolve_task_names(_fake_default_config(["task_a", "task_b"]))
    assert resolved == ["task_b"]


def test_resolve_unknown_string_raises():
    cfg = AppVariationParallelTasksConfig(
        app_variations=[[]], task_names="everything"
    )
    with pytest.raises(ValueError, match="task_names must be a list"):
        cfg._resolve_task_names(_fake_default_config(["task_a"]))


def test_resolve_all_matches_longer_horizon_file():
    """`all` expands to exactly the task keys defined in longer_horizon.yaml."""
    expected = list(OmegaConf.load(_LH_PATH).keys())
    cfg = AppVariationParallelTasksConfig(app_variations=[[]], task_names=ALL_TASKS)
    resolved = cfg._resolve_task_names(_fake_default_config(expected))
    assert resolved == expected
    assert len(resolved) == len(expected)


# --------------------------------------------------------------------------
# create_configs fan-out (tasks x app_variations)
# --------------------------------------------------------------------------


def test_create_configs_count_all(monkeypatch):
    _stub_compose(monkeypatch)
    task_names = ["task_a", "task_b", "task_c"]
    app_variations = [["v1"], ["v2"]]
    cfg = AppVariationParallelTasksConfig(
        app_variations=app_variations, task_names=ALL_TASKS
    )
    configs = cfg.create_configs(_fake_default_config(task_names))

    assert len(configs) == len(task_names) * len(app_variations)  # 3 x 2 = 6
    # Every task runs once per app variation.
    assert sorted(c.task_name for c in configs) == sorted(
        task_names * len(app_variations)
    )


def test_create_configs_count_subset(monkeypatch):
    _stub_compose(monkeypatch)
    all_tasks = ["task_a", "task_b", "task_c", "task_d"]
    subset = ["task_b", "task_d"]
    app_variations = [["v1"], ["v2"], ["v3"]]
    cfg = AppVariationParallelTasksConfig(
        app_variations=app_variations, task_names=subset
    )
    configs = cfg.create_configs(_fake_default_config(all_tasks))

    assert len(configs) == len(subset) * len(app_variations)  # 2 x 3 = 6
    assert {c.task_name for c in configs} == set(subset)


def test_create_configs_count_all_longer_horizon(monkeypatch):
    """`all` over the real longer_horizon set fans out to len(tasks) x variations."""
    _stub_compose(monkeypatch)
    task_names = list(OmegaConf.load(_LH_PATH).keys())
    app_variations = [["v1"], ["v2"]]
    cfg = AppVariationParallelTasksConfig(
        app_variations=app_variations, task_names=ALL_TASKS
    )
    configs = cfg.create_configs(_fake_default_config(task_names))

    assert len(configs) == len(task_names) * len(app_variations)


class TestVariationOverridesCompose:
    """The variation grids are raw Hydra override strings -- data, not code.

    A renamed config group here raises at *launch* time, not import time, so
    nothing catches it until a sweep is already holding an allocation. These
    tests compose for real (no ``_stub_compose``), which is slower but is the
    only thing that actually exercises the strings.
    """

    @staticmethod
    def _compose(overrides: list[str]) -> None:
        from hydra import compose, initialize_config_dir
        from hydra.core.global_hydra import GlobalHydra

        GlobalHydra.instance().clear()
        with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
            compose(config_name="config_parallel_tasks", overrides=overrides)

    def test_app_variations_in_config_parallel_tasks(self):
        raw = OmegaConf.load(config_dir() / "config_parallel_tasks.yaml")
        variations = OmegaConf.to_container(raw.parallel_tasks.app_variations)
        assert variations, "no app_variations to check"
        for overrides in variations:
            try:
                self._compose(list(overrides))
            except Exception as e:
                pytest.fail(f"app_variations entry {overrides} no longer composes: {e}")

    def test_save_screenshots_variation_grid(self):
        """Same guard for the visual-regression grid in save_screenshots.py."""
        from tests.save_screenshots import build_variation_overrides

        grid = build_variation_overrides()
        assert grid, "no screenshot variations to check"
        for name, overrides in grid.items():
            try:
                self._compose(list(overrides))
            except Exception as e:
                pytest.fail(f"screenshot variation {name!r} no longer composes: {e}")


class TestCommandLineOverridesReachEveryRun:
    """Each run is composed from the command line plus its variation.

    The old fan-out copied the parent and swapped in a freshly composed
    `apps`, which silently dropped any command-line override under `apps.*`.
    Composes for real, like TestVariationOverridesCompose.
    """

    CLI = [
        "apps.todo.title=From the command line",
        "browsergym_env_args.max_steps=40",
        "parallel_tasks.task_names=[navigate_to_todo,navigate_to_calendar]",
    ]
    VARIATIONS = [[], ["apps/theme=dark"]]

    @pytest.fixture(scope="class")
    def runs(self):
        from hydra import compose, initialize_config_dir
        from hydra.core.global_hydra import GlobalHydra

        GlobalHydra.instance().clear()
        with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
            parent = compose(config_name="config_parallel_tasks", overrides=self.CLI)
            # What freeze_recording_dir does before the fan-out.
            parent.record_video_dir = "/frozen/recordings"
            cfg = AppVariationParallelTasksConfig(
                app_variations=self.VARIATIONS, task_names=["navigate_to_todo", "navigate_to_calendar"]
            )
            return parent, cfg.create_configs(default_config=parent, cli_overrides=self.CLI)

    def test_one_run_per_variation_and_task(self, runs):
        _, configs = runs
        assert len(configs) == 4
        assert sorted(c.task_name for c in configs) == sorted(["navigate_to_todo", "navigate_to_calendar"] * 2)

    def test_app_override_from_the_command_line_survives(self, runs):
        _, configs = runs
        assert {c.apps.todo.title for c in configs} == {"From the command line"}

    def test_other_overrides_survive(self, runs):
        _, configs = runs
        assert {c.browsergym_env_args.max_steps for c in configs} == {40}

    def test_variation_still_applies(self, runs):
        parent, configs = runs
        dark = [c for c in configs if c.app_overrides == ["apps/theme=dark"]]
        plain = [c for c in configs if c.app_overrides == []]
        assert len(dark) == len(plain) == 2
        assert all(c.apps.theme != parent.apps.theme for c in dark)
        assert all(c.apps.theme == parent.apps.theme for c in plain)

    def test_launcher_settled_keys_come_from_the_parent(self, runs):
        parent, configs = runs
        raw_parent = OmegaConf.to_container(parent, resolve=False)
        for c in configs:
            raw = OmegaConf.to_container(c, resolve=False)
            assert raw["record_video_dir"] == "/frozen/recordings"
            # Still an interpolation, so it resolves per job on the node.
            assert raw["logs_dir"] == raw_parent["logs_dir"]


# --------------------------------------------------------------------------
# device as a cell axis
# --------------------------------------------------------------------------


class TestDeviceInCells:
    """A cell's ``device=`` override must reach the run.

    ``device`` is a root-level group, not under ``apps``: when
    ``create_configs`` swapped in only the composed ``apps`` subtree, a
    ``device=phone`` cell came out on the parent's desktop. Composing each
    run whole fixes that by construction; this pins it, since align_v1's
    phone cells depend on it -- the viewport, input model and ``apps.device``
    all interpolate ``${device}`` from the root. Composes for real.
    """

    @staticmethod
    def _configs(app_variations):
        from hydra import compose, initialize_config_dir
        from hydra.core.global_hydra import GlobalHydra

        GlobalHydra.instance().clear()
        with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
            parent = compose(config_name="config_parallel_tasks")
            cfg = AppVariationParallelTasksConfig(
                app_variations=app_variations, task_names=["navigate_to_todo"]
            )
            return cfg.create_configs(default_config=parent)

    def test_device_override_reaches_the_run(self):
        desktop, phone = self._configs([["apps/theme=default"], ["device=phone"]])
        assert desktop.device.name == "desktop"
        assert phone.device.name == "phone"
        # Everything that interpolates ${device} follows the cell.
        assert phone.apps.device.form_factor == "phone"
        assert list(phone.browsergym_env_args.task_kwargs.screen_resolution) == [
            390,
            844,
        ]
        assert phone.app_overrides == ["device=phone"]

    def test_cells_without_device_keep_the_parent_device(self):
        (only,) = self._configs([["apps/theme=dark"]])
        assert only.device.name == "desktop"

