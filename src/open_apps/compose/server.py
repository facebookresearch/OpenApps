"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""The composer's local host: the docs component plus things only a laptop can do.

    uv run compose.py                 # http://127.0.0.1:8765/?token=...

Serves the same ``<openapps-composer>`` the docs site embeds, with a freshly
built catalog whose ``capabilities`` is ``local`` -- which is what makes the
component show its Validate / Preview / Record / Run buttons -- and the
endpoints behind them:

``POST /validate``  compose the selection with Hydra in-process; the resolved
                    config's diff against the defaults, or Hydra's error pinned
                    to the override that caused it.
``POST /preview``   serve the apps for the selection (launch.py, through
                    :mod:`open_apps.compose.live`) and return a URL to frame.
                    One at a time: a new preview, ``/preview/stop``, or a page
                    that stops sending heartbeats kills the old one's group.
``POST /record``    run one episode with ``agent=dummy record_video=True`` (or
                    the selected agent, if asked explicitly); progress streams
                    over SSE and the webm plays inline when it lands.
``POST /run``       run the composed command with a streamed log -- never a
                    SLURM one: those come back as a command to copy.

What may reach a process is narrow on purpose. The client sends a selection,
never a command: the server re-checks every value against the catalog, every
extra override against Hydra's grammar, renders the command itself and splits
it with ``shlex`` into an argv for ``Popen`` -- no shell anywhere.

