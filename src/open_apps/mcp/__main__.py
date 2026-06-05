"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Entry point: ``python -m open_apps.mcp`` / ``open-apps-mcp``.

Serves one OpenApps app as an MCP environment. Process = session, so
spawn one process per parallel env.
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    p = argparse.ArgumentParser(
        prog="open-apps-mcp",
        description="Serve OpenApps as an MCP environment (one app per process).",
    )
    p.add_argument(
        "--app",
        default="todo",
        help="App to serve: todo, calendar, messages, map, codeeditor.",
    )
    p.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "http"],
        help="MCP transport (http = streamable-http).",
    )
    p.add_argument("--host", default="127.0.0.1", help="Bind host for HTTP/SSE.")
    p.add_argument("--port", type=int, default=8000, help="Bind port for HTTP/SSE.")
    args = p.parse_args()

    os.environ["OPENAPPS_APP"] = args.app
    os.environ["OPENAPPS_MCP_HOST"] = args.host
    os.environ["OPENAPPS_MCP_PORT"] = str(args.port)

    # Imported after env is set so FastMCP picks up host/port at construction.
    from open_apps.mcp import server

    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
