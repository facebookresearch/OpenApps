"""Real Hydra composition and backend boundaries for shared sweeps."""

from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys

import pytest
from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf

from open_apps import config_dir
from open_apps import parallel_launcher
from open_apps.launcher import OpenAppsLauncher
from open_apps.parallel_launcher import create_job_configs, normalize_cli_args


@pytest.fixture
def catalogs(tmp_path):
    (tmp_path / "tasks").mkdir()
    (tmp_path / "agent").mkdir()
    for name, app in (("sweep_a", "calendar"), ("sweep_b", "todo")):
        task = {
            "_target_": "open_apps.tasks.tasks.NavigateToAppTask",
            "goal": f"Navigate to {app}",
            "source_app": "start_page",
            "target_app": app,
        }
        OmegaConf.save(
            OmegaConf.create({"shared": task, f"{app}_only": task}),
            tmp_path / "tasks" / f"{name}.yaml",
        )
    OmegaConf.save(
        OmegaConf.create({"defaults": ["dummy", "_self_"], "model_pretty_name": "other"}),
        tmp_path / "agent" / "other.yaml",
    )
    return tmp_path


def compose_jobs(catalogs, *extra):
    overrides = normalize_cli_args([
        "tasks=sweep_a.yaml,sweep_b.yaml",
        "agents=dummy,other",
        f"hydra.searchpath=[file://{catalogs}]",
        f"logs_dir={catalogs}/logs",
        'parallel_tasks.app_variations=[[],["apps/theme=dark"]]',
        *extra,
    ])
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        config = compose(config_name="config_parallel_tasks", overrides=overrides)
        return config, create_job_configs(config, overrides, "config_parallel_tasks")


def test_files_agents_variations_and_overrides(catalogs):
    config, jobs = compose_jobs(
        catalogs, "apps.todo.title=CLI title", "browsergym_env_args.max_steps=3"
    )
    assert len(jobs) == 16
    assert len({job.logs_dir for job in jobs}) == len(jobs)
    assert len({job.databases_dir for job in jobs}) == len(jobs)
    assert [job.job_id for job in jobs] == list(range(16))
    assert all(job.num_jobs == 16 for job in jobs)
    assert all(job.apps.todo.title == "CLI title" for job in jobs)
    assert all(job.browsergym_env_args.max_steps == 3 for job in jobs)
    assert {job.agent.model_pretty_name for job in jobs} == {"dummy", "other"}
    shared = [job for job in jobs if job.task_name == "shared"]
    assert len(shared) == 8
    assert {job.tasks.shared.target_app for job in shared} == {"calendar", "todo"}
    assert [job.task_config_name for job in jobs[:8]] == ["sweep_a"] * 8
    assert all(job.logs_dir.startswith(str(catalogs / "logs")) for job in jobs)
    assert all(job.databases_dir == f"{job.logs_dir}/databases" for job in jobs)
    assert {job.apps.theme for job in jobs} != {config.apps.theme}


def test_subset_matches_each_file_without_merging(catalogs):
    _, jobs = compose_jobs(catalogs, "parallel_tasks.task_names=[shared]")
    assert len(jobs) == 8
    assert {job.task_config_name for job in jobs} == {"sweep_a", "sweep_b"}


def test_single_catalog_with_agents_selects_all_tasks(catalogs):
    overrides = normalize_cli_args([
        "tasks=original_tasks.yaml",
        "agents=dummy,other",
        f"hydra.searchpath=[file://{catalogs}]",
    ])
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        config = compose(config_name="config_parallel_tasks", overrides=overrides)
    assert len(config.tasks) == 31
    assert config.parallel_tasks.task_names == "all"
    assert list(config.parallel_tasks.task_configs) == ["original_tasks"]


def test_single_catalog_subset_keeps_catalog_and_agent_provenance(catalogs):
    overrides = normalize_cli_args([
        "tasks=original_tasks.yaml",
        "agents=dummy,other",
        "parallel_tasks.task_names=[navigate_to_messenger]",
        "parallel_tasks.app_variations=[[]]",
        f"hydra.searchpath=[file://{catalogs}]",
        f"logs_dir={catalogs}/logs",
    ])
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        config = compose(config_name="config_parallel_tasks", overrides=overrides)
        jobs = create_job_configs(config, overrides, "config_parallel_tasks")
    assert [(job.task_config_name, job.agent_config_name) for job in jobs] == [
        ("original_tasks", "dummy"), ("original_tasks", "other")
    ]


