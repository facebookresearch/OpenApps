"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Run a frozen suite (default ``align_v1``) across many models.

The models are the entries of ``scripts/benchmark_models.yaml``. They are
planned into *launch groups*, each one ``launch_parallel_agents.py`` sweep
using the launcher's native agent sweep (``agents=a,b``):

* every ``vllm`` model with the same ``hf_model`` shares one server (e.g. the
  two gemma-4-31B prompt variants): submit ``scripts/serve_vllm.sbatch`` with
  the registry's GPU count, wait until ``http://<node>:<port>/v1/models``
  serves it, run the group's sweep with ``agent.hostname=<node>``, wait for
  its jobs, ``scancel`` the server;
* the ``api`` and ``none`` (``dummy``) models form one group: check their
  environment variables, run the sweep.

Each group's runs land in ``<logs-root>/<group>/run-*/<job_id>/``; ``collect``
then packs and validates each agent's runs and previews the whole root.

    uv run scripts/benchmark_sweep.py list
    uv run scripts/benchmark_sweep.py run dummy --mode local --collect
    uv run scripts/benchmark_sweep.py run vllm --gpu-type h200 --parallel 4 --dry-run
    uv run scripts/benchmark_sweep.py submit-one gemma-4-31B-computer-use --gpu-type h200
    uv run scripts/benchmark_sweep.py collect log_outputs/align_v1-<stamp>

Nothing here talks to a model: it builds commands, submits jobs and waits.
``--dry-run`` prints every command instead of running it. See
docs/benchmarking.md.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import IO, Mapping, Optional, Sequence

import yaml

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "scripts" / "benchmark_models.yaml"
SERVE_SBATCH = REPO / "scripts" / "serve_vllm.sbatch"
ONE_SBATCH = REPO / "scripts" / "benchmark_one.sbatch"
INTERNAL_MODE = REPO / "config" / "mode" / "internal-slurm_cluster.yaml"
#: Where serve_vllm.sbatch's ``#SBATCH --output`` writes.
SERVE_LOG_DIR = Path("/storage/home") / os.environ.get("USER", "user") / "logs"

KINDS = ("none", "api", "vllm")
GPU_TYPES = ("h100", "h200")
SUITE = "align_v1"
DEFAULT_PORT = 8000
#: The key ``serve_vllm.sbatch`` starts vLLM with and every vLLM agent config
#: sends (``${oc.env:VLLM_API_KEY,AMI_RULZ}`` in config/agent/default.yaml).
DEFAULT_VLLM_API_KEY = "AMI_RULZ"

#: ``--smoke``: a two-run ad-hoc sweep (no ``benchmark=``) on the reference
#: cell, under the suite's termination rule, with a short step budget. It
#: proves the model is served, answers and emits parseable actions, for
#: minutes of GPU time. Its records carry no suite, so ``collect`` previews
#: them but does not pack them.
SMOKE_OVERRIDES = (
    "parallel_tasks.task_names=[add_call_mom_to_my_todo,message_bob_to_meet]",
    "parallel_tasks.app_variations=[['apps/theme=default']]",
    "task_termination=on_agent_stop",
    "browsergym_env_args.max_steps=10",
)


# --- registry ------------------------------------------------------------------


@dataclass(frozen=True)
class Model:
    """One ``benchmark_models.yaml`` entry, defaults applied."""

    agent: str
    kind: str
    hf_model: Optional[str] = None
    env: tuple[str, ...] = ()
    gpus: Mapping[str, int] = field(default_factory=dict)
    max_model_len: int = 32768
    gpu_memory_utilization: float = 0.90
    shards: int = 8
    vllm_args: tuple[str, ...] = ()
    params_b: Optional[float] = None

    @property
    def needs_server(self) -> bool:
        return self.kind == "vllm"

    def tensor_parallel(self, gpu_type: str) -> int:
        """Recommended GPU count (= tensor-parallel size) on ``gpu_type``."""
        if not self.needs_server:
            return 0
        if gpu_type not in self.gpus:
            raise ValueError(f"{self.agent}: no GPU count for {gpu_type!r}")
        return int(self.gpus[gpu_type])

    def missing_env(self, environ: Mapping[str, str]) -> list[str]:
        return [name for name in self.env if not environ.get(name)]

    def serve_env(self, gpus: int) -> dict[str, str]:
        """What ``serve_vllm.sbatch`` / ``benchmark_one.sbatch`` read."""
        if not self.needs_server:
            return {}
        return {
            "VLLM_MODEL": str(self.hf_model),
            "GPUS": str(gpus),
            "VLLM_MAX_MODEL_LEN": str(self.max_model_len),
            "VLLM_GPU_MEM_UTIL": str(self.gpu_memory_utilization),
            "VLLM_EXTRA_ARGS": " ".join(self.vllm_args),
        }


