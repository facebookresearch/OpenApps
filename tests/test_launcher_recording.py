"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
``record_video_dir``: where an episode's video ends up and what it is called.

BrowserGym does the recording; what OpenApps owns is turning it on and
getting the file out of ``<exp_dir>/task_video/<hash>.webm`` into a folder of
readably named recordings. That part is tested here without a browser, on a
fake experiment directory -- the end-to-end path (a real episode producing a
real video) needs Chromium and is not something CI can run.
"""

import re
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra

from open_apps.launcher import AgentLauncher, _absolute_path, freeze_recording_dir

CONFIG_DIR = str((Path(__file__).resolve().parent.parent / "config").resolve())


def launcher(tmp_path: Path, record: bool = True, **config) -> AgentLauncher:
    """An AgentLauncher with only a config -- __init__ picks ports and writes
    files, none of which the recording step touches."""
    instance = object.__new__(AgentLauncher)
    instance.config = OmegaConf.create({
        "task_name": "add_meeting_with_dennis",
        "logs_dir": str(tmp_path / "logs"),
        "agent": {"model_pretty_name": "dummy"},
        "browsergym_env_args": {"record_video": record},
        **config,
    })
    return instance


def compose_root(name: str, *overrides: str):
    if GlobalHydra.instance().is_initialized():
        GlobalHydra.instance().clear()
    with initialize_config_dir(version_base=None, config_dir=CONFIG_DIR):
        return compose(config_name=name, overrides=list(overrides))


def exp_dir_with_video(tmp_path: Path, payload: bytes = b"webm") -> Path:
    exp_dir = tmp_path / "exp"
    (exp_dir / "task_video").mkdir(parents=True)
    (exp_dir / "task_video" / "a1b2c3.webm").write_bytes(payload)
    return exp_dir


def test_off_unless_record_video(tmp_path):
    lch = launcher(tmp_path, record=False, record_video_dir=str(tmp_path / "r"))
    assert lch._save_recording(exp_dir_with_video(tmp_path), {}) is None


def test_defaults_to_the_run_logs_dir(tmp_path):
    saved = launcher(tmp_path)._save_recording(exp_dir_with_video(tmp_path), {})
    assert saved.parent == (tmp_path / "logs" / "recordings").resolve()


def test_saved_under_a_readable_name_and_original_kept(tmp_path):
    out = tmp_path / "recordings"
    exp_dir = exp_dir_with_video(tmp_path, b"episode")
    saved = launcher(tmp_path, record_video_dir=str(out))._save_recording(exp_dir, {"cum_reward": 1.0})

    assert saved.parent == out
    assert re.fullmatch(r"\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d_add_meeting_with_dennis_dummy_pass\.webm", saved.name)
    assert saved.read_bytes() == b"episode"
    # AgentLab's tooling still finds it where it expects it.
    assert (exp_dir / "task_video" / "a1b2c3.webm").exists()


def test_failed_episode_and_job_id_are_in_the_name(tmp_path):
    saved = launcher(tmp_path, record_video_dir=str(tmp_path / "r"), job_id=3)._save_recording(
        exp_dir_with_video(tmp_path), {"cum_reward": 0.0}
    )
    assert saved.name.endswith("_add_meeting_with_dennis_dummy_fail_job3.webm")


def test_model_names_are_made_filename_safe(tmp_path):
    lch = launcher(tmp_path, record_video_dir=str(tmp_path / "r"), agent={"model_name": "Qwen/Qwen3 VL"})
    saved = lch._save_recording(exp_dir_with_video(tmp_path), {})
    assert "_Qwen-Qwen3-VL_" in saved.name and saved.parent == (tmp_path / "r").resolve()


def test_an_existing_file_is_never_overwritten(tmp_path):
    out = tmp_path / "r"
    lch = launcher(tmp_path, record_video_dir=str(out))
    first = lch._save_recording(exp_dir_with_video(tmp_path, b"one"), {})
    first.unlink()
    first.write_bytes(b"someone else's")  # same name, different file
    second = lch._save_recording(tmp_path / "exp", {})
    assert first.read_bytes() == b"someone else's" and second != first


def test_same_second_collisions_get_a_suffix(tmp_path):
    lch = launcher(tmp_path, record_video_dir=str(tmp_path / "r"))
    exp_dir = exp_dir_with_video(tmp_path)
    # Three saves well inside one second: the timestamped stem repeats, and
    # each later save must get its own suffixed name instead of overwriting.
    names = {lch._save_recording(exp_dir, {}).name for _ in range(3)}
    assert len(names) == 3


def test_missing_video_is_reported_not_raised(tmp_path, capsys):
    exp_dir = tmp_path / "exp"
    exp_dir.mkdir()
    assert launcher(tmp_path, record_video_dir=str(tmp_path / "r"))._save_recording(exp_dir, {}) is None
    assert "no video was written" in capsys.readouterr().out


# --------------------------------------------------------------------------
# One destination per launch, whatever runs the jobs
# --------------------------------------------------------------------------


def test_freeze_pins_a_sweep_to_one_folder(tmp_path):
    cfg = compose_root("config_parallel_tasks", f"logs_dir={tmp_path}/sweep", "record_video=True")
    freeze_recording_dir(cfg)
    job = OmegaConf.create(OmegaConf.to_container(cfg))
    job.logs_dir = f"{tmp_path}/sweep/3"  # what run_locally does per job
    assert job.record_video_dir == str((tmp_path / "sweep" / "recordings").resolve())


def test_freeze_resolves_relative_paths_and_keeps_absolute_ones(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = compose_root("config", "logs_dir=/x", "record_video=True", "record_video_dir=videos")
    freeze_recording_dir(rel)
    assert rel.record_video_dir == str((tmp_path / "videos").resolve())
    absolute = compose_root("config", "logs_dir=/x", "record_video=True", "record_video_dir=/checkpoint/me/v")
    freeze_recording_dir(absolute)
    assert absolute.record_video_dir == "/checkpoint/me/v"


def test_freeze_is_a_noop_when_not_recording(tmp_path):
    cfg = compose_root("config", f"logs_dir={tmp_path}")
    freeze_recording_dir(cfg)
    assert OmegaConf.to_yaml(cfg).count("${logs_dir}/recordings") == 1


def test_relative_paths_resolve_from_cwd_outside_hydra(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _absolute_path("recordings") == (tmp_path / "recordings").resolve()


@pytest.mark.parametrize(
    "root", ["config", "config_parallel_tasks", "config_parallel_tasks_across_goal_variations"]
)
def test_every_root_config_records_on_one_switch(root, tmp_path):
    """And composes at all: the parallel roots had no `device`, so resolving
    their env args failed before a single sweep job ran -- and no
    `apps/chrome`, so sweeps would have run without the window chrome."""
    off = compose_root(root, f"logs_dir={tmp_path}")
    on = compose_root(root, f"logs_dir={tmp_path}", "record_video=True")
    assert OmegaConf.to_container(off.browsergym_env_args, resolve=True)["record_video"] is False
    assert OmegaConf.to_container(on.browsergym_env_args, resolve=True)["record_video"] is True
    assert on.record_video_dir == f"{tmp_path}/recordings"
    assert "chrome" in on.apps and on.apps.device.name == "desktop"
    # The launcher starts every web app with a `headless=True` override.
    assert "headless" in on
