"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

MCP server exposing one OpenApps :class:`~open_apps.mcp.session.Session`.

This is the only module that imports the MCP SDK. It holds a single
process-global ``Session`` (process = session) created/torn down by the
FastMCP lifespan, and wraps the Session's methods as MCP tools.

Observations are returned vision-first: a screenshot image content block
plus a JSON metadata blob ``{url, reward, done, step_count, action_desc}``.

Configure via env (set by ``__main__``): ``OPENAPPS_APP`` (which app to
serve), ``OPENAPPS_MCP_HOST`` / ``OPENAPPS_MCP_PORT`` (HTTP/SSE bind).
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP, Image

from open_apps.mcp.actions import describe as _describe_actions
from open_apps.mcp.registry import list_variants as _list_variants
from open_apps.mcp.session import Session
from open_apps.tasks import list_task_keys
from open_apps.tasks import load_task as _load_task


# Process-global session (process = session).
_session: Session | None = None


def _require() -> Session:
    if _session is None or not _session.started:
        raise RuntimeError(
            "OpenApps session is not started (server still initializing)."
        )
    return _session


@asynccontextmanager
async def _lifespan(_server):
    global _session
    app_name = os.environ.get("OPENAPPS_APP", "todo")
    _session = Session(app_name)
    await _session.start()
    try:
        yield
    finally:
        try:
            await _session.close()
        finally:
            _session = None


_HOST = os.environ.get("OPENAPPS_MCP_HOST", "127.0.0.1")
_PORT = int(os.environ.get("OPENAPPS_MCP_PORT", "8000"))

mcp = FastMCP("OpenApps", lifespan=_lifespan, host=_HOST, port=_PORT)


def _obs_result(obs) -> list:
    """Screenshot as an image content block + metadata as a JSON text block."""
    return [Image(data=obs.screenshot_png, format="png"), json.dumps(obs.meta())]


# --------------------------------------------------------------------------
# Lifecycle.


@mcp.tool()
async def reset(seed: int | None = None):
    """Reset all apps to their initial state and reload the page.

    Returns the first observation (screenshot + metadata).
    """
    return _obs_result(await _require().reset(seed=seed))


@mcp.tool()
async def reconfigure(
    appearance: str | None = None,
    content: str | None = None,
    seed: int | None = None,
    extras: dict | None = None,
) -> str:
    """Swap appearance/content variant and seed (live) and re-seed app state."""
    await _require().reconfigure(
        appearance=appearance, content=content, seed=seed, extras=extras
    )
    return "reconfigured"


# --------------------------------------------------------------------------
# Actions.


@mcp.tool()
async def act(action: str, with_reward: bool = True):
    """Execute a BrowserGym action string -> observation (screenshot + metadata).

    Examples: ``mouse_click(375, 292)``, ``keyboard_type('Call Mom')``,
    ``keyboard_press('Enter')``, ``scroll(0, 300)``,
    ``mouse_drag_and_drop(10, 20, 200, 40)``, ``goto('/calendar')``.
    Call ``describe_actions()`` for the full action space.
    """
    return _obs_result(await _require().act(action, with_reward=with_reward))


@mcp.tool()
async def describe_actions() -> str:
    """Describe the action space accepted by ``act`` (BrowserGym coord + nav)."""
    return _describe_actions()


# --------------------------------------------------------------------------
# Observation.


@mcp.tool()
async def observe():
    """Current observation (screenshot + metadata) without taking an action."""
    return _obs_result(await _require().observe())


@mcp.tool()
async def screenshot() -> Image:
    """Current screenshot as a PNG image."""
    return Image(data=await _require().screenshot(), format="png")


@mcp.tool()
async def get_state() -> dict:
    """Structured cross-app state (the JSON behind reward computation)."""
    return await asyncio.to_thread(_require().get_state)


# --------------------------------------------------------------------------
# Reward / task.


@mcp.tool()
async def list_tasks(app: str | None = None) -> list[str]:
    """Task keys from all_tasks.yaml, optionally filtered to those starting in ``app``."""
    return list_task_keys(app)


@mcp.tool()
async def load_task(key: str) -> str:
    """Bind a task (for reward scoring) by its key; returns the goal string.

    Call ``reset`` after this (before acting) so the initial state is
    snapshotted for scoring.
    """
    sess = _require()
    task = _load_task(key)
    sess.set_task(task)
    return task.goal


@mcp.tool()
async def set_goal(goal: str) -> str:
    """Set a free-form goal with no automatic scoring (clears the bound task)."""
    _require().set_task(None)
    return goal


@mcp.tool()
async def get_reward() -> float:
    """Reward for the currently bound task (0.0 if none): 1.0 if complete else 0.0."""
    return await _require().get_reward()


# --------------------------------------------------------------------------
# Metadata / discovery.


@mcp.tool()
async def list_apps() -> list[str]:
    """App keys actually registered this process (Java-aware live set)."""
    return _require().appserver.registered_apps()


@mcp.tool()
async def list_variants(app: str, group: str) -> list[str]:
    """Variant stems for an app's ``appearance`` or ``content`` group."""
    return _list_variants(app, group)


@mcp.tool()
async def app_url(app: str | None = None) -> str:
    """Absolute URL of an app's landing page (defaults to the served app)."""
    return _require().appserver.url_for(app)


def run(transport: str = "stdio") -> None:
    """Serve over MCP. ``transport`` in {``stdio``, ``sse``, ``http``}."""
    if transport == "http":
        transport = "streamable-http"
    mcp.run(transport=transport)
