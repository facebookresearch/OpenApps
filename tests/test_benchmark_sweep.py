"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

scripts/benchmark_sweep.py: the model registry, launch-group planning and the
commands it builds. Nothing here submits a job or calls a model.
"""

import importlib.util
import io
import sys
from pathlib import Path

import pytest
import yaml
from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra

from open_apps import config_dir

REPO = Path(__file__).resolve().parent.parent


def _load_sweep():
    spec = importlib.util.spec_from_file_location(
        "benchmark_sweep", REPO / "scripts" / "benchmark_sweep.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["benchmark_sweep"] = module  # dataclasses resolve their module
    spec.loader.exec_module(module)
    return module


S = _load_sweep()
REGISTRY = S.load_registry()


# --- registry --------------------------------------------------------------------


def _agent_config(name: str):
    GlobalHydra.instance().clear()
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        return compose(config_name="config", overrides=[f"agent={name}"]).agent


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_registry_entries_match_their_agent_config(name):
    model = REGISTRY[name]
    agent = _agent_config(name)  # the config exists and composes
    if model.kind == "vllm":
        assert agent.client_type == "vllm"
        # vLLM serves hf_model under that name; the agent must ask for it.
        assert agent.model_name == model.hf_model
        assert model.tensor_parallel("h100") >= model.tensor_parallel("h200") >= 1
    else:
        assert agent.get("client_type") != "vllm"


def test_every_self_hosted_agent_config_is_in_the_registry():
    runnable_fragments = {"axtree-only", "screenshot-only", "Qwen3.6-VL-computer-use", "default"}
    for path in (config_dir() / "agent").glob("*.yaml"):
        if path.stem in runnable_fragments:
            continue
        raw = yaml.safe_load(path.read_text()) or {}
        if raw.get("client_type") == "vllm":
            assert path.stem in REGISTRY, f"add {path.stem} to scripts/benchmark_models.yaml"


def test_vllm_agents_send_the_key_the_server_starts_with(monkeypatch):
    monkeypatch.delenv("VLLM_API_KEY", raising=False)
    sbatch = (REPO / "scripts" / "serve_vllm.sbatch").read_text()
    assert 'VLLM_API_KEY="${VLLM_API_KEY:-AMI_RULZ}"' in sbatch
    assert '--api-key "$VLLM_API_KEY"' in sbatch
    for model in (m for m in REGISTRY.values() if m.kind == "vllm"):
        assert _agent_config(model.agent).api_key == S.DEFAULT_VLLM_API_KEY
    monkeypatch.setenv("VLLM_API_KEY", "site-key")
    assert _agent_config("gemma-4-31B-coords").api_key == "site-key"


def test_parse_registry_applies_defaults_and_rejects_bad_entries():
    data = {
        "defaults": {"max_model_len": 4096, "shards": 3},
        "models": {"m": {"kind": "vllm", "hf_model": "org/m", "gpus": {"h100": 2, "h200": 1}}},
    }
    model = S.parse_registry(data)["m"]
    assert (model.max_model_len, model.shards, model.tensor_parallel("h100")) == (4096, 3, 2)
    assert model.serve_env(2)["VLLM_MODEL"] == "org/m"
    for bad, match in [
        ({"kind": "gpu"}, "kind"),
        ({"kind": "vllm", "gpus": {"h100": 1, "h200": 1}}, "hf_model"),
        ({"kind": "vllm", "hf_model": "x", "gpus": {"h100": 1}}, "missing"),
        ({"kind": "api", "typo": 1}, "unknown"),
    ]:
        with pytest.raises(ValueError, match=match):
            S.parse_registry({"models": {"m": bad}})


def test_select_groups_and_unknown_names():
    assert [m.agent for m in S.select(REGISTRY, ["dummy"])] == ["dummy"]
    vllm = S.select(REGISTRY, ["vllm"])
    assert vllm and all(m.kind == "vllm" for m in vllm)
    assert "dummy" not in [m.agent for m in S.select(REGISTRY, ["all"])]
    both = S.select(REGISTRY, ["UI-TARS-1.5-7B,dummy", "dummy"])
    assert [m.agent for m in both] == ["UI-TARS-1.5-7B", "dummy"]
    with pytest.raises(SystemExit, match="unknown model"):
        S.select(REGISTRY, ["GPT-4o"])


def test_models_sharing_a_checkpoint_share_one_launch():
    groups = S.plan_groups(S.select(REGISTRY, ["all", "dummy"]))
    by_label = {g.label: g for g in groups}
    gemma = by_label["google__gemma-4-31B-it"]
    assert gemma.agents == ["gemma-4-31B-computer-use", "gemma-4-31B-coords"]
    assert gemma.server.hf_model == "google/gemma-4-31B-it"
    hosted = by_label["hosted"]
    assert hosted.server is None and "dummy" in hosted.agents
    assert sum(len(g.agents) for g in groups) == len(REGISTRY)
    separate = S.plan_groups(S.select(REGISTRY, ["vllm"]), separate=True)
    assert [g.label for g in separate] == [m.agent for m in S.select(REGISTRY, ["vllm"])]


# --- commands ---------------------------------------------------------------------


def _group(*agents):
    groups = S.plan_groups(S.select(REGISTRY, list(agents)))
    assert len(groups) == 1
    return groups[0]


def test_launch_command_for_a_served_model(tmp_path):
    cmd = S.launch_command(
        _group("UI-TARS-1.5-7B"), mode="internal-slurm_cluster", logs_root=tmp_path,
        hostname="node-3", seed_set="heldout", extra=["use_wandb=True"],
    )
    assert cmd[:3] == ["uv", "run", "launch_parallel_agents.py"]
    for part in [
        "benchmark=align_v1", "benchmark.seed_set=heldout", "agent=UI-TARS-1.5-7B",
        "mode=internal-slurm_cluster", "agent.hostname=node-3", "agent.port=8000",
        f"logs_dir={tmp_path.resolve()}/UI-TARS-1.5-7B", "open_leaderboard=false",
    ]:
        assert part in cmd
    assert cmd[-1] == "use_wandb=True"
    with pytest.raises(ValueError, match="serving node"):
        S.launch_command(_group("UI-TARS-1.5-7B"), mode="local", logs_root=tmp_path)


def test_launch_command_uses_the_native_agent_sweep_and_shards(tmp_path):
    cmd = S.launch_command(
        _group("gemma-4-31B-computer-use", "gemma-4-31B-coords"), mode="local",
        logs_root=tmp_path, hostname="127.0.0.1", shard=(2, 8),
    )
    assert "agents=gemma-4-31B-computer-use,gemma-4-31B-coords" in cmd
    assert not any(c.startswith("agent=") for c in cmd)
    assert {"shard_index=2", "num_shards=8", "continue_on_error=true"} <= set(cmd)
    hosted = S.launch_command(_group("dummy"), mode="local", logs_root=tmp_path, smoke=True)
    assert not any(c.startswith(("benchmark", "agent.hostname")) for c in hosted)
    assert set(S.SMOKE_OVERRIDES) <= set(hosted)


def test_smoke_overrides_compose_into_two_runs():
    from open_apps.tasks.parallel_tasks import AppVariationParallelTasksConfig

    GlobalHydra.instance().clear()
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        cli = ["agent=dummy", *S.SMOKE_OVERRIDES]
        parent = compose(config_name="config_parallel_tasks", overrides=cli)
        pt = parent.parallel_tasks
        runs = AppVariationParallelTasksConfig(
            app_variations=list(pt.app_variations), task_names=list(pt.task_names)
        ).create_configs(parent, cli, "config_parallel_tasks")
    assert len(runs) == 2
    assert {r.task_termination for r in runs} == {"on_agent_stop"}


def test_serve_and_single_node_submissions(tmp_path):
    slurm = S.Slurm(account="acct", qos="q", partition="h200", time_min=600)
    serve = S.serve_submit_command(REGISTRY["Qwen3.6-27B-computer-use"], gpus=2, slurm=slurm)
    assert serve[:2] == ["sbatch", "--parsable"]
    assert "--gpus-per-node=2" in serve and "--account=acct" in serve
    assert "--partition=h200" in serve and "--time=600" in serve
    export = next(c for c in serve if c.startswith("--export="))
    assert "VLLM_MODEL=Qwen/Qwen3.6-27B" in export and "GPUS=2" in export
    assert export.startswith("--export=ALL,")
    assert serve[-1].endswith("scripts/serve_vllm.sbatch")

    one = S.one_submit_command(
        _group("gemma-4-31B-computer-use", "gemma-4-31B-coords"), gpus=1, slurm=S.Slurm(),
        logs_root=tmp_path, smoke=True, extra=["use_wandb=True"],
    )
    export = next(c for c in one if c.startswith("--export="))
    assert "AGENT=gemma-4-31B-computer-use gemma-4-31B-coords" in export
    assert "SMOKE=1" in export and f"LOGS_ROOT={tmp_path.resolve()}" in export
    assert not any(c.startswith("--account") for c in one)
    assert one[-2].endswith("scripts/benchmark_one.sbatch") and one[-1] == "use_wandb=True"


def test_slurm_settings_precedence(tmp_path):
    internal = tmp_path / "internal-slurm_cluster.yaml"
    internal.write_text(
        "slurm_sweep_launcher:\n  slurm_account: a0\n  slurm_qos: q0\n  slurm_partition: p0\n"
    )
    assert S.slurm_settings({}, internal) == S.Slurm("a0", "q0", "p0", 1440)
    env = {"SBATCH_QOS": "q1", "OPENAPPS_SLURM_PARTITION": "h100", "OPENAPPS_SLURM_TIME": "60"}
    assert S.slurm_settings(env, internal) == S.Slurm("a0", "q1", "h100", 60)
    assert S.slurm_settings({}, tmp_path / "missing.yaml") == S.Slurm()
    assert S.default_mode(internal) == "internal-slurm_cluster"
    assert S.default_mode(tmp_path / "missing.yaml") == "local"


def test_parse_array_id():
    out = "155 runs in this sweep.\nSubmitted 155 SLURM jobs: 4242_0, 4242_1, 4242_2\n"
    assert S.parse_array_id(out) == "4242"
    assert S.parse_array_id("Submitted 1 SLURM jobs: 77\n") == "77"
    assert S.parse_array_id("nothing") is None


def test_collect_splits_a_shared_sweep_by_agent(tmp_path):
    for agent, job in [("a", "0"), ("b", "1"), ("a", "2")]:
        d = tmp_path / "grp" / "run-x1" / job
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(yaml.safe_dump({"agent_config_name": agent}))
    (tmp_path / "grp" / "run-x1" / "config.yaml").write_text("{}")  # not a job dir
    jobs = S.job_dirs_by_agent(tmp_path)
    assert {k: [p.name for p in v] for k, v in jobs.items()} == {"a": ["0", "2"], "b": ["1"]}
    cmds = S.collect_commands(tmp_path, jobs, org="me")
    packs = [c for c in cmds if "open_apps.leaderboard.pack" in c]
    assert len(packs) == 2
    assert packs[0][packs[0].index("--out") + 1].endswith("_bundles/me__a__align_v1")
    assert sum("open_apps.leaderboard.validate" in c for c in cmds) == 2
    assert "open_apps.leaderboard.preview" in cmds[-1]
    smoke = S.collect_commands(tmp_path, jobs, smoke=True)
    assert len(smoke) == 1 and "open_apps.leaderboard.preview" in smoke[0]


def test_run_dry_run_prints_the_whole_pipeline(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GPT55_API_KEY", raising=False)
    code = S.main([
        "run", "UI-TARS-1.5-7B", "GPT-5.5-computer-use", "--mode", "internal-slurm_cluster",
        "--gpu-type", "h100", "--logs-root", str(tmp_path), "--dry-run", "--collect",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "serve_vllm.sbatch" in out and "--gpus-per-node=1" in out
    assert "agent.hostname='<node>'" in out or "agent.hostname=<node>" in out
    assert "scancel '<serve-job>'" in out or "scancel <serve-job>" in out
    assert "GPT55_API_KEY unset" in out
    assert "open_apps.leaderboard.pack" in out and "open_apps.leaderboard.preview" in out
    # nothing was written: a dry run creates no logs
    assert not any(tmp_path.iterdir())


def test_run_local_dry_run_starts_one_process_per_shard(tmp_path, capsys):
    S.main(["run", "dummy", "--mode", "local", "--shards", "3",
            "--logs-root", str(tmp_path), "--dry-run"])
    out = capsys.readouterr().out
    assert out.count("launch_parallel_agents.py") == 3
    assert "num_shards=3" in out and "serve_vllm" not in out


def test_env_refuses_agents_needing_two_servers(capsys):
    with pytest.raises(SystemExit, match="2 servers"):
        S.main(["env", "UI-TARS-1.5-7B", "gemma-4-2B-computer-use"])
    assert S.main(["env", "UI-TARS-1.5-7B", "--gpu-type", "h100"]) == 0
    out = capsys.readouterr().out
    assert "export VLLM_MODEL=ByteDance-Seed/UI-TARS-1.5-7B" in out
    assert "export AGENTS_SELECTOR=agent=UI-TARS-1.5-7B" in out


def test_runner_dry_run_never_executes(tmp_path):
    buf = io.StringIO()
    runner = S.Runner(True, buf)
    assert runner.run(["false"]) == ""
    assert runner.run_parallel([["false"]], [tmp_path / "x.log"]) == 0
    assert "+ false" in buf.getvalue() and not (tmp_path / "x.log").exists()
