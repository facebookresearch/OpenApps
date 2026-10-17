"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""The composer's local host: the docs component plus things only a laptop can do.

    uv run compose.py                 # http://127.0.0.1:8765

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
it with ``shlex`` into an argv for ``Popen`` -- no shell anywhere. Requests
must carry the per-process token embedded in the page and a loopback Host, so
another site open in the same browser cannot drive the endpoints (a plain
cross-origin POST needs no preflight) and DNS rebinding gets nowhere.
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
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from starlette.staticfiles import StaticFiles

from open_apps.compose.catalog import TARGETS, Catalog, build_catalog, hydra_context
from open_apps.compose.command import (
    ALL_TASKS,
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
PREVIEW_TTL_S = 20.0
#: Hosts a request may name. Anything else is a rebound DNS name.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")

#: What a token may not contain, even though no shell ever sees it: command
#: separators, redirections, substitution and control characters. ``${`` is
#: allowed -- it is OmegaConf interpolation (``${oc.env:HOME}``) -- ``$(`` not.
SHELL_META = re.compile(r"[;&|`<>\\\n\r\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|\$\(")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: Environment variables a request may not set for /run: each changes what
#: gets executed or loaded, not how a run is configured.
_ENV_DENY = re.compile(
    r"^(PATH|IFS|ENV|BASH_ENV|SHELLOPTS|PS4|PROMPT_COMMAND|HOME|"
    r"LD_\w*|DYLD_\w*|PYTHON\w*|UV_\w*|VIRTUAL_ENV)$"
)
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
    if SHELL_META.search(token):
        raise RequestError(f"{what} {token!r} contains a shell metacharacter")
    return token


def check_override(token: str) -> str:
    """An extra override: safe characters, and valid Hydra override grammar."""
    check_token(token, "override")
    try:
        OverridesParser.create().parse_override(token)
    except HydraException as exc:
        raise RequestError(f"{token!r} is not a Hydra override: {exc}") from None
    return token


def checked_selection(data: Any, catalog: Catalog) -> Selection:
    """A :class:`Selection` from request JSON, every value checked.

    Group values must be options of the group, scalars must have the
    catalog's type (and ``task_name`` must name a task), extras must parse as
    overrides, env names must be plain and not on :data:`_ENV_DENY`. What
    passes is exactly what the static page could have produced.
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
        name, sep, value = str(assignment).partition("=")
        if not sep or not _ENV_NAME.match(name) or _ENV_DENY.match(name):
            raise RequestError(f"env {assignment!r} is not allowed")
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
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        name, value = words.pop(0).split("=", 1)
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


def is_slurm(selection: Selection) -> bool:
    """Whether the selection asks for the cluster, by pill or by extra."""
    if "slurm_cluster" in selection.choices.get("mode", []):
        return True
    return any(
        t.split("=", 1)[0].lstrip("+") == "mode" and t.endswith("slurm_cluster")
        for t in selection.extra
    )


# ---------------------------------------------------------------------------
# Validate: compose in-process, diff against the defaults
# ---------------------------------------------------------------------------


def _compose(config_name: str, overrides: list[str]) -> Any:
    from hydra import compose

    with HYDRA_LOCK, hydra_context():
        return compose(config_name=config_name, overrides=overrides)


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
    config_name = TARGETS[p.target].config_name
    overrides = list(p.overrides)
    composed = list(overrides)
    if p.target in POOL_TARGETS:
        agents = selection.choices.get("agent") or [
            TARGETS[p.target].defaults.get("agent") or "dummy"
        ]
        composed = [
            f"agent={agents[0]}",
            "browsergym_env_args.headless=True",
            *overrides,
        ]
        if p.target == "slurm_pool":
            composed = ["use_wandb=True", *composed]
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
    _log: Any = None

    def finished(self) -> bool:
        return self.process.poll() is not None

    def video(self) -> Path | None:
        if self.recordings is None:
            return None
        videos = sorted(self.recordings.glob("*.webm"), key=lambda p: p.stat().st_mtime)
        return videos[-1] if videos else None

    def stop(self) -> None:
        kill_group(self.process)
        if self._log is not None:
            self._log.close()
            self._log = None


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

    def _ensure_watchdog(self) -> None:
        if self._watchdog is not None:
            return

        def watch() -> None:
            while not self._closed.wait(2.0):
                if (
                    self.preview is not None
                    and time.monotonic() - self.preview_beat > PREVIEW_TTL_S
                ):
                    self.stop_preview()

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
        log = open(log_path, "ab")
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
        job = Job(job_id, kind, command, process, log_path, recordings, _log=log)
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
        self._closed.set()
        self.stop_preview()
        for job in list(self.jobs.values()):
            job.stop()
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


def create_app(
    composer: Composer | None = None, allowed_hosts: tuple[str, ...] = LOOPBACK_HOSTS
):
    """The FastHTML app. ``allowed_hosts`` is widened only by tests (their
    client calls itself ``testserver``)."""
    from fasthtml.common import FastHTML

    composer = composer or Composer(build_catalog(capabilities="local"))

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

    def guard(request: Request, query_token: bool = False) -> None:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in allowed_hosts:
            raise RequestError(f"host {host!r} is not a loopback name", 403)
        sent = request.headers.get("x-composer-token")
        if sent is None and query_token:
            sent = request.query_params.get("token")
        if not sent or not secrets.compare_digest(sent, composer.token):
            raise RequestError("missing or wrong composer token", 403)

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
        """JSON in/out with :class:`RequestError` and CommandError as 4xx."""

        # No return annotation: FastHTML would wrap the response in that class.
        async def handler(request: Request):
            try:
                guard(request)
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
            if isinstance(result, Response):
                return result
            return JSONResponse(result)

        return handler

    # -- pages -----------------------------------------------------------

    @app.route("/", methods=["GET"])
    def index(request: Request):
        return HTMLResponse(_PAGE.format(token=composer.token))

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
        # Also the pagehide beacon, which cannot set headers: token in the query.
        try:
            guard(request, query_token=True)
        except RequestError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, exc.status)
        return JSONResponse({"ok": True, "stopped": composer.stop_preview()})

    @app.route("/preview/heartbeat", methods=["POST"])
    def preview_heartbeat(request: Request):
        try:
            guard(request)
        except RequestError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, exc.status)
        return JSONResponse({"ok": True, "alive": composer.heartbeat()})

    # -- record / run ----------------------------------------------------

    def job_json(job: Job) -> dict[str, Any]:
        return {
            "ok": True,
            "job": job.id,
            "command": job.command,
            "events": f"/jobs/{job.id}/events?token={composer.token}",
        }

    def do_record(selection: Selection, data: dict) -> dict:
        return job_json(composer.record(selection, bool(data.get("real_agent"))))

    def do_run(selection: Selection, data: dict) -> Response | dict:
        if is_slurm(selection):
            command = plan(selection, composer.catalog).command
            return JSONResponse(
                {
                    "ok": False,
                    "refused": True,
                    "error": "The browser never launches cluster jobs; run this yourself.",
                    "command": command,
                },
                409,
            )
        return job_json(composer.run(selection))

    app.route("/record", methods=["POST"])(endpoint(do_record))
    app.route("/run", methods=["POST"])(endpoint(do_run))

    @app.route("/jobs/{job_id}/events", methods=["GET"])
    async def job_events(request: Request):
        try:
            guard(request, query_token=True)
            job = composer.jobs.get(request.path_params["job_id"])
            if job is None:
                raise RequestError("no such job", 404)
        except RequestError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, exc.status)

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
                            "video": f"/jobs/{job.id}/video?token={composer.token}"
                            if video
                            else None,
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
        try:
            guard(request, query_token=True)
        except RequestError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, exc.status)
        job = composer.jobs.get(request.path_params["job_id"])
        video = job.video() if job else None
        if video is None:
            return JSONResponse({"ok": False, "error": "no video"}, 404)
        return FileResponse(video, media_type="video/webm")

    @app.route("/jobs/{job_id}/stop", methods=["POST"])
    def job_stop(request: Request):
        try:
            guard(request)
        except RequestError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, exc.status)
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
    url = f"http://{HOST}:{port}/"
    print(f"Config Composer (local): {url}")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning")
