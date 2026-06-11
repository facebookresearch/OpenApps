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
import sys


class _StdoutToStderr:
    """Keep the stdio MCP JSONRPC channel (stdout, fd 1) private.

    The MCP stdio transport writes protocol frames to ``sys.stdout.buffer``.
    OpenApps app code (and uvicorn/Playwright) ``print()`` to stdout while
    handling requests, which interleaves with — and corrupts — those frames,
    desyncing the protocol and hanging the client. This proxy exposes the real
    stdout's binary ``buffer`` (so the transport still reaches the client) but
    routes every text ``write()`` to stderr, so stray prints can never break
    the protocol stream.
    """

    def __init__(self, real, err):
        self.buffer = real.buffer
        self._err = err

    def write(self, s):
        return self._err.write(s)

    def flush(self):
        return self._err.flush()

    def __getattr__(self, name):
        return getattr(self._err, name)


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

    # stdio uses stdout as the JSONRPC channel: shield it from app prints.
    if args.transport == "stdio":
        sys.stdout = _StdoutToStderr(sys.stdout, sys.stderr)

    # Imported after env is set so FastMCP picks up host/port at construction.
    from open_apps.mcp import server

    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