_MODEL_KEYS = {f.name for f in fields(Model)} - {"agent"}


def parse_registry(data: Mapping) -> dict[str, Model]:
    """Validate and expand the registry YAML (already loaded)."""
    defaults = dict(data.get("defaults") or {})
    models = data.get("models") or {}
    if not models:
        raise ValueError("registry has no models")
    out: dict[str, Model] = {}
    for agent, raw in models.items():
        entry = {**defaults, **(raw or {})}
        unknown = set(entry) - _MODEL_KEYS
        if unknown:
            raise ValueError(f"{agent}: unknown keys {sorted(unknown)}")
        kind = entry.get("kind")
        if kind not in KINDS:
            raise ValueError(f"{agent}: kind must be one of {KINDS}, got {kind!r}")
        gpus = {str(k): int(v) for k, v in (entry.get("gpus") or {}).items()}
        if kind == "vllm":
            if not entry.get("hf_model"):
                raise ValueError(f"{agent}: a vllm model needs hf_model")
            missing = [g for g in GPU_TYPES if g not in gpus]
            if missing:
                raise ValueError(f"{agent}: gpus is missing {missing}")
            if any(n < 1 for n in gpus.values()):
                raise ValueError(f"{agent}: gpu counts must be >= 1")
        out[str(agent)] = Model(
            agent=str(agent),
            kind=kind,
            hf_model=entry.get("hf_model"),
            env=tuple(entry.get("env") or ()),
            gpus=gpus,
            max_model_len=int(entry.get("max_model_len", 32768)),
            gpu_memory_utilization=float(entry.get("gpu_memory_utilization", 0.90)),
            shards=int(entry.get("shards", 8)),
            vllm_args=tuple(str(a) for a in entry.get("vllm_args") or ()),
            params_b=entry.get("params_b"),
        )
    return out


def load_registry(path: Path = REGISTRY) -> dict[str, Model]:
    with open(path) as fh:
        return parse_registry(yaml.safe_load(fh))


def select(registry: Mapping[str, Model], names: Sequence[str]) -> list[Model]:
    """Agent names (comma lists allowed), or the groups ``all`` / ``vllm`` /
    ``api`` (``all`` skips ``dummy``). Order is kept, duplicates dropped."""
    picked: list[Model] = []
    for name in [n for arg in names for n in arg.split(",") if n]:
        if name == "all":
            group = [m for m in registry.values() if m.kind != "none"]
        elif name in ("vllm", "api"):
            group = [m for m in registry.values() if m.kind == name]
        elif name in registry:
            group = [registry[name]]
        else:
            raise SystemExit(
                f"unknown model {name!r}; known: {', '.join(registry)} "
                "(or all / vllm / api). Add it to scripts/benchmark_models.yaml."
            )
        picked += [m for m in group if m not in picked]
    return picked


@dataclass(frozen=True)
class Group:
    """Models run by one ``launch_parallel_agents.py`` sweep (and, for vLLM,
    served by one server)."""

    label: str
    models: tuple[Model, ...]

    @property
    def agents(self) -> list[str]:
        return [m.agent for m in self.models]

    @property
    def server(self) -> Optional[Model]:
        """The model whose ``serve_env`` starts the shared server, if any."""
        return self.models[0] if self.models[0].needs_server else None

    @property
    def shards(self) -> int:
        return max(m.shards for m in self.models)


