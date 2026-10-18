"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""The composer's local host (``compose.py`` / ``open_apps.compose.server``).

Driven through Starlette's test client against the real app and catalog. The
preview test boots the real apps once (~20 s) and checks the whole process
group is gone afterwards -- the point of the endpoint is that nothing it
starts outlives it.
"""

import contextlib
import json
import os
import re
import signal
import sys
import time
import urllib.request

import psutil
import pytest
from starlette.testclient import TestClient

from open_apps.compose.catalog import build_catalog
from open_apps.compose.command import Selection
from open_apps.compose.live import REPO_ROOT, group_alive
from open_apps.compose.server import (
    PREVIEW_TTL_S,
    Composer,
    RequestError,
    ENV_ALLOWLIST,
    WANDB_ENV,
    checked_selection,
    cookie_name,
    create_app,
    plan,
    run_refusal,
    single_view,
)


@pytest.fixture(scope="module")
def catalog():
    return build_catalog(capabilities="local")


@pytest.fixture(scope="module")
def composer(catalog, tmp_path_factory):
    composer = Composer(catalog, tmp_path_factory.mktemp("compose-runtime"))
    yield composer
    composer.shutdown()


@pytest.fixture(scope="module")
def client(composer):
    app = create_app(composer, allowed_hosts=("testserver",))
    with TestClient(app) as client:
        client.headers["X-Composer-Token"] = composer.token
        yield client


def post(client, path, selection, **extra):
    return client.post(path, json={"selection": selection, **extra})


# --------------------------------------------------------------------------
# Serving
# --------------------------------------------------------------------------


def test_serves_the_component_with_local_capabilities(client, composer):
    page = client.get("/")
    assert page.status_code == 200
    assert "<openapps-composer" in page.text
    assert "cdn." not in page.text  # nothing from outside (see open_apps.frontend)
    catalog = client.get("/catalog.json").json()
    assert catalog["capabilities"] == "local"
    assert catalog["groups"]["agent"]["options"]
    assert client.get("/assets/composer/composer.js").status_code == 200


#: Every route, as (method, path): pages, the catalog, assets, endpoints, SSE.
ROUTES = [
    ("GET", "/"),
    ("GET", "/catalog.json"),
    ("GET", "/assets/composer/composer.js"),
    ("GET", "/assets/composer/media/manifest.json"),
    ("POST", "/validate"),
    ("POST", "/preview"),
    ("POST", "/preview/stop"),
    ("POST", "/preview/heartbeat"),
    ("POST", "/record"),
    ("POST", "/run"),
    ("GET", "/jobs/nope/events"),
    ("GET", "/jobs/nope/video"),
    ("POST", "/jobs/nope/stop"),
]
LOCAL = "http://127.0.0.1:8765"


def _request(client, method, path, **kwargs):
    if method == "POST":
        kwargs.setdefault("json", {"selection": {}})
    return client.request(method, path, **kwargs)


@pytest.mark.parametrize("method,path", ROUTES)
def test_a_foreign_host_is_refused_on_every_route(composer, method, path):
    """DNS rebinding: the right token, the wrong Host."""
    rebound = TestClient(create_app(composer), base_url="http://evil.example:8765")
    rebound.cookies.set(cookie_name(8765), composer.token)
    response = _request(
        rebound,
        method,
        path + f"?token={composer.token}",
        headers={"X-Composer-Token": composer.token},
    )
    assert response.status_code == 403
    assert "loopback" in response.json()["error"]


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_route_needs_the_token(composer, method, path):
    anonymous = TestClient(create_app(composer), base_url=LOCAL)
    assert _request(anonymous, method, path).status_code == 403


def test_the_page_trades_the_url_token_for_a_cookie(composer):
    browser = TestClient(create_app(composer), base_url=LOCAL)
    assert browser.get("/?token=wrong").status_code == 403
    login = browser.get(f"/?token={composer.token}", follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/"  # the token leaves the address bar
    cookie = login.headers["set-cookie"]
    assert cookie.startswith(f"{cookie_name(8765)}={composer.token};")
    for attribute in ("HttpOnly", "SameSite=strict", "Path=/"):
        assert attribute.lower() in cookie.lower(), attribute
    # The cookie alone opens every page and asset...
    assert browser.get("/").status_code == 200
    assert browser.get("/catalog.json").status_code == 200
    assert browser.get("/assets/composer/composer.js").status_code == 200
    # ...and the beacons, which cannot set headers.
    assert browser.post("/preview/stop").json()["ok"]
    # A JSON endpoint also wants the header (double submit).
    assert browser.post("/validate", json={"selection": {}}).status_code == 403
    response = browser.post(
        "/validate",
        json={"selection": {}},
        headers={"X-Composer-Token": composer.token},
    )
    assert response.status_code == 200


def test_the_cookie_is_per_port(composer):
    """Cookies ignore the port: two composers on one host must not share one."""
    other = TestClient(create_app(composer), base_url="http://127.0.0.1:9000")
    other.cookies.set(cookie_name(8765), composer.token)
    assert other.get("/catalog.json").status_code == 403


def test_serve_prints_the_tokenized_url(monkeypatch, capsys, catalog):
    import uvicorn
    import webbrowser

    import open_apps.compose.server as server

    opened = []
    monkeypatch.setattr(server, "build_catalog", lambda **_: catalog)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.setattr(server.threading, "Timer", lambda _t, fn, args: _Now(fn, args))
    server.serve(port=8765, open_browser=True)
    out = capsys.readouterr().out
    url = re.search(r"http://127\.0\.0\.1:8765/\?token=\S+", out).group(0)
    assert opened == [url]


class _Now:
    def __init__(self, fn, args):
        self.fn, self.args = fn, args

    def start(self):
        self.fn(*self.args)


def test_a_cross_site_simple_post_is_refused(client):
    """text/plain needs no CORS preflight, so it must not be accepted."""
    response = client.post(
        "/run", content=b'{"selection": {}}', headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 415


# --------------------------------------------------------------------------
# Validate
# --------------------------------------------------------------------------


def test_validate_accepts_a_known_good_override(client):
    response = post(
        client,
        "/validate",
        {"choices": {"device": ["phone"]}, "extra": ["apps.todo.title=Super Todo"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] and result["config"] == "config"
    diff = {d["key"]: d for d in result["diff"]}
    assert diff["device.name"]["value"] == '"phone"'
    assert diff["apps.todo.title"]["value"] == '"Super Todo"'
    assert "agent.agent_name" not in diff  # untouched keys are not listed


def test_validate_maps_a_hydra_error_to_its_override(client):
    response = post(
        client,
        "/validate",
        {"choices": {"device": ["phone"]}, "extra": ["apps.todo.no_such_key.x=1"]},
    )
    assert response.status_code == 422
    result = response.json()
    assert not result["ok"]
    assert result["override"] == "apps.todo.no_such_key.x=1"
    assert result["error"]


def test_validate_maps_a_missing_option_to_its_override(client):
    response = post(client, "/validate", {"extra": ["+experiment=no_such_preset"]})
    assert response.status_code == 422
    assert response.json()["override"] == "+experiment=no_such_preset"


def test_validate_checks_a_sweep(client):
    sweep = {"choices": {"apps/theme": ["dark", "mono"]}}
    result = post(client, "/validate", sweep).json()
    assert result["ok"] and result["target"] == "sweep"
    assert result["config"] == "config_parallel_tasks"


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


def test_run_refuses_slurm_and_returns_the_command(client, composer):
    before = dict(composer.jobs)
    response = post(
        client,
        "/run",
        {"choices": {"mode": ["slurm_cluster"], "agent": ["gemma-4-2B-computer-use"]}},
    )
    assert response.status_code == 409
    result = response.json()
    assert result["refused"]
    assert (
        result["command"]
        == "AGENTS=gemma-4-2B-computer-use sbatch scripts/conduct_slurm.sh"
    )
    assert composer.jobs == before  # nothing was started


def test_run_refuses_a_slurm_sweep_too(client, composer):
    sweep = {"choices": {"mode": ["slurm_cluster"], "apps/theme": ["dark", "mono"]}}
    response = post(client, "/run", sweep)
    assert response.status_code == 409
    assert response.json()["command"].startswith(
        "uv run launch_parallel_agents.py mode=slurm_cluster"
    )


@pytest.mark.parametrize(
    "selection",
    [
        # Forced target: renders `sbatch scripts/conduct_slurm.sh`.
        {"target": "slurm_pool"},
        {"target": "slurm_pool", "choices": {"agent": ["dummy"]}},
        # The config key launch_parallel_agents.py actually reads.
        {"choices": {"apps/theme": ["dark", "mono"]}, "extra": ["cluster=slurm"]},
        {"extra": ["cluster=slurm"]},
        # Quoted: Hydra unquotes it, a string match does not.
        {
            "choices": {"apps/theme": ["dark", "mono"]},
            "extra": ["mode='slurm_cluster'"],
        },
        {"extra": ['mode="slurm_cluster"']},
        {"extra": ["cluster='slurm'"]},
    ],
    ids=json.dumps,
)
def test_run_refuses_every_spelling_of_slurm(client, composer, selection):
    before = dict(composer.jobs)
    response = post(client, "/run", selection)
    assert response.status_code == 409, response.text
    result = response.json()
    assert result["refused"] and result["command"]
    assert composer.jobs == before


def test_run_refusal_sees_a_hydra_launcher_plugin(catalog):
    """Hydra's own overrides are refused at the door (see Token validation);
    the resolved-plan check would refuse a SLURM launcher even past it."""
    extra = ["hydra.mode=MULTIRUN", "hydra/launcher=submitit_slurm"]
    assert "launcher" in run_refusal(Selection(extra=extra), catalog)


def test_run_refusal_allows_the_local_scripts(catalog):
    assert run_refusal(Selection(), catalog) is None
    assert (
        run_refusal(Selection(choices={"apps/theme": ["dark", "mono"]}), catalog)
        is None
    )
    assert (
        run_refusal(Selection(choices={"agent": ["dummy", "claude_4_sonnet"]}), catalog)
        is None
    )
    assert run_refusal(Selection(extra=["mode=local"]), catalog) is None


def test_run_refusal_needs_a_known_program(catalog, monkeypatch):
    import open_apps.compose.server as server

    real = server.plan

    def sbatch_plan(selection, catalog):
        p = real(selection, catalog)
        p.argv = ["sbatch", *p.argv]
        p.first_override += 1
        return p

    monkeypatch.setattr(server, "plan", sbatch_plan)
    assert "sbatch" in run_refusal(Selection(), catalog)


# --------------------------------------------------------------------------
# Token validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "selection",
    [
        {"extra": ["apps.todo.title=x; rm -rf ~"]},
        {"extra": ["apps.todo.title=$(whoami)"]},
        {"extra": ["apps.todo.title=`id`"]},
        {"extra": ["apps.todo.title=a\nb"]},
        {"extra": ["seed=1 && curl evil"]},
        {"env": ["VLLM_HOST=node|tee"]},
        {"env": ["LD_PRELOAD=/tmp/x.so"]},
        {"env": ["PATH=/tmp"]},
        {"sbatch_args": ["--qos=q;id"]},
    ],
)
def test_token_validation_rejects_shell_metacharacters(catalog, client, selection):
    with pytest.raises(RequestError):
        checked_selection(selection, catalog)
    assert post(client, "/run", selection).status_code == 400


@pytest.mark.parametrize(
    "token",
    [
        # Hydra's own config: callbacks, launchers and sweepers are code.
        "+hydra.callbacks.x._target_=os.system",
        "hydra.mode=MULTIRUN",
        "hydra/launcher=submitit_slurm",
        "~hydra.output_subdir",
        "++hydra.job.chdir=True",
        "hydra=x",
        "apps/theme@hydra.callbacks=dark",
        # Instantiation keys anywhere: agent, tasks, browsergym_env_args and
        # parallel_tasks all go through hydra.utils.instantiate.
        "agent._target_=os.system",
        "+parallel_tasks._args_=[id]",
        "agent._partial_=true",
        "browsergym_env_args._recursive_=false",
        "agent={_target_:os.system,_args_:[id]}",
        # Resolvers outside the safe set.
        "logs_dir=${oc.decode:'{a: 1}'}",
        "logs_dir=${oc.create:{}}",
        "logs_dir=${ oc.decode :x}",
        "apps.todo.title=${oc.env:HOME}${oc.decode:x}",
        "apps.todo.title=[${oc.dict.keys:apps}]",
    ],
)
def test_overrides_that_could_run_code_are_refused(catalog, client, token):
    with pytest.raises(RequestError):
        checked_selection({"extra": [token]}, catalog)
    for path in ("/validate", "/run"):
        assert post(client, path, {"extra": [token]}).status_code == 400


@pytest.mark.parametrize(
    "token",
    [
        "logs_dir=${oc.env:HOME}/runs",
        "logs_dir=/tmp/${now:%Y-%m-%d}",
        "apps.todo.title=${oc.select:apps.todo.name,Todo}",
        "logs_dir=${hydra:runtime.cwd}/runs",
        "databases_dir=${logs_dir}/db",
        "apps.todo.title=target practice",
    ],
)
def test_safe_interpolations_pass(catalog, token):
    assert checked_selection({"extra": [token]}, catalog).extra == [token]


@pytest.mark.parametrize(
    "assignment",
    [
        "LD_PRELOAD=/tmp/x.so",
        "DYLD_INSERT_LIBRARIES=/tmp/x.dylib",
        "PATH=/tmp",
        "PYTHONSTARTUP=/tmp/x.py",
        "BROWSER=/tmp/x",
        "GIT_SSH_COMMAND=id",
        "NODE_OPTIONS=--require=/tmp/x.js",
        "WANDB_BASE_URL=http://evil.example",
        "WANDB_API_KEY=someone-elses",
        "UV_PYTHON=/tmp/python",
    ],
)
def test_env_outside_the_allowlist_is_refused(catalog, client, assignment):
    with pytest.raises(RequestError):
        checked_selection({"env": [assignment]}, catalog)
    assert post(client, "/run", {"env": [assignment]}).status_code == 400


@pytest.mark.parametrize(
    "assignment",
    [
        "AGENTS=dummy",
        "COUNT=2",
        "MAX_PARALLEL=1",
        "HEADLESS=False",
        "LOG_DIR=runs",
        "WANDB_GROUP=g",
        "WANDB_PROJECT=p",
        "WANDB_MODE=offline",
        "VLLM_HOST=node-007",
    ],
)
def test_allowlisted_env_passes(catalog, assignment):
    assert checked_selection({"env": [assignment]}, catalog).env == [assignment]


def test_the_env_allowlist_covers_what_the_scripts_read():
    """Derived from the scripts: every ``${NAME:-default}`` they read is
    settable (and nothing else from them, beyond the documented W&B list)."""
    read = set()
    for script in (REPO_ROOT / "scripts").glob("conduct*.sh"):
        read |= set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*):-", script.read_text()))
    assert read and read <= ENV_ALLOWLIST
    assert ENV_ALLOWLIST - read <= WANDB_ENV


@pytest.mark.parametrize(
    "selection",
    [
        {"choices": {"agent": ["dummy; id"]}},
        {"choices": {"made_up": ["x"]}},
        {"choices": {"seed": ["1"]}},
        {"choices": {"use_wandb": [1]}},
        {"choices": {"task_name": ["not_a_task"]}},
        {"extra": ["not an override"]},
        {"target": "cluster"},
        {"count": -1},
    ],
)
def test_values_must_come_from_the_catalog(catalog, selection):
    with pytest.raises(RequestError):
        checked_selection(selection, catalog)


def test_interpolations_and_known_env_pass(catalog):
    sel = checked_selection(
        {
            "choices": {"agent": ["dummy", "claude_4_sonnet"], "seed": [3]},
            "extra": ["logs_dir=${oc.env:HOME}/runs", "apps.todo.title=Super Todo"],
            "env": ["VLLM_HOST=node-007"],
        },
        catalog,
    )
    p = plan(sel, catalog)
    assert p.env == {"AGENTS": "dummy claude_4_sonnet", "VLLM_HOST": "node-007"}
    assert p.argv[:1] == ["./scripts/conduct.sh"]
    # One argv word per override, quoting already undone: no shell needed.
    assert "apps.todo.title=Super Todo" in p.overrides


def test_single_view_is_the_first_cell(catalog):
    sweep = Selection(
        choices={"apps/theme": ["dark", "mono"], "mode": ["slurm_cluster"]},
        extra=["parallel_tasks.task_names=all", "apps.todo.title=T"],
    )
    view = single_view(sweep, catalog)
    assert plan(view, catalog).command == (
        "uv run launch_agent.py apps/theme=dark apps.todo.title=T"
    )


# --------------------------------------------------------------------------
# Jobs: stopping, errors, leaks
# --------------------------------------------------------------------------

#: A job that starts a grandchild in a session of its own -- what
#: AgentLauncher does with `uv run launch.py` -- then waits.
_SPAWNER = """
import subprocess, sys, time
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True
)
open(sys.argv[1], "w").write(str(child.pid))
time.sleep(120)
"""


def _gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False


def _open_paths() -> set[str]:
    return {f.path for f in psutil.Process().open_files()}


def test_stopping_a_job_kills_descendants_in_other_sessions(client, composer, tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    argv = [sys.executable, "-c", _SPAWNER, str(pid_file)]
    job = composer.start_job("run", argv, "fake job")
    grandchild = None
    try:
        deadline = time.monotonic() + 15
        while not pid_file.exists() or not pid_file.read_text():
            assert time.monotonic() < deadline, "the fake job never started"
            time.sleep(0.1)
        grandchild = int(pid_file.read_text())
        assert os.getpgid(grandchild) != job.process.pid  # its own session
        response = client.post(f"/jobs/{job.id}/stop")
        assert response.json() == {"ok": True}
        assert job.finished()
        assert _gone(grandchild), "the grandchild outlived its job"
    finally:
        if grandchild is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(grandchild, signal.SIGKILL)  # don't leak it if we failed


def test_a_job_log_is_closed_once_started(composer):
    job = composer.start_job("run", [sys.executable, "-c", "print('hi')"], "hi")
    job.process.wait(10)
    assert job.finished()
    assert str(job.log_path) not in _open_paths()
    assert "hi" in job.log_path.read_text()


def test_a_popen_failure_is_a_json_error_and_leaks_no_log(
    client, composer, monkeypatch
):
    import open_apps.compose.server as server

    def no_exec(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "launch_agent.py")

    before = dict(composer.jobs)
    opened_before = _open_paths()
    monkeypatch.setattr(server.subprocess, "Popen", no_exec)
    response = post(client, "/record", {})
    assert response.status_code == 500
    result = response.json()
    assert not result["ok"] and "could not start" in result["error"]
    assert composer.jobs == before
    assert _open_paths() <= opened_before


def test_a_hydra_error_is_a_json_error(client):
    response = post(client, "/run", {"extra": ["++mode=slurm_cluster"]})
    assert response.status_code == 422
    result = response.json()
    assert not result["ok"] and "force-add" in result["error"]
    response = post(client, "/preview", {"extra": ["apps.todo.no_such_key.x=1"]})
    assert response.status_code == 422
    assert "no_such_key" in response.json()["error"]


def test_a_launcher_exception_is_a_json_error(client, composer, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("the launcher fell over")

    monkeypatch.setattr(composer, "record", boom)
    response = post(client, "/record", {})
    assert response.status_code == 500
    assert response.json() == {
        "ok": False,
        "error": "RuntimeError: the launcher fell over",
    }


def test_shutdown_never_signals_a_reaped_job(catalog, tmp_path, monkeypatch):
    """Its pid may belong to an unrelated process by now."""
    import open_apps.compose.live as live

    composer = Composer(catalog, tmp_path)
    job = composer.start_job("run", [sys.executable, "-c", "pass"], "pass")
    job.process.wait(10)
    assert job.finished()  # reaped
    signalled = []
    monkeypatch.setattr(live.os, "killpg", lambda pgid, sig: signalled.append(pgid))
    composer.shutdown()
    assert job.process.pid not in signalled
    assert composer.jobs == {}


def test_a_stale_preview_is_stopped_but_a_background_tab_is_not(catalog, tmp_path):
    """Browsers throttle a background tab's timers to about once a minute."""

    class FakePreview:
        stopped = False

        def stop(self):
            self.stopped = True

    composer = Composer(catalog, tmp_path)
    try:
        assert PREVIEW_TTL_S >= 180
        preview = composer.preview = FakePreview()
        composer.preview_beat = time.monotonic() - 90
        assert not composer.expire_stale_preview()
        assert not preview.stopped
        composer.preview_beat = time.monotonic() - PREVIEW_TTL_S - 1
        assert composer.expire_stale_preview()
        assert preview.stopped and composer.preview is None
    finally:
        composer.shutdown()


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------


def _answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status == 200
    except OSError:
        return False


@pytest.mark.slow
def test_preview_starts_and_cleans_up_its_process_group(client, composer):
    first = post(client, "/preview", {"choices": {"device": ["phone"]}})
    assert first.status_code == 200, first.text
    first = first.json()
    assert first["device"] == "phone" and first["viewport"] == [390, 844]
    assert _answers(first["url"])
    assert group_alive(first["pgid"])

    # A second preview replaces the first: the old group is killed.
    second = post(client, "/preview", {"choices": {"apps/theme": ["dark"]}}).json()
    assert second["ok"], second
    assert not group_alive(first["pgid"])
    assert group_alive(second["pgid"])

    stop = client.post("/preview/stop")
    assert stop.json() == {"ok": True, "stopped": True}
    assert not group_alive(second["pgid"])
    deadline = time.monotonic() + 5
    while _answers(second["url"]) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _answers(second["url"])
