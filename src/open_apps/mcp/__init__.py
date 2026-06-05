"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

OpenApps as an environment-as-a-service.

This package exposes OpenApps to any consumer (world-model training, AI
agents, eval harnesses) over the Model Context Protocol, and as a plain
in-process API:

  - :class:`~open_apps.mcp.appserver.AppServer` — the control plane
    (FastHTML server thread + live Hydra config + reset/reconfigure/state).
  - :class:`~open_apps.mcp.session.Session` — a full env (AppServer + a
    browser + the action executor + screenshot/reward).
  - :func:`~open_apps.mcp.server.run` — serve a Session over MCP.

Only :mod:`open_apps.mcp.server` imports the MCP SDK; ``AppServer`` and
``Session`` are protocol-agnostic and importable without it.
"""

from __future__ import annotations


__all__ = ["AppServer", "Session", "run"]


def __getattr__(name: str):
    # Lazy re-exports so importing the package doesn't pull in playwright
    # (Session) or the mcp SDK (run) unless actually used.
    if name == "AppServer":
        from open_apps.mcp.appserver import AppServer

        return AppServer
    if name == "Session":
        from open_apps.mcp.session import Session

        return Session
    if name == "run":
        from open_apps.mcp.server import run

        return run
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