def plan_groups(models: Sequence[Model], separate: bool = False) -> list[Group]:
    """One group per served checkpoint, plus one for everything hosted
    elsewhere. ``separate``: one group per model (no shared launches)."""
    if separate:
        return [Group(m.agent, (m,)) for m in models]
    by_checkpoint: dict[str, list[Model]] = {}
    hosted: list[Model] = []
    for m in models:
        if m.needs_server:
            by_checkpoint.setdefault(str(m.hf_model), []).append(m)
        else:
            hosted.append(m)
    groups = []
    for checkpoint, members in by_checkpoint.items():
        label = members[0].agent if len(members) == 1 else checkpoint.replace("/", "__")
        groups.append(Group(label, tuple(members)))
    if hosted:
        label = hosted[0].agent if len(hosted) == 1 else "hosted"
        groups.append(Group(label, tuple(hosted)))
    return groups


# --- SLURM settings ---------------------------------------------------------------


@dataclass(frozen=True)
class Slurm:
    account: Optional[str] = None
    qos: Optional[str] = None
    partition: Optional[str] = None
    #: Wall clock for a serve / single-node job, minutes.
    time_min: int = 1440

    def sbatch_flags(self) -> list[str]:
        flags = [
            f"--{name}={value}"
            for name, value in (
                ("account", self.account),
                ("qos", self.qos),
                ("partition", self.partition),
            )
            if value
        ]
        return flags + [f"--time={self.time_min}"]


def slurm_settings(
    environ: Mapping[str, str] = os.environ, internal_mode: Path = INTERNAL_MODE
) -> Slurm:
    """``OPENAPPS_SLURM_{ACCOUNT,QOS,PARTITION,TIME}`` win, then sbatch's own
    ``SBATCH_*``, then ``config/mode/internal-slurm_cluster.yaml`` (the
    gitignored site config the CPU sweep already uses), else unset."""
    internal: dict = {}
    if internal_mode.exists():
        with open(internal_mode) as fh:
            internal = (yaml.safe_load(fh) or {}).get("slurm_sweep_launcher") or {}

    def pick(name: str) -> Optional[str]:
        return (
            environ.get(f"OPENAPPS_SLURM_{name.upper()}")
            or environ.get(f"SBATCH_{name.upper()}")
            or internal.get(f"slurm_{name}")
            or None
        )

    return Slurm(
        account=pick("account"),
        qos=pick("qos"),
        partition=pick("partition"),
        time_min=int(environ.get("OPENAPPS_SLURM_TIME", 1440)),
    )


def default_mode(internal_mode: Path = INTERNAL_MODE) -> str:
    return "internal-slurm_cluster" if internal_mode.exists() else "local"


def is_slurm_mode(mode: str) -> bool:
    return "slurm" in mode


# --- command construction (pure) ------------------------------------------------------


def group_logs_dir(logs_root: Path, label: str) -> str:
    """A sweep-level root: the launcher adds ``run-*/<job_id>/`` below it."""
    return f"{Path(logs_root).resolve()}/{label}"


def launch_command(
    group: Group,
    *,
    mode: str,
    logs_root: Path,
    hostname: Optional[str] = None,
    port: int = DEFAULT_PORT,
    seed_set: str = "public",
    suite: str = SUITE,
    shard: Optional[tuple[int, int]] = None,
    smoke: bool = False,
    extra: Sequence[str] = (),
) -> list[str]:
    """The ``launch_parallel_agents.py`` command for one group (one shard)."""
    cmd = ["uv", "run", "launch_parallel_agents.py"]
    if smoke:
        cmd += list(SMOKE_OVERRIDES)
    else:
        cmd += [f"benchmark={suite}", f"benchmark.seed_set={seed_set}"]
    agents = group.agents
    cmd.append(f"agent={agents[0]}" if len(agents) == 1 else f"agents={','.join(agents)}")
    cmd.append(f"mode={mode}")
    if group.server is not None:
        if not hostname:
            raise ValueError(f"{group.label} is self-hosted: pass the serving node")
        cmd += [f"agent.hostname={hostname}", f"agent.port={port}"]
    cmd.append(f"logs_dir={group_logs_dir(logs_root, group.label)}")
    if shard is not None:
        index, count = shard
        cmd += [f"shard_index={index}", f"num_shards={count}", "continue_on_error=true"]
    # The sweep previews once at the end (collect), not once per group.
    cmd.append("open_leaderboard=false")
    return cmd + list(extra)


