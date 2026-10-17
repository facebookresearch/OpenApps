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

import time
import urllib.request

import pytest
from starlette.testclient import TestClient

from open_apps.compose.catalog import build_catalog
from open_apps.compose.command import Selection
from open_apps.compose.live import group_alive
from open_apps.compose.server import (
    Composer,
    RequestError,
    checked_selection,
    create_app,
    plan,
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
    assert f'token="{composer.token}"' in page.text
    assert "cdn." not in page.text  # nothing from outside (see open_apps.frontend)
    catalog = client.get("/catalog.json").json()
    assert catalog["capabilities"] == "local"
    assert catalog["groups"]["agent"]["options"]
    assert client.get("/assets/composer/composer.js").status_code == 200


def test_endpoints_need_the_token_and_a_loopback_host(composer, client):
    app = create_app(composer, allowed_hosts=("testserver",))
    with TestClient(app) as anonymous:
        response = anonymous.post("/validate", json={"selection": {}})
        assert response.status_code == 403
        # The beacon's spelling: token in the query string.
        stop = anonymous.post(f"/preview/stop?token={composer.token}")
        assert stop.status_code == 200
    with TestClient(create_app(composer)) as rebound:  # Host: testserver
        response = rebound.post(
            "/validate",
            json={"selection": {}},
            headers={"X-Composer-Token": composer.token},
        )
        assert response.status_code == 403
        assert "loopback" in response.json()["error"]


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
