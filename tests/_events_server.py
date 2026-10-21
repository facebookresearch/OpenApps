"""Shared helpers for the adversarial-event tests: the real app server behind a
``TestClient`` and the cross-app state read through it.

Not a conftest: only the event tests need it, and an explicit import keeps
the dependency visible.
"""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# The endpoints open_apps.state.get_current_state reads, keyed the same way.
STATE_ENDPOINTS = {
    "todo": "/todo_all",
    "calendar": "/calendar_all",
    "map": "/maps/landmarks",
    "messenger": "/messages_all",
    "codeeditor": "/codeeditor_all",
    "ui_library": "/uilibrary_all",
    "openbanking": "/openbanking_all",
    "online_shop": "/onlineshop_all",
}


def compose(overrides: list[str], config_name: str = "config"):
    from hydra import compose as hydra_compose
    from hydra import initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra

    if GlobalHydra.instance().is_initialized():
        GlobalHydra.instance().clear()
    with initialize_config_dir(version_base=None, config_dir=str(CONFIG_DIR)):
        return hydra_compose(config_name=config_name, overrides=overrides)


def start_apps(logs_dir: Path, overrides: list[str] = ()):
    """(re)initialize the start page server on a fresh database dir.

    A fresh ``logs_dir`` per call: the apps seed sqlite with fixed primary
    keys, so re-seeding an existing database collides on insert.
    """
    from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task

    config = compose([f"logs_dir={logs_dir}", *overrides])
    Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(config.apps)
    return config, TestClient(app)


def state_of(client: TestClient) -> dict:
    """``get_current_state`` over a TestClient (a 404 -- an app not served --
    reads as ``[]``, as ``safe_get_json`` does)."""
    out = {}
    for key, path in STATE_ENDPOINTS.items():
        response = client.get(path)
        out[key] = response.json() if response.status_code == 200 else []
    out["openbanking"] = out["openbanking"] or {}
    return out


def reset_apps(config) -> None:
    """Back to the seeded state (and no unread toasts) without re-creating the
    server: much cheaper than ``start_apps`` per test."""
    from open_apps.apps.start_page.main import reset_all_apps

    reset_all_apps(config.apps)
