"""Shared entry points for task and agent sweeps, locally or on SLURM."""

from concurrent.futures import ProcessPoolExecutor, as_completed
import functools
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import traceback
from typing import Callable, Optional, Sequence
import warnings

import hydra
import submitit
from hydra.core.hydra_config import HydraConfig
from hydra.core.override_parser.overrides_parser import OverridesParser
from omegaconf import DictConfig, OmegaConf, open_dict

from open_apps.launcher import AgentLauncher, freeze_recording_dir


def normalize_cli_args(arguments: Sequence[str]) -> list[str]:
    """Translate comma selectors into ordinary Hydra overrides."""
    if "-m" in arguments or "--multirun" in arguments:
        if any(argument.startswith("agents=") for argument in arguments):
            raise ValueError("agents= selects a parallel sweep; omit --multirun")
        return list(arguments)

    parser = OverridesParser.create()
    agent_sweep = any(argument.startswith("agents=") for argument in arguments)
    normalized = []
    selectors = []
    for argument in arguments:
        key, separator, value = argument.partition("=")
        if not separator or key not in ("tasks", "agents"):
            normalized.append(argument)
            continue
        override = parser.parse_override(argument)
        if key == "tasks" and not override.is_sweep_override() and not agent_sweep:
            normalized.append(f"tasks={value.removesuffix('.yaml')}")
            continue
        values = (
            list(override.sweep_iterator())
            if override.is_sweep_override()
            else override.value()
        )
        if not isinstance(values, list):
            values = [values]
        if not values or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise ValueError(f"{key}= requires nonempty config names")
        values = [value.removesuffix(".yaml") for value in values]
        field = "task_configs" if key == "tasks" else "agent_configs"
        selectors.append(f"++parallel_tasks.{field}={json.dumps(values)}")
        normalized.append(f"{'tasks' if key == 'tasks' else 'agent'}={values[0]}")
        if key == "tasks" and not any(
            argument.startswith("parallel_tasks.task_names=") for argument in arguments
        ):
            selectors.append("parallel_tasks.task_names=all")
    if any(argument.startswith("agents=") for argument in arguments) and any(
        argument.startswith("agent=") for argument in arguments
    ):
        raise ValueError("Choose either agent= or agents=, not both")
    insertion_point = next(
        (
            index
            for index, argument in enumerate(normalized)
            if "=" in argument and not argument.startswith("-")
        ),
        len(normalized),
    )
    return [*normalized[:insertion_point], *selectors, *normalized[insertion_point:]]


def create_job_configs(
    config: DictConfig,
    cli_overrides: Sequence[str] | None = None,
    config_name: str | None = None,
) -> list[DictConfig]:
    """Compose the sweep and give every job independent logs and databases."""
    if cli_overrides is None or config_name is None:
        hydra_config = HydraConfig.get()
        if cli_overrides is None:
            cli_overrides = [
                *hydra_config.overrides.task,
                *[
                    override
                    for override in hydra_config.overrides.get("hydra", [])
                    if override.startswith("hydra.searchpath=")
                ],
            ]
        if config_name is None:
            config_name = hydra_config.job.config_name
    groups = {}
    if HydraConfig.initialized():
        groups.update(HydraConfig.get().get("runtime", {}).get("choices", {}))
    parser = OverridesParser.create()
    for override in cli_overrides:
        key = override.partition("=")[0].lstrip("+")
        if key in ("tasks", "agent"):
            value = parser.parse_override(override).value()
            if isinstance(value, str):
                groups[key] = value.removesuffix(".yaml")
    freeze_recording_dir(config)
    jobs = hydra.utils.instantiate(config.parallel_tasks).create_configs(
        default_config=config, cli_overrides=cli_overrides, config_name=config_name
    )
    logs_dir = Path(hydra.utils.to_absolute_path(str(config.logs_dir))).resolve()
    for job_id, job_config in enumerate(jobs):
        with open_dict(job_config):
            job_config.job_id = job_id
            job_config.num_jobs = len(jobs)
            job_config.logs_dir = str(logs_dir / str(job_id))
            job_config.databases_dir = f"{job_config.logs_dir}/databases"
            job_config.task_config_name = (
                job_config.get("task_config_name") or groups.get("tasks")
            )
            job_config.agent_config_name = (
                job_config.get("agent_config_name") or groups.get("agent")
            )
    return jobs


