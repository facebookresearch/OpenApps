"""Shared entry points for task and agent sweeps, locally or on SLURM."""

import collections
import functools
import json
import multiprocessing
import multiprocessing.connection
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Callable, Optional, Sequence
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


#: ``agent.hostname=auto``: find the node serving the agent's model.
AUTO_HOST = "auto"


def running_job_nodes() -> list[str]:
    """The nodes of this user's running SLURM jobs."""
    listing = subprocess.run(
        ["squeue", "--me", "-t", "R", "-h", "-o", "%N"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    nodes: set[str] = set()
    for nodelist in filter(None, (line.strip() for line in listing.splitlines())):
        expanded = subprocess.run(
            ["scontrol", "show", "hostnames", nodelist],
            capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
        nodes.update(expanded.split())
    return sorted(nodes)


def serves_model(
    host: str, port: str | int, model: str, api_key: Optional[str] = None, timeout: float = 5
) -> bool:
    """Whether ``http://host:port/v1/models`` lists ``model``. Probed
    directly: a proxy in the environment cannot reach cluster nodes."""
    request = urllib.request.Request(f"http://{host}:{port}/v1/models")
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            listed = json.load(response).get("data") or []
    except (OSError, ValueError, AttributeError):
        return False
    return any(isinstance(m, dict) and m.get("id") == model for m in listed)


def find_vllm_host(
    model: str,
    port: str | int = 8000,
    api_key: Optional[str] = None,
    nodes: Optional[Sequence[str]] = None,
    probe: Callable[..., bool] = serves_model,
) -> str:
    """The first of this user's running job nodes serving ``model`` on
    ``port``. It probes what each node serves, so it does not matter how the
    vLLM job was started."""
    if nodes is None:
        try:
            nodes = running_job_nodes()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"agent.hostname=auto needs SLURM's squeue: {exc}") from exc
    own = socket.gethostname().split(".")[0]
    for node in [n for n in nodes if n != own] + [n for n in nodes if n == own]:
        if probe(node, port, model, api_key):
            return node
    raise RuntimeError(
        f"agent.hostname=auto: none of your running jobs ({', '.join(nodes) or 'none'}) "
        f"serves {model!r} on port {port}. Is the vLLM job running (squeue --me)? "
        "Or set agent.hostname=<node>."
    )


def resolve_auto_hostnames(
    jobs: Sequence[DictConfig], find: Callable[..., str] = find_vllm_host
) -> dict[tuple[str, str], str]:
    """Point every job whose agent says ``hostname: auto`` at the node serving
    its model, one lookup per (model, port). Returns what was found."""
    found: dict[tuple[str, str], str] = {}
    for job in jobs:
        agent = job.get("agent")
        if agent is None or agent.get("hostname") != AUTO_HOST:
            continue
        key = (str(agent.get("model_name")), str(agent.get("port") or 8000))
        if key not in found:
            found[key] = find(key[0], key[1], agent.get("api_key"))
        with open_dict(agent):
            agent.hostname = found[key]
    return found


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
        try:
            for job_config in parallel_configs:
                print(f"\n=== Running local job {job_config.job_id + 1}/{num_jobs} ===")
                run_task(job_config, launcher_class)
        except KeyboardInterrupt:
            # The job's own `finally` has stopped its web app; the rest never start.
            print("Sweep stopped by Ctrl-C.", file=sys.stderr)
            raise SystemExit(130) from None
        print(f"\nFinished {num_jobs} local jobs.")
        return
    print(f"Running {num_jobs} local jobs, {max_parallel} at a time; each logs to <job>/{RUN_LOG}")
    task = functools.partial(run_task, launcher_class=launcher_class)
    try:
        failures = run_concurrently(parallel_configs, max_parallel, task)
    except KeyboardInterrupt:
        # The jobs are already stopped; spare the user a traceback.
        print("Sweep stopped by Ctrl-C.", file=sys.stderr)
        raise SystemExit(130) from None
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

    Each job runs in a fresh spawned process:

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

    A plain process per job rather than a ``ProcessPoolExecutor``, so Ctrl-C
    can stop the sweep: the executor queues a job beyond its workers that can
    no longer be cancelled, replaces a worker that exits, and its own shutdown
    waits for every queued job to run. Here a job that has not started is just
    a list entry. On Ctrl-C (or any error in this loop) no further job starts,
    and the running ones are stopped -- see :func:`_stop_jobs`.
    """
    context = multiprocessing.get_context("spawn")
    queued = collections.deque(configs)
    running: dict = {}
    failures: list[DictConfig] = []
    interrupts = _Interrupts()
    previous = interrupts.install()
    try:
        while queued or running:
            while queued and len(running) < max_parallel:
                config = queued.popleft()
                process = context.Process(
                    target=_run_job_process, args=(task, config), name=f"job-{config.job_id}"
                )
                process.start()
                running[process] = config
            finished = multiprocessing.connection.wait([p.sentinel for p in running])
            for process in [p for p in running if p.sentinel in finished]:
                process.join()
                config = running.pop(process)
                log_path = Path(config.logs_dir) / RUN_LOG
                if process.exitcode == 0:
                    status = "ok"
                else:
                    status = f"FAILED (exit {process.exitcode})"
                    failures.append(config)
                print(f"[done] job {config.job_id} {config.task_name}: {status} -> {log_path}", flush=True)
    except BaseException:
        _stop_jobs(list(running), len(queued), interrupts)
        raise
    finally:
        interrupts.restore(previous)
    return failures


#: Repeat SIGINTs within this many seconds of the first are the same Ctrl-C.
#: ``uv run`` forwards the SIGINT its child already got from the terminal, so
#: one keypress arrives twice; a later one is the user asking to kill now.
SAME_CTRL_C_S = 1.0


class _Interrupts:
    """SIGINT handler for the sweep's loop: the first raises KeyboardInterrupt,
    an echo of it is ignored, and a later one asks :func:`_stop_jobs` to kill."""

    def __init__(self) -> None:
        self.first: Optional[float] = None
        self.kill_now = False

    def __call__(self, _signum, _frame) -> None:
        now = time.monotonic()
        if self.first is None:
            self.first = now
            raise KeyboardInterrupt
        if now - self.first > SAME_CTRL_C_S:
            self.kill_now = True

    def install(self):
        try:
            return signal.signal(signal.SIGINT, self)
        except ValueError:  # not the main thread: leave signals alone
            return None

    def restore(self, previous) -> None:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


#: Seconds a running job gets to clean up (stop its web app) after Ctrl-C
#: before it is killed. ``stop_process_group`` alone allows 10 s + 10 s.
STOP_GRACE_S = 30.0


def _run_job_process(task: Callable[[DictConfig], None], config: DictConfig) -> None:
    """Body of one job's process: exit 0 if ``task`` succeeded, 1 if it raised.

    Ctrl-C belongs to the parent. A terminal Ctrl-C signals the whole
    foreground process group, so every job would otherwise raise its own
    KeyboardInterrupt at an arbitrary point. Jobs swallow SIGINT instead (with
    a Python handler rather than ``SIG_IGN``, which every process a job starts
    would inherit), and the parent stops them with SIGTERM, which becomes
    ``SystemExit``: it unwinds through ``AgentLauncher.launch``'s ``finally``,
    so the job's web app is stopped rather than orphaned.
    """
    signal.signal(signal.SIGINT, lambda *_: None)
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    sys.exit(0 if _run_logged(task, config) is None else 1)


def _exit_on_sigterm(signum, _frame) -> None:
    # Once only: a second SIGTERM must not interrupt the cleanup the first
    # one started. A job that hangs anyway is killed after STOP_GRACE_S.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise SystemExit(128 + signum)


def _stop_jobs(
    processes: list, queued: int, interrupts: Optional[_Interrupts] = None, grace: float = STOP_GRACE_S
) -> None:
    """SIGTERM the running jobs and wait ``grace`` seconds for them to clean up.

    A job still alive after that, or every job on a second Ctrl-C, is killed.
    """
    print(
        f"\nInterrupted: cancelled {queued} queued job(s), stopping {len(processes)} "
        f"running (Ctrl-C again to kill them now)...",
        file=sys.stderr,
        flush=True,
    )
    for process in processes:
        process.terminate()
    deadline = time.monotonic() + grace
    try:
        while any(p.is_alive() for p in processes) and time.monotonic() < deadline:
            if interrupts is not None and interrupts.kill_now:
                break
            multiprocessing.connection.wait([p.sentinel for p in processes if p.is_alive()], timeout=0.2)
    except KeyboardInterrupt:  # no handler installed (not the main thread)
        pass
    for process in processes:
        if process.is_alive():
            process.kill()
        process.join()


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
    for (model, port), host in resolve_auto_hostnames(jobs).items():
        print(f"agent.hostname=auto: {model} is served on {host}:{port}")
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