def _export(env: Mapping[str, str]) -> str:
    # sbatch --export splits on commas, so a value cannot contain one.
    bad = [k for k, v in env.items() if "," in v]
    if bad:
        raise ValueError(f"--export values cannot contain commas: {bad}")
    return ",".join(["ALL"] + [f"{k}={v}" for k, v in env.items() if v != ""])


def serve_submit_command(
    model: Model,
    *,
    gpus: int,
    slurm: Slurm,
    port: int = DEFAULT_PORT,
    job_name: Optional[str] = None,
) -> list[str]:
    """``sbatch`` for ``serve_vllm.sbatch``; settings go through ``--export``."""
    env = {**model.serve_env(gpus), "VLLM_PORT": str(port)}
    return [
        "sbatch",
        "--parsable",
        f"--job-name={job_name or 'openapps-vllm-' + model.agent}",
        f"--gpus-per-node={gpus}",
        *slurm.sbatch_flags(),
        f"--export={_export(env)}",
        str(SERVE_SBATCH),
    ]


def one_submit_command(
    group: Group,
    *,
    gpus: int,
    slurm: Slurm,
    logs_root: Optional[Path] = None,
    seed_set: str = "public",
    shards: Optional[int] = None,
    smoke: bool = False,
    extra: Sequence[str] = (),
) -> list[str]:
    """``sbatch`` for ``benchmark_one.sbatch``: serve + suite on one node.
    The group's agents are space-separated in ``AGENT`` (no commas: see
    ``_export``); extra Hydra overrides go after the script, as its args."""
    env = {"AGENT": " ".join(group.agents), "SEED_SET": seed_set}
    if logs_root is not None:
        env["LOGS_ROOT"] = str(Path(logs_root).resolve())
    if shards:
        env["SHARDS"] = str(shards)
    if smoke:
        env["SMOKE"] = "1"
    return [
        "sbatch",
        "--parsable",
        f"--job-name=openapps-{SUITE}-{group.label}",
        f"--gpus-per-node={max(gpus, 0)}",
        *slurm.sbatch_flags(),
        f"--export={_export(env)}",
        str(ONE_SBATCH),
        *extra,
    ]


def job_dirs_by_agent(logs_root: Path) -> dict[str, list[Path]]:
    """Every job directory under a sweep root (``.../run-*/<job_id>/`` with a
    ``config.yaml``), keyed by the agent config it ran."""
    found: dict[str, list[Path]] = {}
    for cfg_path in sorted(Path(logs_root).rglob("config.yaml")):
        job = cfg_path.parent
        if not (job.name.isdigit() and job.parent.name.startswith("run-")):
            continue
        try:
            with open(cfg_path) as fh:
                cfg = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        agent = cfg.get("agent_config_name") or (cfg.get("agent") or {}).get(
            "model_pretty_name"
        )
        if agent:
            found.setdefault(str(agent), []).append(job)
    return found


def collect_commands(
    logs_root: Path,
    jobs: Mapping[str, Sequence[Path]],
    *,
    org: str = "local",
    suite: str = SUITE,
    smoke: bool = False,
) -> list[list[str]]:
    """pack + validate per agent (from its job dirs), then one preview."""
    root = Path(logs_root).resolve()
    cmds: list[list[str]] = []
    if not smoke:
        for agent, dirs in jobs.items():
            bundle = root / "_bundles" / f"{org}__{agent}__{suite}"
            cmds.append(
                ["uv", "run", "-m", "open_apps.leaderboard.pack", *map(str, dirs),
                 "--org", org, "--agent", agent, "--suite", suite, "--out", str(bundle)]
            )
            cmds.append(["uv", "run", "-m", "open_apps.leaderboard.validate", str(bundle)])
    cmds.append(
        ["uv", "run", "-m", "open_apps.leaderboard.preview", str(root),
         "--out", str(root / "leaderboard.html")]
    )
    return cmds


_SUBMITTED = re.compile(r"Submitted \d+ SLURM jobs?:\s*(\d+)")


def parse_array_id(output: str) -> Optional[str]:
    """The SLURM job (array) id ``launch_parallel_agents.py`` printed
    (``Submitted 155 SLURM jobs: 4242_0, 4242_1, ...`` -> ``4242``)."""
    match = _SUBMITTED.search(output)
    return match.group(1) if match else None


