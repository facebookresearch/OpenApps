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

from omegaconf import OmegaConf

from open_apps.launcher import AgentLauncher, _absolute_path


def launcher(tmp_path: Path, **config) -> AgentLauncher:
    """An AgentLauncher with only a config -- __init__ picks ports and writes
    files, none of which the recording step touches."""
    instance = object.__new__(AgentLauncher)
    instance.config = OmegaConf.create({"task_name": "add_meeting_with_dennis", **config})
    return instance


def exp_dir_with_video(tmp_path: Path, payload: bytes = b"webm") -> Path:
    exp_dir = tmp_path / "exp"
    (exp_dir / "task_video").mkdir(parents=True)
    (exp_dir / "task_video" / "a1b2c3.webm").write_bytes(payload)
    return exp_dir


def test_off_by_default(tmp_path):
    assert launcher(tmp_path)._save_recording(exp_dir_with_video(tmp_path), {}) is None


def test_saved_under_a_readable_name_and_original_kept(tmp_path):
    out = tmp_path / "recordings"
    exp_dir = exp_dir_with_video(tmp_path, b"episode")
    saved = launcher(tmp_path, record_video_dir=str(out))._save_recording(exp_dir, {"cum_reward": 1.0})

    assert saved.parent == out
    assert re.fullmatch(r"\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d_add_meeting_with_dennis_pass\.webm", saved.name)
    assert saved.read_bytes() == b"episode"
    # AgentLab's tooling still finds it where it expects it.
    assert (exp_dir / "task_video" / "a1b2c3.webm").exists()


def test_failed_episode_and_job_id_are_in_the_name(tmp_path):
    saved = launcher(tmp_path, record_video_dir=str(tmp_path / "r"), job_id=3)._save_recording(
        exp_dir_with_video(tmp_path), {"cum_reward": 0.0}
    )
    assert saved.name.endswith("_add_meeting_with_dennis_fail_job3.webm")


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


def test_relative_paths_resolve_from_cwd_outside_hydra(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _absolute_path("recordings") == (tmp_path / "recordings").resolve()


def test_config_key_exists_and_defaults_off():
    cfg = OmegaConf.load(Path(__file__).resolve().parent.parent / "config" / "config.yaml")
    assert "record_video_dir" in cfg and cfg.record_video_dir is None