def test_hydra_searchpath_override_reaches_child_composition(catalogs, monkeypatch):
    overrides = normalize_cli_args([
        "tasks=sweep_a.yaml,sweep_b.yaml",
        "agents=dummy,other",
        f"logs_dir={catalogs}/logs",
        "parallel_tasks.task_names=[shared]",
        "parallel_tasks.app_variations=[[]]",
    ])
    searchpath = f"hydra.searchpath=[file://{catalogs}]"
    hydra_config = OmegaConf.create({
        "overrides": {"task": overrides, "hydra": [searchpath]},
        "job": {"config_name": "config_parallel_tasks"},
    })
    monkeypatch.setattr(parallel_launcher.HydraConfig, "get", lambda: hydra_config)
    with initialize_config_dir(config_dir=str(config_dir()), version_base=None):
        config = compose(config_name="config_parallel_tasks", overrides=[*overrides, searchpath])
        jobs = create_job_configs(config)
    assert len(jobs) == 4


def test_unknown_task_fails_before_launch(catalogs):
    with pytest.raises(ValueError, match="Unknown task names"):
        compose_jobs(catalogs, "parallel_tasks.task_names=[missing]")


@pytest.mark.parametrize("selection", ["tasks=sweep_a,missing", "agents=dummy,missing"])
def test_unknown_config_fails_before_launch(catalogs, selection):
    with pytest.raises(Exception, match="missing"):
        compose_jobs(catalogs, selection)


def test_existing_overrides_and_native_multirun_are_preserved():
    arguments = ["tasks=original_tasks", "agent=dummy", "device=phone"]
    assert normalize_cli_args(arguments) == arguments


def test_existing_script_accepts_comma_selectors(catalogs):
    script = Path(__file__).resolve().parents[1] / "launch_parallel_agents.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "tasks=sweep_a.yaml,sweep_b.yaml",
            "agents=dummy,other",
            f"hydra.searchpath=[file://{catalogs}]",
            "--cfg",
            "job",
        ],
        cwd=catalogs,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    config = OmegaConf.create(result.stdout)
    assert list(config.parallel_tasks.task_configs) == ["sweep_a", "sweep_b"]
    assert list(config.parallel_tasks.agent_configs) == ["dummy", "other"]
    assert config.parallel_tasks.task_names == "all"
    arguments = ["--multirun", "tasks=original_tasks,user_goal_variations", "agent=dummy"]
    assert normalize_cli_args(arguments) == arguments


@pytest.mark.parametrize(
    "arguments",
    [["agents=dummy", "agent=other"], ["-m", "agents=dummy,other"], ["agents=[]"]],
)
def test_conflicting_or_empty_selectors_are_rejected(arguments):
    with pytest.raises(ValueError):
        normalize_cli_args(arguments)


def test_large_job_port_falls_back_to_valid_port():
    launcher = object.__new__(OpenAppsLauncher)
    port = launcher.pick_empty_port(start_port=5001 + 606 * 100)
    assert 0 < port <= 65535


def test_large_job_reuses_the_forwarded_port_in_the_app_process(tmp_path, monkeypatch):
    config = OmegaConf.create({
        "seed": 233,
        "job_id": 606,
        "logs_dir": str(tmp_path),
        "databases_dir": str(tmp_path / "databases"),
        "use_wandb": False,
    })
    parent = OpenAppsLauncher(config)
    saved = OmegaConf.load(parent.config_path)
    assert "web_app_port" not in saved
    forwarded = OmegaConf.merge(saved, {"web_app_port": parent.web_app_port})

    def unexpected_port_pick(*args, **kwargs):
        pytest.fail("The app subprocess must reuse its parent's forwarded port")

    monkeypatch.setattr(OpenAppsLauncher, "pick_empty_port", unexpected_port_pick)
    child = OpenAppsLauncher(forwarded)
    assert child.web_app_port == parent.web_app_port == forwarded.web_app_port
    assert child.web_app_url == parent.web_app_url