def fmt(cmd: Sequence[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


# --- execution ------------------------------------------------------------------------


class Runner:
    """Runs (or, with ``dry_run``, prints) commands; one per group, so groups
    running concurrently write to their own log."""

    def __init__(self, dry_run: bool, out: Optional[IO[str]] = None, label: str = ""):
        self.dry_run = dry_run
        self.out = out if out is not None else sys.stdout
        self.label = label
        self._lock = threading.Lock()

    def say(self, msg: str) -> None:
        with self._lock:
            print(f"[{self.label}] {msg}" if self.label else msg, file=self.out, flush=True)

    def run(self, cmd: Sequence[str], capture: bool = False, check: bool = True) -> str:
        self.say(f"+ {fmt(cmd)}")
        if self.dry_run:
            return ""
        if capture:
            proc = subprocess.run(cmd, cwd=REPO, text=True, capture_output=True)
            self.out.write(proc.stdout or "")
            self.out.write(proc.stderr or "")
            self.out.flush()
        else:
            self.out.flush()
            proc = subprocess.run(cmd, cwd=REPO, stdout=self.out, stderr=subprocess.STDOUT)
        if check and proc.returncode != 0:
            raise RuntimeError(f"exit {proc.returncode}: {fmt(cmd)}")
        return (proc.stdout or "") if capture else ""

    def run_parallel(self, cmds: Sequence[Sequence[str]], logs: Sequence[Path]) -> int:
        """Start every command at once (local shards); wait; count failures."""
        for cmd, log in zip(cmds, logs):
            self.say(f"+ {fmt(cmd)} > {log}")
        if self.dry_run:
            return 0
        procs = []
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}  # logs can be tail -f'd
        for cmd, log in zip(cmds, logs):
            log.parent.mkdir(parents=True, exist_ok=True)
            fh = open(log, "w")
            proc = subprocess.Popen(
                cmd, cwd=REPO, stdout=fh, stderr=subprocess.STDOUT, env=env
            )
            procs.append((proc, fh))
        failed = 0
        for proc, fh in procs:
            failed += proc.wait() != 0
            fh.close()
        return failed


def serving(node: str, port: int, hf_model: str, api_key: str) -> bool:
    """Does ``node:port`` answer ``/v1/models`` with ``hf_model``?"""
    # Compute nodes are cluster-internal; an inherited (sandbox) proxy would
    # turn every probe into a timeout.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        f"http://{node}:{port}/v1/models", headers={"Authorization": f"Bearer {api_key}"}
    )
    try:
        with opener.open(req, timeout=5) as resp:
            return hf_model in resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return False


def squeue_state(job_id: str) -> tuple[str, str]:
    """``(state, nodelist)`` of a job, ``("", "")`` once it left the queue."""
    proc = subprocess.run(
        ["squeue", "-h", "-j", job_id, "-o", "%T %N"], text=True, capture_output=True
    )
    lines = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not lines:
        return "", ""
    state, _, nodes = lines[0].partition(" ")
    return state, nodes.strip()


def wait_for_server(
    runner: Runner,
    job_id: str,
    model: Model,
    port: int,
    api_key: str,
    timeout_s: int,
    poll_s: int = 30,
) -> str:
    """Block until the serve job runs and its node answers ``/v1/models`` with
    the model; return the node. Fails fast when the job dies."""
    deadline = time.time() + timeout_s
    log = SERVE_LOG_DIR / f"openapps-vllm-{model.agent}_{job_id}.out"
    while time.time() < deadline:
        state, nodes = squeue_state(job_id)
        if not state:
            raise RuntimeError(f"serve job {job_id} ended before serving; see {log}")
        node = nodes.split(",")[0] if state == "RUNNING" else ""
        if node and serving(node, port, str(model.hf_model), api_key):
            runner.say(f"{model.hf_model} is up on {node}:{port}")
            return node
        runner.say(f"waiting for serve job {job_id} ({state} {node}); log {log}")
        time.sleep(poll_s)
    raise TimeoutError(f"{model.hf_model} not served after {timeout_s}s (job {job_id})")


