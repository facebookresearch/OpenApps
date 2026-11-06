"""Shared entry points for task and agent sweeps, locally or on SLURM."""

import json
from pathlib import Path
import tempfile
import time
from typing import Sequence
import urllib.error
import urllib.request
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


def run_task(
    config: DictConfig, launcher_class: type[AgentLauncher] = AgentLauncher
) -> None:
    launcher_class(config).launch()


def shard_indices(num_jobs: int, shard_index: int = 0, num_shards: int = 1) -> list[int]:
    """The job ids one local process runs when a sweep is split ``num_shards``
    ways: every ``num_shards``-th id from ``shard_index`` (round-robin, so each
    shard gets a mix of blocks rather than all the long ones)."""
    if num_shards < 1 or not 0 <= shard_index < num_shards:
        raise ValueError(
            f"need 0 <= shard_index < num_shards, got {shard_index}/{num_shards}"
        )
    return list(range(shard_index, num_jobs, num_shards))


def wait_for_url(url: str, timeout_s: float, poll_s: float = 10.0) -> None:
    """Block until ``url`` answers 200 (e.g. a vLLM server's ``/health``).

    Composing a suite takes minutes, so benchmark_one.sbatch starts its shards
    alongside the server and has them wait here, after composing and before
    the first episode, rather than idling the GPU through both in turn.
    """
    # The server is local or cluster-internal; an inherited proxy would only
    # turn every probe into a timeout.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with opener.open(url, timeout=5) as response:
                if response.status == 200:
                    print(f"{url} is up.")
                    return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{url} did not answer within {timeout_s:.0f}s")
        print(f"Waiting for {url} ...")
        time.sleep(poll_s)


def local_shard(config: DictConfig) -> tuple[int, int]:
    """``(shard_index, num_shards)`` from config/mode/local.yaml (``(0, 1)``
    when the mode does not define them)."""
    return int(config.get("shard_index", 0) or 0), int(config.get("num_shards", 1) or 1)


def run_locally(
    config: DictConfig,
    parallel_configs: list[DictConfig],
    num_jobs: int,
    launcher_class: type[AgentLauncher] = AgentLauncher,
) -> None:
    """Run the jobs one after another in this process.

    ``shard_index``/``num_shards`` (config/mode/local.yaml) split one sweep
    across several processes on the same machine -- scripts/benchmark_one.sbatch
    runs eight against one vLLM server, which batches their requests. Job ids
    stay global, so the shards' job directories and web-app port windows never
    collide; give every shard the same ``logs_dir`` root.
    ``continue_on_error`` keeps going past a job that raises, lists the failed
    ids at the end and exits non-zero.
    """
    shard_index, num_shards = local_shard(config)
    selected = set(shard_indices(num_jobs, shard_index, num_shards))
    jobs = [job for job in parallel_configs if job.job_id in selected]
    if num_shards > 1:
        print(f"Shard {shard_index}/{num_shards}: {len(jobs)} of {num_jobs} jobs.")
    if config.get("wait_for_url"):
        wait_for_url(
            str(config.wait_for_url), int(config.get("wait_for_url_timeout_s", 5400))
        )
    continue_on_error = bool(config.get("continue_on_error", False))
    failed: list[int] = []
    for job_config in jobs:
        print(f"\n=== Running local job {job_config.job_id + 1}/{num_jobs} ===")
        try:
            run_task(job_config, launcher_class)
        except Exception as exc:  # noqa: BLE001 -- opt-in, for unattended sweeps
            if not continue_on_error:
                raise
            print(f"Local job {job_config.job_id} failed, continuing: {exc!r}")
            failed.append(job_config.job_id)
    print(f"\nFinished {len(jobs) - len(failed)}/{len(jobs)} local jobs.")
    if failed:
        print(f"Failed job ids: {failed}. Re-run them; pack takes several logs dirs.")
        raise SystemExit(1)


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
    # Always say how big the sweep is before anything is queued (a
    # `benchmark=` suite also prints its per-block breakdown above).
    print(f"{len(jobs)} runs in this sweep.")
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
    # Every model run so far sits under the configured logs_dir's parent
    # (log_outputs/), and the point of the preview is the new one beside them.
    leaderboard_root = logs_root.parent
    if cluster == "local":
        run_locally(config, jobs, len(jobs), launcher_class)
        # One shard of several finishing says nothing about the others.
        if config.get("open_leaderboard", True) and local_shard(config)[1] == 1:
            from open_apps.leaderboard.preview import launch

            launch([leaderboard_root])
        return []
    sweep_dir = Path(config.logs_dir) / "slurm"
    submitted = run_via_slurm(config, jobs, len(jobs), sweep_dir, launcher_class)
    if config.get("open_leaderboard", True):
        # The jobs run after this process exits; say how to see them.
        print(
            "When the jobs finish, see every model under the logs root ranked with:\n"
            f"  uv run -m open_apps.leaderboard.preview {leaderboard_root}"
        )
    return submitted