Who may ask is narrow too, the way Jupyter does it. ``serve()`` prints (and
opens) ``http://127.0.0.1:PORT/?token=...``; that one GET trades the token for
an HttpOnly, SameSite=Strict cookie and redirects to the bare URL. Every
request -- page, catalog, assets, endpoints, SSE -- must then name a loopback
Host (DNS rebinding gets nowhere) and carry the cookie or the token header,
and the JSON endpoints want the header as well (double submit): another site
open in the same browser can neither read the page nor drive the endpoints,
and another local user cannot fetch the token from ``GET /``.
"""

import asyncio
import contextlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from hydra.core.override_parser.overrides_parser import OverridesParser
from hydra.errors import HydraException
from omegaconf import OmegaConf
from omegaconf.errors import OmegaConfBaseException
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.staticfiles import StaticFiles

from open_apps.compose.catalog import TARGETS, Catalog, build_catalog, hydra_context
from open_apps.compose.command import (
    ALL_TASKS,
    ENV_ASSIGNMENT,
    POOL_TARGETS,
    CommandError,
    Selection,
    normalize,
    render,
    resolve_target,
)
from open_apps.compose.live import (
    HYDRA_LOCK,
    REPO_ROOT,
    AppsStartError,
    LiveApps,
    kill_group,
    start_apps,
)

ASSETS = REPO_ROOT / "docs" / "assets" / "composer"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765
#: A preview whose page has not sent a heartbeat for this long is stopped: the
#: tab was closed without its beacon arriving (crash, sleep, killed browser).
#: Minutes, not seconds: browsers throttle a background tab's timers to about
#: one run a minute, and switching tabs must not kill the preview. (The page
#: also beats as soon as it is visible again.)
PREVIEW_TTL_S = 180.0
#: Hosts a request may name. Anything else is a rebound DNS name. (No
#: ``[::1]``: the server binds 127.0.0.1 only.)
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
#: POSTs that may authenticate with the cookie alone: the ``pagehide``
#: beacons, which cannot set headers. SameSite=Strict keeps the cookie off
#: cross-site requests, so another site still cannot send them.
_BEACONS = re.compile(r"^/(preview|jobs/[^/]+)/stop$")

#: What a token may not contain: control characters (newlines, NUL, escape
#: sequences), which no value needs and which garble the streamed log. Shell
#: metacharacters are fine -- no shell ever sees a token (argv for Popen) --
#: and valid Hydra values use them: ``apps.todo.title='Q&A'``, ``a\,b``.
#: What an extra override may *mean* is :func:`check_override`'s business.
CONTROL_CHARS = re.compile(r"[\n\r\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: W&B variables a request may set: where a run is filed and how it is
#: labelled. Explicit rather than ``WANDB_*``: ``WANDB_BASE_URL`` and
#: ``WANDB_API_KEY`` would send the run (config, screenshots, logs) to
#: someone else's server or account, and the directory variables move where
#: files are written and read.
WANDB_ENV = frozenset(
    {
        "WANDB_PROJECT",
        "WANDB_ENTITY",
        "WANDB_GROUP",
        "WANDB_RUN_GROUP",
        "WANDB_JOB_TYPE",
        "WANDB_NAME",
        "WANDB_NOTES",
        "WANDB_TAGS",
        "WANDB_MODE",
        "WANDB_SILENT",
        "WANDB_CONSOLE",
    }
)
#: Environment variables a request may set. An allowlist, not a denylist:
#: the variables that change what gets executed or loaded (``PATH``,
#: ``LD_PRELOAD``, ``PYTHONSTARTUP``, ``BROWSER``, ``GIT_SSH_COMMAND``, ...)
#: are an open-ended set, while the ones a run is configured with are few.
#: These are what ``scripts/conduct*.sh`` read as ``${NAME:-default}`` --
#: the worker-pool knobs the composer renders plus the vLLM node -- and the
#: W&B labels above; a test keeps the list in step with the scripts.
ENV_ALLOWLIST = (
    frozenset(
        {
            "AGENTS",
            "COUNT",
            "MAX_PARALLEL",
            "HEADLESS",
            "LOG_DIR",
            "WANDB_GROUP",
            "VLLM_HOST",
            "VLLM_MODEL",
            "VLLM_PORT",
        }
    )
    | WANDB_ENV
)

#: Config keys an extra override may not touch (after ``+``/``++``/``~``):
#: Hydra's own settings -- callbacks, launchers, sweepers, search path --
#: name classes to import and run.
_HYDRA_KEY = re.compile(r"^(_global_\.)?hydra([./]|$)")
#: Keys ``hydra.utils.instantiate`` acts on. The repo instantiates ``agent``,
#: ``browsergym_env_args``, ``parallel_tasks`` and task configs, so one of
#: these anywhere in an override -- key path or dict value -- can name any
#: callable to run.
INSTANTIATE_KEYS = ("_target_", "_args_", "_partial_", "_recursive_")
#: Resolvers an interpolation may call: read-only lookups. Everything else
#: (``oc.decode``, ``oc.create``, ...) builds config nodes from strings,
#: which is a way around the checks above. The repo registers no resolvers of
#: its own; ``hydra`` is Hydra's read-only view of its runtime config
#: (``${hydra:runtime.cwd}``, as config/mode/local.yaml uses).
SAFE_RESOLVERS = frozenset({"oc.env", "now", "oc.select", "hydra"})
#: ``${name:`` -> a resolver call; ``${a.b}`` (no colon) is a plain reference.
_RESOLVER = re.compile(r"\$\{\s*([^:}\s]*)\s*(:?)")
# The cheap stand-in for the shop's 999-product pack (see catalog._FAST_OVERRIDES).
_FAST = "apps/onlineshop/content=default"


class RequestError(ValueError):
    """A request the server will not act on; ``status`` is the HTTP code."""

    def __init__(self, message: str, status: int = 400, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra


# ---------------------------------------------------------------------------
# Selections in, argv out
# ---------------------------------------------------------------------------


def check_token(token: str, what: str) -> str:
    """``token`` if it is safe to place in an argv, else :class:`RequestError`."""
    if not isinstance(token, str) or not token:
        raise RequestError(f"{what} must be a non-empty string")
    if CONTROL_CHARS.search(token):
        raise RequestError(f"{what} {token!r} contains a control character")
    return token


def check_override(token: str) -> str:
    """An extra override that configures a run and cannot run code.

    Valid Hydra override grammar; no key under ``hydra`` (as the key or the
    ``@package``); no :data:`INSTANTIATE_KEYS` anywhere in it; and no
    interpolation calling a resolver outside :data:`SAFE_RESOLVERS`. A link
    can carry any override into the page, so this -- not the page -- is what
    stands between a crafted URL and ``+hydra.callbacks.x._target_=os.system``.
    """
    check_token(token, "override")
    try:
        override = OverridesParser.create().parse_override(token)
    except HydraException as exc:
        raise RequestError(f"{token!r} is not a Hydra override: {exc}") from None
    for path in (override.key_or_group, override.package or ""):
        if _HYDRA_KEY.match(path):
            raise RequestError(
                f"{token!r} sets Hydra's own config (launchers, callbacks), "
                "which can run code; run it yourself"
            )
    for key in INSTANTIATE_KEYS:
        if key in token:
            raise RequestError(
                f"{token!r} contains {key}, which names code for Hydra to run"
            )
    for name, call in _RESOLVER.findall(token):
        if call and name not in SAFE_RESOLVERS:
            raise RequestError(
                f"{token!r} calls the resolver {name!r}; only "
                f"{', '.join(sorted(SAFE_RESOLVERS))} are allowed here"
            )
    return token


def checked_selection(data: Any, catalog: Catalog) -> Selection:
    """A :class:`Selection` from request JSON, every value checked.

    Group values must be options of the group, scalars must have the
    catalog's type (and ``task_name`` must name a task), extras must pass
    :func:`check_override`, env names must be on :data:`ENV_ALLOWLIST`. What
    passes is a subset of what the static page can produce: the page renders
    any extra or env a link brings in, but only these may reach a process.
    """
    if not isinstance(data, dict):
        raise RequestError("selection must be an object")
    choices = data.get("choices") or {}
    if not isinstance(choices, dict):
        raise RequestError("selection.choices must be an object")
    all_tasks = {t.name for entries in catalog.tasks.values() for t in entries}
    checked: dict[str, list[Any]] = {}
    for key, values in choices.items():
        if not isinstance(values, list) or not values:
            raise RequestError(f"choices[{key!r}] must be a non-empty list")
        if key in catalog.groups:
            names = {o.name for o in catalog.groups[key].options}
            bad = [v for v in values if v not in names]
            if bad:
                raise RequestError(f"{bad[0]!r} is not an option of {key}")
        elif key in catalog.scalars:
            kind = catalog.scalars[key].type
            for v in values:
                ok = {
                    "bool": isinstance(v, bool),
                    "int": isinstance(v, int) and not isinstance(v, bool),
                    "enum": v == ALL_TASKS or v in all_tasks,
                }.get(kind, isinstance(v, str))
                if not ok:
                    raise RequestError(f"{v!r} is not a valid {key}")
                if isinstance(v, str):
                    check_token(v, key)
        else:
            raise RequestError(f"unknown key {key!r}")
        checked[key] = list(values)

    target = data.get("target")
    if target is not None and target not in TARGETS:
        raise RequestError(f"unknown target {target!r}")
    numbers = {}
    for key in ("count", "max_parallel"):
        value = data.get(key)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise RequestError(f"{key} must be a non-negative integer")
        numbers[key] = value
    extra = [check_override(t) for t in data.get("extra") or []]
    env = []
    for assignment in data.get("env") or []:
        match = ENV_ASSIGNMENT.match(str(assignment))
        if not match or match.group(1) not in ENV_ALLOWLIST:
            raise RequestError(
                f"env {assignment!r} is not allowed; the browser may set "
                f"{', '.join(sorted(ENV_ALLOWLIST))}"
            )
        name, value = match.groups()
        if value:
            check_token(value, f"env {name}")
        env.append(f"{name}={value}")
    sbatch = [check_token(a, "sbatch option") for a in data.get("sbatch_args") or []]
    return Selection(
        choices=checked,
        target=target,
        count=numbers["count"],
        max_parallel=numbers["max_parallel"],
        extra=extra,
        env=env,
        sbatch_args=sbatch,
    )


@dataclass
class Plan:
    """A rendered selection, split for :class:`subprocess.Popen`."""

    target: str
    command: str
    env: dict[str, str]
    argv: list[str]
    #: Index in ``argv`` of the first Hydra override (after the script).
    first_override: int

    @property
    def overrides(self) -> list[str]:
        return self.argv[self.first_override :]


def plan(selection: Selection, catalog: Catalog) -> Plan:
    """Render ``selection`` and split it: leading ``NAME=value`` words become
    the environment, the rest the argv. Raises :class:`CommandError`."""
    command = render(selection, catalog)
    words = shlex.split(command)
    env: dict[str, str] = {}
    while words and ENV_ASSIGNMENT.match(words[0]):
        name, value = ENV_ASSIGNMENT.match(words.pop(0)).groups()
        env[name] = value
    script = next(i for i, w in enumerate(words) if w.endswith((".py", ".sh")))
    return Plan(
        resolve_target(normalize(selection, catalog), catalog),
        command,
        env,
        words,
        script + 1,
    )


def single_view(selection: Selection, catalog: Catalog) -> Selection:
    """The one run a preview or recording shows for any selection.

    A sweep or pool becomes its first cell / first agent: every axis keeps its
    first value, the mode is local, and sweep-only keys go -- launch.py and
    launch_agent.py compose config.yaml, which has no ``parallel_tasks``.
    """
    choices = {
        key: values[:1]
        for key, values in selection.choices.items()
        if key != "mode" and not (key == "task_name" and values == [ALL_TASKS])
    }
    extra = [t for t in selection.extra if not t.startswith("parallel_tasks.")]
    return Selection(choices=choices, target="single", extra=extra)


# ---------------------------------------------------------------------------
# Validate: compose in-process, diff against the defaults
# ---------------------------------------------------------------------------


def _compose(config_name: str, overrides: list[str], **kwargs: Any) -> Any:
    from hydra import compose

    with HYDRA_LOCK, hydra_context():
        return compose(config_name=config_name, overrides=overrides, **kwargs)


def composition(p: "Plan", selection: Selection) -> tuple[str, list[str]]:
    """The config name and overrides the planned command would compose.

    Pools compose config.yaml under the first agent with what conduct.sh puts
    in front of the forwarded overrides (and conduct_slurm.sh its
    ``use_wandb=True``); the other targets compose exactly their argv.
    """
    config_name = TARGETS[p.target].config_name
    overrides = list(p.overrides)
    if p.target in POOL_TARGETS:
        agents = selection.choices.get("agent") or [
            TARGETS[p.target].defaults.get("agent") or "dummy"
        ]
        overrides = [
            f"agent={agents[0]}",
            "browsergym_env_args.headless=True",
            *overrides,
        ]
        if p.target == "slurm_pool":
            overrides = ["use_wandb=True", *overrides]
    return config_name, overrides


def _short(value: Any, limit: int = 120) -> str:
    if isinstance(value, list) and len(value) > 8:
        return f"[{len(value)} items]"
    if isinstance(value, dict):
        return f"{{{len(value)} keys}}"
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def config_diff(base: Any, cfg: Any, limit: int = 200) -> list[dict[str, str]]:
    """Leaf-level differences between two compositions, unresolved.

    Unresolved so ``${now:...}`` and env interpolations compare as written
    rather than as whatever they evaluate to this second. Lists compare whole
    (a content pack's product list is one entry, not 999); a subtree present
    on one side only is one entry too. ``tasks`` is the task map the tasks
    group loads -- hundreds of goals -- so it is summarized, not walked.
    """
    a = OmegaConf.to_container(base, resolve=False)
    b = OmegaConf.to_container(cfg, resolve=False)
    out: list[dict[str, str]] = []
    missing = object()

    def walk(x: Any, y: Any, path: str) -> None:
        if isinstance(x, dict) and isinstance(y, dict) and path != "tasks":
            for key in list(x) + [k for k in y if k not in x]:
                walk(
                    x.get(key, missing),
                    y.get(key, missing),
                    f"{path}.{key}" if path else str(key),
                )
            return
        if x == y:
            return
        out.append(
            {
                "key": path,
                "default": "(unset)" if x is missing else _short(x),
                "value": "(unset)" if y is missing else _short(y),
            }
        )

    walk(a, b, "")
    if len(out) > limit:
        out = out[:limit] + [
            {"key": f"… {len(out) - limit} more", "default": "", "value": ""}
        ]
    return out


def offending_override(
    message: str, overrides: list[str], config_name: str
) -> str | None:
    """The override a Hydra error is about.

    Hydra names it in most messages (``Could not find 'agent/x'``, ``Could not
    override 'a.b'``); failing that, each override is composed alone (with the
    cheap shop pack) and the first one that fails is it.
    """
    for token in overrides:
        key, _, value = token.lstrip("+~").partition("=")
        if f"'{key}/{value}'" in message or f"'{key}'" in message:
            return token
    for token in overrides:
        fast = [] if "apps/onlineshop/content" in token else [_FAST]
        try:
            _compose(config_name, [token, *fast])
        except Exception:
            return token
    return None


def validate(selection: Selection, catalog: Catalog) -> dict[str, Any]:
    """Compose what the rendered command would compose; diff it or map the error.

    Sweeps compose the sweep config and then the first cell the way
    create_configs does; pools compose config.yaml under the first agent with
    what conduct.sh puts in front of the forwarded overrides.
    """
    p = plan(selection, catalog)
    config_name, composed = composition(p, selection)
    try:
        cfg = _compose(config_name, composed)
        if p.target == "sweep":
            cells = OmegaConf.to_container(cfg.parallel_tasks.app_variations)
            names = cfg.parallel_tasks.task_names
            first = next(iter(cfg.tasks)) if names == ALL_TASKS else names[0]
            for cell in cells[:1]:
                _compose("config", [*cell, f"task_name={first}"])
    except Exception as exc:  # Hydra raises several unrelated types
        message = str(exc).strip()
        return {
            "ok": False,
            "command": p.command,
            "target": p.target,
            "error": message,
            "override": offending_override(message, composed, config_name),
        }
    base = _default_composition(config_name)
    return {
        "ok": True,
        "command": p.command,
        "target": p.target,
        "config": config_name,
        "diff": config_diff(base, cfg),
    }


_DEFAULTS: dict[str, Any] = {}


def _default_composition(config_name: str) -> Any:
    if config_name not in _DEFAULTS:
        _DEFAULTS[config_name] = _compose(config_name, [])
    return _DEFAULTS[config_name]


# ---------------------------------------------------------------------------
# Run: what the browser may start
# ---------------------------------------------------------------------------

#: The programs /run may start, as the argv words before the first override.
#: Everything else -- ``sbatch`` above all -- comes back as a command to copy.
LOCAL_PROGRAMS = (
    ("uv", "run", "launch_agent.py"),
    ("uv", "run", "launch_parallel_agents.py"),
    ("./scripts/conduct.sh",),
)
_BASIC_LAUNCHER = "hydra._internal.core_plugins.basic_launcher.BasicLauncher"


def run_refusal(selection: Selection, catalog: Catalog) -> str | None:
    """Why /run will not start ``selection`` here, or None if it may.

    Decided from the resolved plan, not from how the selection spells it:
    the rendered program must be one of :data:`LOCAL_PROGRAMS`, the target
    must not be the SLURM pool, and the config the command would compose
    must say ``cluster: local`` (the key launch_parallel_agents.py dispatches
    on; ``mode=`` is just the group that usually sets it) with Hydra's own
    basic, single-run launcher. A pill, a quoted ``mode='slurm_cluster'``, a
    bare ``cluster=slurm`` or a forced target all end up in the same place.
    Raises Hydra's exception when the selection does not compose at all.
    """
    p = plan(selection, catalog)
    if p.target == "slurm_pool":
        return "A SLURM worker pool is submitted with sbatch."
    program = tuple(p.argv[: p.first_override])
    if program not in LOCAL_PROGRAMS:
        return f"The browser only starts local launchers, not {shlex.join(program)}."
    config_name, overrides = composition(p, selection)
    cfg = _compose(config_name, overrides, return_hydra_config=True)
    cluster = cfg.get("cluster")
    if cluster != "local":
        return f"The composed config runs on cluster={cluster}."
    hydra = cfg.hydra
    if (
        str(hydra.mode).endswith("MULTIRUN")
        or hydra.launcher.get("_target_") != _BASIC_LAUNCHER
    ):
        return "The composed config hands the run to a Hydra launcher plugin."
    return None


# ---------------------------------------------------------------------------
# Processes: the preview, and recorded / run jobs
# ---------------------------------------------------------------------------


@dataclass
class Job:
    id: str
    kind: str
    command: str
    process: subprocess.Popen
    log_path: Path
    recordings: Path | None = None
    started: float = field(default_factory=time.time)

    def finished(self) -> bool:
        return self.process.poll() is not None

    def video(self) -> Path | None:
        if self.recordings is None:
            return None
        videos = sorted(self.recordings.glob("*.webm"), key=lambda p: p.stat().st_mtime)
        return videos[-1] if videos else None

    def stop(self) -> None:
        """Kill the job's whole process tree (see :func:`kill_group`)."""
        kill_group(self.process)


class Composer:
    """Process state for one ``compose.py``: the catalog, the token, the live
    preview and the jobs. :meth:`shutdown` kills every process it started."""

    def __init__(self, catalog: Catalog, runtime_dir: Path | None = None) -> None:
        self.catalog = catalog
        self.token = secrets.token_urlsafe(24)
        self._own_runtime = runtime_dir is None
        self.runtime = Path(runtime_dir or tempfile.mkdtemp(prefix="openapps-compose-"))
        self.preview: LiveApps | None = None
        self.preview_beat = 0.0
        self.preview_count = 0
        self.preview_lock = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self._watchdog: threading.Thread | None = None
        self._closed = threading.Event()

    # -- preview ---------------------------------------------------------

    def start_preview(self, selection: Selection) -> dict[str, Any]:
        view = single_view(selection, self.catalog)
        overrides = plan(view, self.catalog).overrides
        with self.preview_lock:
            self._stop_preview_locked()
            self.preview_count += 1
            runtime = self.runtime / f"preview-{self.preview_count}"
            live = start_apps(overrides, runtime)
            self.preview = live
            self.preview_beat = time.monotonic()
        self._ensure_watchdog()
        device = live.launcher.config.device
        return {
            "url": live.url + "/",
            "viewport": list(device.viewport),
            "device": str(device.name),
            "pgid": live.pgid,
            "overrides": overrides,
        }

    def stop_preview(self) -> bool:
        with self.preview_lock:
            return self._stop_preview_locked()

    def _stop_preview_locked(self) -> bool:
        if self.preview is None:
            return False
        self.preview.stop()
        self.preview = None
        return True

    def heartbeat(self) -> bool:
        self.preview_beat = time.monotonic()
        return self.preview is not None

    def expire_stale_preview(self) -> bool:
        """Stop the preview if its page has gone quiet for :data:`PREVIEW_TTL_S`."""
        with self.preview_lock:
            if (
                self.preview is None
                or time.monotonic() - self.preview_beat <= PREVIEW_TTL_S
            ):
                return False
            return self._stop_preview_locked()

    def _ensure_watchdog(self) -> None:
        if self._watchdog is not None:
            return

        def watch() -> None:
            while not self._closed.wait(2.0):
                self.expire_stale_preview()

        self._watchdog = threading.Thread(
            target=watch, name="preview-watchdog", daemon=True
        )
        self._watchdog.start()

    # -- jobs ------------------------------------------------------------

    def start_job(
        self,
        kind: str,
        argv: list[str],
        command: str,
        env: dict[str, str] | None = None,
        recordings: Path | None = None,
    ) -> Job:
        job_id = secrets.token_hex(4)
        log_path = self.runtime / f"job-{job_id}.log"
        # Closed as soon as Popen returns or raises: the child holds its own
        # copy of the descriptor, and ours would only leak (one per job).
        with open(log_path, "ab") as log:
            log.write(f"$ {command}\n".encode())
            log.flush()
            process = subprocess.Popen(
                argv,
                cwd=REPO_ROOT,
                env={**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"},
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        job = Job(job_id, kind, command, process, log_path, recordings)
        self.jobs[job_id] = job
        return job

    def record(self, selection: Selection, real_agent: bool) -> Job:
        view = single_view(selection, self.catalog)
        if not real_agent:
            view = replace(view, choices={**view.choices, "agent": ["dummy"]})
        overrides = plan(view, self.catalog).overrides
        job_dir = self.runtime / f"record-{secrets.token_hex(4)}"
        recordings = job_dir / "recordings"
        argv = [
            sys.executable,
            "launch_agent.py",
            *overrides,
            "record_video=True",
            f"record_video_dir={recordings}",
            f"logs_dir={job_dir / 'logs'}",
            "use_wandb=False",
            "browsergym_env_args.headless=True",
        ]
        shown = shlex.join(["uv", "run", *argv[1:]])
        return self.start_job("record", argv, shown, recordings=recordings)

    def run(self, selection: Selection) -> Job:
        p = plan(selection, self.catalog)
        if p.argv[:2] == ["uv", "run"] and shutil.which("uv") is None:
            raise RequestError(
                "`uv` is not on PATH; copy the command instead", 409, command=p.command
            )
        return self.start_job("run", p.argv, p.command, env=p.env)

    def shutdown(self) -> None:
        """Kill every process this composer started and forget its jobs.

        A job already reaped is skipped (kill_group leaves it alone): its pid
        may have been recycled. Finished jobs are dropped so nothing can try
        to signal them later.
        """
        self._closed.set()
        self.stop_preview()
        for job in list(self.jobs.values()):
            job.stop()
        self.jobs.clear()
        if self._own_runtime:
            shutil.rmtree(self.runtime, ignore_errors=True)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Config Composer · OpenApps (local)</title>
<link rel="stylesheet" href="/assets/composer/composer.css">
<style>
  body {{ margin: 0; padding: 24px clamp(12px, 4vw, 48px); background: #f2df85; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #121418; }} }}
</style>
</head>
<body>
<openapps-composer catalog="/catalog.json" media="/assets/composer/media/manifest.json"
  endpoint="/" token="{token}"></openapps-composer>
<script src="/assets/composer/composer.js"></script>
</body>
</html>
"""


def cookie_name(port: int | None) -> str:
    """The session cookie for the composer on ``port``.

    Per port because cookies ignore ports: two ``compose.py`` on one host
    would otherwise overwrite each other's token in the browser.
    """
    return f"openapps_composer_{port or 80}"


def _same(sent: str | None, token: str) -> bool:
    # Bytes: compare_digest raises on a non-ASCII str, which a header can be.
    return bool(sent) and secrets.compare_digest(sent.encode(), token.encode())


class _Guard:
    """ASGI middleware: the loopback Host and the token, before any route.

    Middleware rather than a check per handler so nothing mounted later
    (static files, a new endpoint) can forget it.
    """

    def __init__(self, app: Any, check: Any) -> None:
        self.app = app
        self.check = check

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            try:
                self.check(Request(scope))
            except RequestError as exc:
                response = JSONResponse({"ok": False, "error": str(exc)}, exc.status)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(
    composer: Composer | None = None, allowed_hosts: tuple[str, ...] = LOOPBACK_HOSTS
):
    """The FastHTML app. ``allowed_hosts`` is widened only by tests (their
    client calls itself ``testserver``)."""
    from fasthtml.common import FastHTML

    composer = composer or Composer(build_catalog(capabilities="local"))

    def authorize(request: Request) -> None:
        """Raise :class:`RequestError` (403) unless the request may proceed."""
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in allowed_hosts:
            raise RequestError(f"host {host!r} is not a loopback name", 403)
        path = request.url.path
        if request.method == "GET" and path == "/" and "token" in request.query_params:
            return  # the login: index() checks the token and sets the cookie
        header = request.headers.get("x-composer-token")
        cookie = request.cookies.get(cookie_name(request.url.port))
        if request.method == "POST" and not _BEACONS.match(path):
            authorized = _same(header, composer.token)
        else:
            authorized = _same(header, composer.token) or _same(cookie, composer.token)
        if not authorized:
            raise RequestError(
                "missing or wrong composer token: open the URL compose.py printed",
                403,
            )

    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        composer.shutdown()  # every process it started dies with the server

    app = FastHTML(
        # No CDN headers (see open_apps.frontend), no session cookie file.
        default_hdrs=False,
        secret_key=secrets.token_hex(16),
        lifespan=lifespan,
    )
    app.state.composer = composer
    app.add_middleware(_Guard, check=authorize)

    async def body(request: Request) -> dict[str, Any]:
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise RequestError("expected application/json", 415)
        try:
            data = await request.json()
        except ValueError:
            raise RequestError("body is not JSON") from None
        if not isinstance(data, dict):
            raise RequestError("body must be an object")
        return data

    def endpoint(fn):
        """JSON in/out, errors included: :class:`RequestError` and
        CommandError as 4xx, a selection Hydra cannot compose as 422, a
        process that cannot start (or anything else that goes wrong) as 500
        -- always ``{"ok": false, "error": ...}`` the page can show, never a
        bare 500 page."""

        # No return annotation: FastHTML would wrap the response in that class.
        async def handler(request: Request):
            try:
                data = await body(request)
                selection = checked_selection(
                    data.get("selection") or {}, composer.catalog
                )
                result = await asyncio.to_thread(fn, selection, data)
            except RequestError as exc:
                return JSONResponse(
                    {"ok": False, "error": str(exc), **exc.extra}, exc.status
                )
            except CommandError as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, 422)
            except (HydraException, OmegaConfBaseException) as exc:
                message = f"Hydra could not compose this selection: {exc}"
                return JSONResponse({"ok": False, "error": message}, 422)
            except OSError as exc:  # Popen: no such program, no permission, ...
                message = f"could not start the process: {exc}"
                return JSONResponse({"ok": False, "error": message}, 500)
            except Exception as exc:  # the launcher's own errors
                message = f"{type(exc).__name__}: {exc}"
                return JSONResponse({"ok": False, "error": message}, 500)
            if isinstance(result, Response):
                return result
            return JSONResponse(result)

        return handler

    # -- pages -----------------------------------------------------------

    @app.route("/", methods=["GET"])
    def index(request: Request):
        sent = request.query_params.get("token")
        if sent is not None:
            if not _same(sent, composer.token):
                return JSONResponse({"ok": False, "error": "wrong composer token"}, 403)
            # Trade the URL's token for a cookie and drop it from the address
            # bar (and the history); the hash, if any, survives the redirect.
            response = RedirectResponse("/", 303)
            response.set_cookie(
                cookie_name(request.url.port),
                composer.token,
                httponly=True,
                samesite="strict",
                path="/",
            )
            return response
        # The page still carries the token, for the header the JSON endpoints
        # want; only a request that already holds it can read the page.
        return HTMLResponse(
            _PAGE.format(token=composer.token), headers={"Cache-Control": "no-store"}
        )

    @app.route("/catalog.json", methods=["GET"])
    def catalog_json(request: Request):
        return Response(composer.catalog.to_json(), media_type="application/json")

    app.mount("/assets/composer", StaticFiles(directory=ASSETS), name="composer-assets")

    # -- validate --------------------------------------------------------

    def do_validate(selection: Selection, data: dict) -> Response:
        result = validate(selection, composer.catalog)
        return JSONResponse(result, 200 if result["ok"] else 422)

    app.route("/validate", methods=["POST"])(endpoint(do_validate))

    # -- preview ---------------------------------------------------------

    def do_preview(selection: Selection, data: dict) -> Response:
        try:
            return JSONResponse({"ok": True, **composer.start_preview(selection)})
        except AppsStartError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc), "log": exc.log[-4000:]}, 502
            )

    app.route("/preview", methods=["POST"])(endpoint(do_preview))

    @app.route("/preview/stop", methods=["POST"])
    def preview_stop(request: Request):
        # Also the pagehide beacon: it carries the cookie (see _BEACONS).
        return JSONResponse({"ok": True, "stopped": composer.stop_preview()})

    @app.route("/preview/heartbeat", methods=["POST"])
    def preview_heartbeat(request: Request):
        return JSONResponse({"ok": True, "alive": composer.heartbeat()})

    # -- record / run ----------------------------------------------------

    def job_json(job: Job) -> dict[str, Any]:
        return {
            "ok": True,
            "job": job.id,
            "command": job.command,
            # EventSource sends the cookie (same origin); no token in URLs.
            "events": f"/jobs/{job.id}/events",
        }

    def do_record(selection: Selection, data: dict) -> dict:
        return job_json(composer.record(selection, bool(data.get("real_agent"))))

    def do_run(selection: Selection, data: dict) -> Response | dict:
        why = run_refusal(selection, composer.catalog)
        if why:
            command = plan(selection, composer.catalog).command
            return JSONResponse(
                {
                    "ok": False,
                    "refused": True,
                    "error": f"{why} The browser never launches cluster jobs; "
                    "run this yourself.",
                    "command": command,
                },
                409,
            )
        return job_json(composer.run(selection))

    app.route("/record", methods=["POST"])(endpoint(do_record))
    app.route("/run", methods=["POST"])(endpoint(do_run))

    @app.route("/jobs/{job_id}/events", methods=["GET"])
    async def job_events(request: Request):
        job = composer.jobs.get(request.path_params["job_id"])
        if job is None:
            return JSONResponse({"ok": False, "error": "no such job"}, 404)

        async def stream():
            with open(job.log_path, "rb") as fh:
                buffer = b""
                while True:
                    chunk = fh.read()
                    if chunk:
                        buffer += chunk
                        *lines, buffer = buffer.split(b"\n")
                        for line in lines:
                            text = line.decode("utf-8", errors="replace").rstrip("\r")
                            yield f"data: {json.dumps({'line': text})}\n\n"
                    elif job.finished():
                        if buffer:
                            yield f"data: {json.dumps({'line': buffer.decode('utf-8', 'replace')})}\n\n"
                        video = job.video()
                        done = {
                            "returncode": job.process.returncode,
                            "video": f"/jobs/{job.id}/video" if video else None,
                        }
                        yield f"event: done\ndata: {json.dumps(done)}\n\n"
                        return
                    else:
                        await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.route("/jobs/{job_id}/video", methods=["GET"])
    def job_video(request: Request):
        job = composer.jobs.get(request.path_params["job_id"])
        video = job.video() if job else None
        if video is None:
            return JSONResponse({"ok": False, "error": "no video"}, 404)
        return FileResponse(video, media_type="video/webm")

    @app.route("/jobs/{job_id}/stop", methods=["POST"])
    def job_stop(request: Request):
        # Also the pagehide beacon: it carries the cookie (see _BEACONS).
        job = composer.jobs.get(request.path_params["job_id"])
        if job is None:
            return JSONResponse({"ok": False, "error": "no such job"}, 404)
        job.stop()
        return JSONResponse({"ok": True})

    return app


def serve(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    """Run the local composer on 127.0.0.1 only -- it can start processes, so
    it is never reachable from another machine. Blocks; Ctrl-C stops it and
    every process it started."""
    import atexit
    import webbrowser

    import uvicorn

    composer = Composer(build_catalog(capabilities="local"))
    atexit.register(composer.shutdown)  # in case the lifespan never runs
    app = create_app(composer)
    # The one URL that carries the token; the page swaps it for a cookie.
    url = f"http://{HOST}:{port}/?token={composer.token}"
    print(f"Config Composer (local): {url}")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning")