def wait_for_jobs(runner: Runner, job_id: str, poll_s: int = 60) -> None:
    """Block until every task of a SLURM job (array) has left the queue."""
    while True:
        proc = subprocess.run(["squeue", "-h", "-j", job_id], text=True, capture_output=True)
        remaining = len([ln for ln in (proc.stdout or "").splitlines() if ln.strip()])
        if proc.returncode != 0 or remaining == 0:
            runner.say(f"sweep job {job_id} finished")
            return
        runner.say(f"sweep job {job_id}: {remaining} task(s) queued or running")
        time.sleep(poll_s)


@dataclass
class SweepOptions:
    mode: str
    logs_root: Path
    gpu_type: str = "h200"
    gpus: Optional[int] = None
    port: int = DEFAULT_PORT
    seed_set: str = "public"
    shards: Optional[int] = None
    smoke: bool = False
    hostname: Optional[str] = None
    skip_env_check: bool = False
    server_timeout_s: int = 5400
    slurm: Slurm = field(default_factory=Slurm)
    extra: tuple[str, ...] = ()


def run_group(group: Group, opts: SweepOptions, runner: Runner) -> None:
    """The whole pipeline for one group; raises on failure, always tears the
    server down."""
    if not opts.skip_env_check:
        missing = sorted({v for m in group.models for v in m.missing_env(os.environ)})
        if missing and not runner.dry_run:
            raise RuntimeError(f"set {', '.join(missing)} (shell or .env), or --skip-env-check")
        if missing:
            runner.say(f"note: {', '.join(missing)} unset (a real run would stop here)")
    server_job: Optional[str] = None
    hostname = opts.hostname
    model = group.server
    try:
        if model is not None and not hostname:
            gpus = opts.gpus or model.tensor_parallel(opts.gpu_type)
            out = runner.run(
                serve_submit_command(model, gpus=gpus, slurm=opts.slurm, port=opts.port),
                capture=True,
            )
            if runner.dry_run:
                server_job, hostname = "<serve-job>", "<node>"
                runner.say(f"(would wait for http://<node>:{opts.port}/v1/models)")
            else:
                server_job = out.strip().split(";")[0]
                api_key = os.environ.get("VLLM_API_KEY", DEFAULT_VLLM_API_KEY)
                hostname = wait_for_server(
                    runner, server_job, model, opts.port, api_key, opts.server_timeout_s
                )
        common = dict(
            mode=opts.mode, logs_root=opts.logs_root, hostname=hostname, port=opts.port,
            seed_set=opts.seed_set, smoke=opts.smoke, extra=opts.extra,
        )
        if is_slurm_mode(opts.mode):
            out = runner.run(launch_command(group, **common), capture=True)
            array = parse_array_id(out)
            if runner.dry_run:
                runner.say("(would wait for the sweep's job array, then scancel the server)")
            elif array:
                wait_for_jobs(runner, array)
            else:
                raise RuntimeError("no 'Submitted N SLURM jobs' line in the launcher output")
        else:
            n = opts.shards or group.shards
            cmds = [launch_command(group, shard=(k, n), **common) for k in range(n)]
            log_dir = Path(opts.logs_root).resolve() / "_sweep"
            logs = [log_dir / f"{group.label}.shard{k}.log" for k in range(n)]
            failed = runner.run_parallel(cmds, logs)
            if failed:
                runner.say(f"{failed}/{n} shard(s) exited non-zero; see {log_dir}")
    finally:
        if server_job:
            runner.run(["scancel", server_job], check=False)


def run_collect(logs_root: Path, runner: Runner, org: str, smoke: bool,
                agents: Optional[Sequence[str]] = None) -> int:
    """pack/validate/preview; returns the number of failed steps."""
    if runner.dry_run:
        jobs = {a: [Path(logs_root).resolve() / "<group>" / "run-*" / "<job_id>"]
                for a in agents or ()}
    else:
        jobs = job_dirs_by_agent(logs_root)
        if agents:
            jobs = {a: d for a, d in jobs.items() if a in agents}
    failures = 0
    for cmd in collect_commands(logs_root, jobs, org=org, smoke=smoke):
        try:
            runner.run(cmd)
        except RuntimeError as exc:
            failures += 1
            runner.say(f"FAILED: {exc}")
    root = Path(logs_root).resolve()
    runner.say(f"Leaderboard: {root / 'leaderboard.html'}; serve it with\n"
               f"  uv run -m open_apps.leaderboard.preview {root}")
    return failures