def test_worker_environment_interpolations_stay_lazy(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAPPS_WORKER_ONLY_TOKEN", raising=False)
    config = OmegaConf.create({"cluster": "slurm", "logs_dir": str(tmp_path)})
    job = OmegaConf.create({
        "job_id": 0,
        "task_name": "task",
        "app_overrides": [],
        "agent": {
            "model_pretty_name": "demo",
            "api_key": "${oc.env:OPENAPPS_WORKER_ONLY_TOKEN}",
        },
    })
    monkeypatch.setattr(parallel_launcher, "create_job_configs", lambda _: [job])
    monkeypatch.setattr(parallel_launcher, "run_via_slurm", lambda *args: args[1])
    assert parallel_launcher.launch_parallel(config) == [job]
    assert OmegaConf.is_interpolation(job.agent, "api_key")


@pytest.mark.parametrize("cluster", ["local", "slurm"])
def test_independent_launches_with_shared_logs_root_get_unique_storage(
    tmp_path, monkeypatch, cluster
):
    directories = []

    def create_jobs(config):
        return [OmegaConf.create({
            "job_id": 0,
            "task_name": "task",
            "app_overrides": [],
            "agent": {"model_pretty_name": "dummy"},
            "logs_dir": f"{config.logs_dir}/0",
            "databases_dir": f"{config.logs_dir}/0/databases",
        })]

    def record_local(config, jobs, *args):
        directories.append((jobs[0].logs_dir, jobs[0].databases_dir, None))

    def record_slurm(config, jobs, count, sweep_dir, *args):
        directories.append((jobs[0].logs_dir, jobs[0].databases_dir, str(sweep_dir)))
        return []

    monkeypatch.setattr(parallel_launcher, "create_job_configs", create_jobs)
    monkeypatch.setattr(parallel_launcher, "run_locally", record_local)
    monkeypatch.setattr(parallel_launcher, "run_via_slurm", record_slurm)
    for launch_index in range(2):
        config = OmegaConf.create({"cluster": cluster, "logs_dir": str(tmp_path)})
        parallel_launcher.launch_parallel(config)
    assert directories[0][0] != directories[1][0]
    assert directories[0][1] != directories[1][1]
    for logs_dir, databases_dir, slurm_dir in directories:
        run_dir = Path(logs_dir).parent
        assert run_dir.parent == tmp_path and run_dir.is_dir()
        assert run_dir.name.startswith("run-")
        assert run_dir.stat().st_mode & 0o777 == 0o755
        assert Path(databases_dir) == Path(logs_dir) / "databases"
        if cluster == "slurm":
            assert slurm_dir == str(run_dir / "slurm")


def test_slurm_receives_isolated_configs_and_launcher(catalogs, monkeypatch):
    config, jobs = compose_jobs(catalogs)
    submissions = []

    class Executor:
        def __init__(self, **kwargs):
            assert kwargs["cluster"] == "slurm"

        def update_parameters(self, **parameters):
            pass

        @contextmanager
        def batch(self):
            yield

        def submit(self, function, job_config, launcher_class):
            submissions.append((function, job_config, launcher_class))
            return type("Job", (), {"job_id": str(len(submissions))})()

    monkeypatch.setattr(parallel_launcher.submitit, "AutoExecutor", Executor)
    submitted = parallel_launcher.run_via_slurm(
        config, jobs, len(jobs), catalogs / "slurm", OpenAppsLauncher
    )
    assert len(submitted) == 16
    assert all(function is parallel_launcher.run_task for function, _, _ in submissions)
    assert all(launcher is OpenAppsLauncher for _, _, launcher in submissions)
    assert len({job.databases_dir for _, job, _ in submissions}) == 16


def test_local_uses_the_supplied_launcher(catalogs, monkeypatch):
    config, jobs = compose_jobs(catalogs, "parallel_tasks.task_names=[shared]")
    calls = []
    monkeypatch.setattr(
        parallel_launcher, "run_task", lambda job, launcher: calls.append((job, launcher))
    )
    parallel_launcher.run_locally(config, jobs, len(jobs), OpenAppsLauncher)
    assert calls == [(job, OpenAppsLauncher) for job in jobs]
