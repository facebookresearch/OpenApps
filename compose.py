"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
The Config Composer, locally: the docs page's command builder plus Validate,
live Preview, Record and Run.

    uv run compose.py                  # opens http://127.0.0.1:8765
    uv run compose.py --port 9000 --no-browser

Binds 127.0.0.1 only. The logic lives in open_apps.compose.server.
"""
import argparse

from open_apps.compose.server import DEFAULT_PORT, serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Config Composer locally.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-browser",
        dest="open_browser",
        action="store_false",
        help="do not open a browser tab",
    )
    args = parser.parse_args()
    serve(port=args.port, open_browser=args.open_browser)


if __name__ == "__main__":
    main()