# --- CLI ------------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    print(f"{'agent':34} {'kind':5} {'H100':>4} {'H200':>4}  model / env")
    for m in registry.values():
        if m.needs_server:
            print(f"{m.agent:34} {m.kind:5} {m.gpus['h100']:>4} {m.gpus['h200']:>4}  {m.hf_model}")
        else:
            missing = m.missing_env(os.environ)
            mark = f"  (unset: {', '.join(missing)})" if missing else ""
            print(f"{m.agent:34} {m.kind:5} {'-':>4} {'-':>4}  {', '.join(m.env) or '-'}{mark}")
    return 0


def cmd_env(args: argparse.Namespace) -> int:
    """Shell exports for one launch group (eval'd by benchmark_one.sbatch)."""
    models = select(load_registry(args.registry), args.agents)
    groups = plan_groups(models)
    if len(groups) != 1:
        raise SystemExit(
            f"{' '.join(args.agents)} need {len(groups)} servers/launches; "
            "benchmark_one.sbatch runs one (agents sharing one checkpoint, or hosted ones)"
        )
    group = groups[0]
    lines = {
        "MODEL_KIND": "vllm" if group.server else "hosted",
        "AGENTS_SELECTOR": (
            f"agent={group.agents[0]}" if len(group.agents) == 1
            else f"agents={','.join(group.agents)}"
        ),
        "GROUP_LABEL": group.label,
        "SHARDS_DEFAULT": str(group.shards),
    }
    if group.server is not None:
        recommended = group.server.tensor_parallel(args.gpu_type)
        gpus = args.gpus or recommended
        if gpus < recommended:
            print(
                f"warning: {group.server.hf_model} is sized for {recommended}x "
                f"{args.gpu_type.upper()}, running on {gpus}: expect OOM or a tiny KV cache",
                file=sys.stderr,
            )
        lines.update(group.server.serve_env(gpus))
    lines["MISSING_ENV"] = " ".join(
        sorted({v for m in group.models for v in m.missing_env(os.environ)})
    )
    for key, value in lines.items():
        print(f"export {key}={shlex.quote(value)}")
    return 0


def _opts(args: argparse.Namespace) -> SweepOptions:
    return SweepOptions(
        mode=args.mode,
        logs_root=Path(args.logs_root),
        gpu_type=args.gpu_type,
        gpus=args.gpus,
        port=args.port,
        seed_set=args.seed_set,
        shards=args.shards,
        smoke=args.smoke,
        hostname=args.hostname,
        skip_env_check=args.skip_env_check,
        server_timeout_s=args.server_timeout * 60,
        slurm=slurm_settings(),
        extra=tuple(args.overrides),
    )


def cmd_run(args: argparse.Namespace) -> int:
    groups = plan_groups(select(load_registry(args.registry), args.models), args.separate)
    if args.hostname and len([g for g in groups if g.server]) > 1:
        raise SystemExit("--hostname points at one server: pass models sharing one checkpoint")
    opts = _opts(args)
    print(f"{len(groups)} launch group(s) -> {opts.logs_root.resolve()} (mode={opts.mode})")
    for g in groups:
        print(f"  {g.label}: {', '.join(g.agents)}")
    failures: dict[str, str] = {}

    def attempt(group: Group, runner: Runner) -> None:
        try:
            run_group(group, opts, runner)
        except Exception as exc:  # noqa: BLE001 -- one group never stops the rest
            failures[group.label] = repr(exc)
            runner.say(f"FAILED: {exc!r}")

    def one(group: Group) -> None:
        if args.parallel > 1 and not args.dry_run:
            log = opts.logs_root.resolve() / "_sweep" / f"{group.label}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            print(f"[{group.label}] logging to {log}")
            with open(log, "a") as fh:
                attempt(group, Runner(False, fh, group.label))
        else:
            attempt(group, Runner(args.dry_run, sys.stdout, group.label))

    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        list(pool.map(one, groups))
    if args.collect:
        agents = [a for g in groups for a in g.agents]
        run_collect(opts.logs_root, Runner(args.dry_run), args.org, args.smoke, agents)
    for label, err in failures.items():
        print(f"FAILED {label}: {err}", file=sys.stderr)
    return 1 if failures else 0