def label_wandb_runs(jobs: Sequence[DictConfig], group: str) -> str:
    """Give every run in a sweep the same W&B group, and its agent as job_type.

    A value already set (``wandb.group=...`` or ``wandb.job_type=...`` on the
    command line) is kept. Returns the group the runs ended up in.
    """
    for job in jobs:
        with open_dict(job):
            if not job.wandb.get("group"):
                job.wandb.group = group
            if not job.wandb.get("job_type"):
                agent = job.get("agent") or {}
                job.wandb.job_type = (
                    job.get("agent_config_name")
                    or agent.get("model_pretty_name")
                    or agent.get("model_name")
                    or "agent"
                )
    return jobs[0].wandb.group if jobs else group


def run_task(
    config: DictConfig, launcher_class: type[AgentLauncher] = AgentLauncher
) -> None:
    launcher_class(config).launch()


#: File each concurrent local job's stdout and stderr go to, inside its logs_dir.
RUN_LOG = "run.log"


def run_locally(
    config: DictConfig,
    parallel_configs: list[DictConfig],
    num_jobs: int,
    launcher_class: type[AgentLauncher] = AgentLauncher,
) -> None:
    """Run the jobs on this machine: one at a time in this process, streaming
    to the terminal (``max_parallel=1``, the default), or ``max_parallel`` at
    a time, each in its own process writing to ``<job logs_dir>/run.log``."""
    max_parallel = int(config.get("max_parallel", 1) or 1)
    if max_parallel <= 1:
        for job_config in parallel_configs:
            print(f"\n=== Running local job {job_config.job_id + 1}/{num_jobs} ===")
            run_task(job_config, launcher_class)
        print(f"\nFinished {num_jobs} local jobs.")
        return
    print(f"Running {num_jobs} local jobs, {max_parallel} at a time; each logs to <job>/{RUN_LOG}")
    task = functools.partial(run_task, launcher_class=launcher_class)
    failures = run_concurrently(parallel_configs, max_parallel, task)
    print(f"\nFinished {num_jobs} local jobs: {num_jobs - len(failures)} ok, {len(failures)} failed.")
    if failures:
        raise SystemExit(1)


def _run_logged(task: Callable[[DictConfig], None], config: DictConfig) -> Optional[str]:
    """Run ``task`` with this process's output sent to the job's run log.

    Redirects the file descriptors, not just ``sys.stdout``, so output from the
    web app subprocess and from BrowserGym lands in the log too. Returns the
    traceback on failure instead of raising, so one bad job is a line in the
    summary rather than a broken pool.
    """
    log_path = Path(config.logs_dir) / RUN_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout.flush()
    sys.stderr.flush()
    with open(log_path, "a", buffering=1) as log:
        os.dup2(log.fileno(), sys.stdout.fileno())
        os.dup2(log.fileno(), sys.stderr.fileno())
        try:
            task(config)
            return None
        except Exception:
            trace = traceback.format_exc()
            print(trace, file=sys.stderr, flush=True)
            return trace


