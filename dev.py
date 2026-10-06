"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Hot-reloading development server.

    ./scripts/dev.sh                       # or:
    uv run uvicorn dev:app --reload --reload-dir src --reload-dir config --port 5001

Save any file under ``src/`` or ``config/`` and the server restarts and the
browser refreshes itself. Not for evals -- use ``launch.py`` for those.

## Why this file exists rather than `serve(reload=True)`

``serve()`` already defaults to ``reload=True``; ``launcher.launch_apps()``
turns it off deliberately, and just flipping it back gives a server that
reloads into a broken state. Two reasons, both worth knowing before touching
this:

**1. The app is configured imperatively, after import.** ``launch.py`` exposes
a bare ``app``; the routes and config are attached later by
``initialize_routes_and_configure_task()`` inside ``launcher.launch()``.
Uvicorn's reloader restarts the worker and re-imports the app from its import
string, which runs none of that -- a freshly imported ``app`` has 7 routes and
no ``.config`` at all, so the reloaded server would 404 every app and crash on
the first request that touches configuration.

This module fixes that by doing the configuration at *module level*, so
re-importing it is enough to rebuild a fully wired app.

**2. Re-seeding a database that already has rows fails.** ``set_environment()``
loops ``insert()`` over the configured rows without clearing first, so a second
run hits unique-constraint violations, and the code editor bails out with
"folder already exists. This is undesired!!!" instead of re-seeding. Since the
reloader restarts the process against the same directory on disk, every reload
after the first would hit both.

So this module wipes its own database directory before initializing. Each
reload therefore starts from the configured seed state -- which is also what an
eval episode does, so what you are looking at matches what an agent would get.
The cost is that runtime state does not survive a reload: pinned apps, theme
mode and any todos you ticked all reset. That is a fair trade while iterating
on rendering, and the wrong one if you are debugging state, in which case run
``launch.py`` normally.
"""
import os
import shutil
import tempfile
from pathlib import Path

from hydra import compose, initialize
from starlette.routing import WebSocketRoute

from fasthtml.live_reload import LiveReloadJs, live_reload_ws

from open_apps.apps.start_page.main import (
    app,
    initialize_routes_and_configure_task,
)

REPO_ROOT = Path(__file__).resolve().parent

#: Deliberately OUTSIDE the repository.
#:
#: The code editor seeds .py files into its database directory on every
#: startup. Put that anywhere the reloader can see and you get an infinite
#: loop: seed -> watcher fires -> restart -> seed. Observed exactly that with
#: the directory at ./.dev, and note it happened even though uvicorn reported
#: watching only src/ and config/ -- so confining it by reload-dir alone is not
#: something to rely on. A path outside the tree cannot be watched by accident.
DEV_DIR = Path(tempfile.gettempdir()) / "openapps-dev"

#: Empty by default so this boots whatever the current branch's default config
#: is. Hardcoding a layout or theme here would tie the dev server to config
#: groups that only exist on some branches, and it would fail to start on the
#: ones where they do not.
#:
#: Pass a space-separated list of Hydra overrides to pick something else:
#:     OPENAPPS_DEV_OVERRIDES="apps/theme=dark apps/todo/layout=kanban_board"
DEFAULT_OVERRIDES = ""


def _build() -> None:
    """Compose config, reset state, and wire up the app. Runs on every import."""
    overrides = os.environ.get("OPENAPPS_DEV_OVERRIDES", DEFAULT_OVERRIDES).split()

    # Wipe first. set_environment() cannot re-seed over existing rows, and the
    # reloader hands us the same directory every time.
    shutil.rmtree(DEV_DIR, ignore_errors=True)
    DEV_DIR.mkdir(parents=True, exist_ok=True)

    with initialize(version_base=None, config_path="config"):
        config = compose(
            config_name="config",
            overrides=[f"logs_dir={DEV_DIR}", "use_wandb=False", *overrides],
        )
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(config.apps)

    _install_live_reload()

    print(f"\n  dev server ready — overrides: {' '.join(overrides) or '(none)'}")
    print(f"  state dir: {DEV_DIR} (wiped on every reload)\n")


def _install_live_reload() -> None:
    """Add the ``/live-reload`` socket and the client snippet.

    FastHTML ships this as ``FastHTMLWithLiveReload``, a ``FastHTML`` subclass.
    Swapping the app's class just for development would mean the thing you are
    testing is not the thing that runs in an eval, so the two halves are
    attached to the existing app instead: a websocket route, and a script that
    reloads the page when that socket drops and comes back.

    Guarded because this module is re-imported on every reload in some
    execution paths, and a duplicate route or a second copy of the script would
    accumulate.
    """
    if not any(getattr(r, "path", None) == "/live-reload" for r in app.routes):
        app.routes.append(WebSocketRoute("/live-reload", live_reload_ws))
    if not any("live-reload" in str(h) for h in app.hdrs):
        app.hdrs.append(LiveReloadJs())


_build()


if __name__ == "__main__":
    # Convenience path so `python dev.py` works as well as the uvicorn command.
    # Passes the module import string, not the object, because the reloader
    # needs something it can re-import in the restarted worker.
    import uvicorn

    uvicorn.run(
        "dev:app",
        host="localhost",
        port=int(os.environ.get("OPENAPPS_DEV_PORT", 5001)),
        reload=True,
        reload_dirs=[str(REPO_ROOT / "src"), str(REPO_ROOT / "config")],
    )