def cmd_submit_one(args: argparse.Namespace) -> int:
    runner = Runner(args.dry_run)
    slurm = slurm_settings()
    for group in plan_groups(select(load_registry(args.registry), args.models), args.separate):
        gpus = args.gpus or (group.server.tensor_parallel(args.gpu_type) if group.server else 0)
        runner.run(
            one_submit_command(
                group, gpus=gpus, slurm=slurm, logs_root=Path(args.logs_root),
                seed_set=args.seed_set, shards=args.shards, smoke=args.smoke,
                extra=args.overrides,
            )
        )
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    failures = run_collect(Path(args.logs_root), Runner(args.dry_run), args.org,
                           args.smoke, args.agents)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    p = argparse.ArgumentParser(
        description="Run the align_v1 suite across many models (docs/benchmarking.md).",
    )
    p.add_argument("--registry", type=Path, default=REGISTRY)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show the registry").set_defaults(func=cmd_list)

    env = sub.add_parser("env", help="shell exports for one launch group (benchmark_one.sbatch)")
    env.add_argument("agents", nargs="+")
    env.add_argument("--gpu-type", choices=GPU_TYPES, default="h200")
    env.add_argument("--gpus", type=int, help="GPUs actually allocated (default: recommended)")
    env.set_defaults(func=cmd_env)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("models", nargs="+", help="agent names, or all / vllm / api")
        sp.add_argument("--gpu-type", choices=GPU_TYPES,
                        default=os.environ.get("OPENAPPS_GPU_TYPE", "h200"))
        sp.add_argument("--gpus", type=int, help="override the registry's GPU count")
        sp.add_argument("--seed-set", choices=("public", "heldout"), default="public")
        sp.add_argument("--shards", type=int, help="concurrent local episodes per group")
        sp.add_argument("--smoke", action="store_true",
                        help="a 2-run ad-hoc sweep per group instead of the suite")
        sp.add_argument("--separate", action="store_true",
                        help="one launch per model (no shared servers or agents= sweeps)")
        sp.add_argument("--logs-root", default=str(REPO / "log_outputs" / f"{SUITE}-{stamp}"))
        sp.add_argument("--dry-run", action="store_true", help="print commands, run nothing")
        sp.add_argument("--overrides", nargs="*", default=[], metavar="KEY=VALUE",
                        help="extra Hydra overrides for every launch (e.g. use_wandb=True)")

    run = sub.add_parser("run", help="serve (if needed) and run the suite for each group")
    common(run)
    run.add_argument("--mode", default=default_mode(),
                     help="config/mode/<mode>: local (shards on this machine) or a "
                          "slurm mode (default: %(default)s)")
    run.add_argument("--parallel", type=int, default=1, help="launch groups at once")
    run.add_argument("--port", type=int, default=DEFAULT_PORT)
    run.add_argument("--hostname", help="use an already running vLLM server on this node")
    run.add_argument("--server-timeout", type=int, default=90, help="minutes to wait for vLLM")
    run.add_argument("--skip-env-check", action="store_true")
    run.add_argument("--collect", action="store_true", help="pack, validate, preview at the end")
    run.add_argument("--org", default="local", help="org slug for the packed bundles")
    run.set_defaults(func=cmd_run)

    one = sub.add_parser("submit-one",
                         help="sbatch benchmark_one.sbatch per group (serve + suite on one node)")
    common(one)
    one.set_defaults(func=cmd_submit_one)

    col = sub.add_parser("collect", help="pack + validate every agent, preview the root")
    col.add_argument("logs_root")
    col.add_argument("--agents", nargs="*", help="default: every agent found under logs_root")
    col.add_argument("--org", default="local")
    col.add_argument("--smoke", action="store_true", help="preview only (smoke runs carry no suite)")
    col.add_argument("--dry-run", action="store_true")
    col.set_defaults(func=cmd_collect)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    # As launch_parallel_agents.py does, so a key kept in .env passes the
    # environment check too (the shell still wins).
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