def run_concurrently(
    configs: Sequence[DictConfig],
    max_parallel: int,
    task: Callable[[DictConfig], None] = run_task,
) -> list[DictConfig]:
    """Run every config, at most ``max_parallel`` at once. Returns the failures.

    Each job runs in a fresh spawned process (``max_tasks_per_child=1``):

    * **spawn, not fork** -- by the time jobs start, this process has imported
      Playwright and may have started wandb, both of which run threads, and
      forking a process with live threads is unsafe.
    * **one job per process** -- a job leaves state behind: BrowserGym task
      registrations, SQLite handles, wandb's run. A fresh process starts clean,
      the same as a separate ``launch_agent.py`` would.

    Each config already carries its own ``job_id``, ``logs_dir`` and
    ``databases_dir`` (``create_job_configs``), which keep concurrent jobs off
    each other's ports and databases. ``task`` must be importable by name, since
    a spawned process receives it by reference.
    """
    failures: list[DictConfig] = []
    with ProcessPoolExecutor(
        max_workers=max_parallel,
        mp_context=multiprocessing.get_context("spawn"),
        max_tasks_per_child=1,
    ) as pool:
        futures = {pool.submit(_run_logged, task, config): config for config in configs}
        for future in as_completed(futures):
            config = futures[future]
            try:
                error = future.result()
            except Exception as exc:  # the worker process itself died
                error = repr(exc)
            log_path = Path(config.logs_dir) / RUN_LOG
            status = "ok" if error is None else "FAILED"
            print(f"[done] job {config.job_id} {config.task_name}: {status} -> {log_path}", flush=True)
            if error is not None:
                failures.append(config)
    return failures


def run_via_slurm(
    config: DictConfig,
    parallel_configs: list[DictConfig],
    num_jobs: int,
    sweep_dir: str | Path,
    launcher_class: type[AgentLauncher] = AgentLauncher,
) -> list[submitit.Job]:
    executor = submitit.AutoExecutor(folder=sweep_dir, cluster="slurm")
    executor.update_parameters(**config.get("slurm_sweep_launcher", {}))
    with executor.batch():
        jobs = [
            executor.submit(run_task, job_config, launcher_class)
            for job_config in parallel_configs
        ]
    print(f"Submitted {num_jobs} SLURM jobs: {', '.join(job.job_id for job in jobs)}")
    print(f"SLURM output in {sweep_dir}")
    return jobs


def launch_parallel(
    config: DictConfig, launcher_class: type[AgentLauncher] = AgentLauncher
) -> list[submitit.Job]:
    """Run a sweep using an importable launcher class, including downstream ones."""
    cluster = config.get("cluster")
    if cluster not in ("local", "slurm"):
        raise ValueError(f"cluster={cluster} is not supported for parallel agents")
    logs_template = OmegaConf.to_container(config, resolve=False).get("logs_dir")
    if isinstance(logs_template, str) and (
        "${job_id}" in logs_template or "${agent." in logs_template
    ):
        warnings.warn(
            "logs_dir is resolved once in the parent. References to job_id or "
            "agent.* use the parent values for every job. Use a sweep-level "
            "logs root; run and job subdirectories are added automatically.",
            UserWarning,
            stacklevel=2,
        )
    logs_root = Path(hydra.utils.to_absolute_path(str(config.logs_dir))).resolve()
    logs_root.mkdir(parents=True, exist_ok=True)
    config.logs_dir = tempfile.mkdtemp(prefix="run-", dir=logs_root)
    Path(config.logs_dir).chmod(0o755)
    print(f"Run directory: {config.logs_dir}")
    jobs = create_job_configs(config)
    if config.get("use_wandb"):
        print(f"W&B group: {label_wandb_runs(jobs, Path(config.logs_dir).name)}")
    for job_config in jobs:
        catalog = job_config.get("task_config_name")
        task_label = f"{catalog}/{job_config.task_name}" if catalog else job_config.task_name
        agent_label = job_config.get("agent_config_name") or OmegaConf.to_container(
            job_config.agent, resolve=False
        ).get("model_pretty_name", "agent")
        print(
            f"job {job_config.job_id}: {task_label} {agent_label} "
            f"{list(job_config.app_overrides)}"
        )
    if cluster == "local":
        run_locally(config, jobs, len(jobs), launcher_class)
        return []
    sweep_dir = Path(config.logs_dir) / "slurm"
    return run_via_slurm(config, jobs, len(jobs), sweep_dir, launcher_class)
