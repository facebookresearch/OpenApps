"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

``uv run -m open_apps.results_preview [DIR ...]`` and the post-sweep hook.
"""

from __future__ import annotations

import argparse
import shlex
import socket
import sys
from pathlib import Path
from typing import Optional, Sequence

from open_apps.results_preview.page import DEFAULT_PORT, Page, plural, serve
from open_apps.results_preview.runs import Results, collect

DEFAULT_LOGS = Path("log_outputs")
MODULE = "open_apps.results_preview"
#: Copies only what the preview reads from a cluster's logs to a laptop
#: (Hydra's overrides.yaml only exists if the run saved one).
#: (W&B's own config.yaml copies and the app databases are left behind.)
RSYNC = (
    "rsync -am --exclude='wandb/' --exclude='databases/' --include='*/' "
    "--include='summary_info.json' --include='config.yaml' --include='overrides.yaml' "
    "--exclude='*' "
    "<cluster>:<logs>/ ./runs/"
)
_SHOW_SKIPPED = 5


def command(paths: Sequence[str | Path]) -> str:
    return f"uv run -m {MODULE} " + " ".join(shlex.quote(str(p)) for p in paths)


def summary(results: Results) -> str:
    """The table as terminal lines."""
    width = max([len(r.label) for r in results.rows] + [12])
    lines = []
    for r in results.rows:
        if r.rate is None:
            rate = "    -  (no counted episodes)"
        else:
            lo, hi = r.ci
            rate = f"{r.rate * 100:5.1f}% [{lo * 100:.1f}-{hi * 100:.1f}]"
        steps = "-" if r.mean_steps is None else f"{r.mean_steps:.1f}"
        lines.append(
            f"{r.rank:>3}. {r.label:<{width}}  success {rate:<19}  "
            f"{plural(r.episodes, 'episode')}, {plural(r.tasks, 'task')}, "
            f"{r.errored} errored, {steps} mean steps"
        )
    extra = []
    if results.n_errored:
        extra.append(f"{results.n_errored} errored runs not counted")
    if results.duplicates:
        extra.append(f"{results.duplicates} re-runs replaced")
    if results.unfinished:
        extra.append(f"{len(results.unfinished)} unfinished")
    if results.skipped:
        extra.append(f"{len(results.skipped)} skipped")
    n = len(results.rows)
    lines.append(
        f"{plural(n, 'model')}, {plural(results.n_episodes, 'episode')}"
        + (f" ({', '.join(extra)})" if extra else "")
    )
    return "\n".join(lines)


def no_runs(paths: Sequence[str | Path]) -> str:
    return (
        f"No finished runs under {', '.join(map(str, paths))} on {socket.gethostname()}.\n"
        "A run is a BrowserGym experiment dir with a summary_info.json, under a job "
        "dir with the launcher's config.yaml. Run this where the logs are (on a "
        "cluster login node it prints the ssh command that brings the page to your "
        "laptop), or copy just those files down first:\n"
        f"  {RSYNC}"
    )


def launch(paths: Sequence[str | Path], open_browser: bool = True) -> None:
    """After a local sweep (``launch_parallel_agents.py``): serve the page
    and open it. Only from a terminal, since serving blocks until Ctrl-C; from
    a batch job it prints the command instead. Never raises: a preview must
    not fail the sweep that just finished."""
    if not sys.stdout.isatty():
        print(f"See every model's results with:\n  {command(paths)}")
        return
    try:
        results = collect(paths)
        if not results.rows:
            print(no_runs(paths))
            return
        print(summary(results))
        serve(Page(results), open_browser=open_browser)
    except Exception as exc:  # noqa: BLE001
        print(
            f"Results preview failed: {type(exc).__name__}: {exc}\n"
            f"Retry with:\n  {command(paths)}",
            file=sys.stderr,
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog=f"python -m {MODULE}",
        description=(
            "Rank the models behind every run under some directories by task "
            "success, one row per model. Serves the page and opens a browser; "
            "writes nothing unless --out is given."
        ),
    )
    parser.add_argument(
        "paths", nargs="*", type=Path,
        help=f"directories to search for runs, however nested (default: {DEFAULT_LOGS}/)",
    )  # fmt: skip
    parser.add_argument(
        "--agent", action="append", default=[], metavar="GLOB",
        help="only this model (display or agent config name; glob ok; repeatable)",
    )  # fmt: skip
    parser.add_argument(
        "--task", action="append", default=[], metavar="GLOB",
        help="only this task (glob ok, e.g. 'todo_*'; repeatable)",
    )  # fmt: skip
    parser.add_argument(
        "--out", type=Path, default=None, metavar="FILE",
        help="write the page to FILE (.html or .pdf) and exit instead of serving it",
    )  # fmt: skip
    parser.add_argument("--no-open", action="store_true", help="serve, but don't open a browser")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--background-seed", type=int, default=None,
        help="fix the halftone background (default: a new one each run)",
    )  # fmt: skip
    parser.add_argument("-v", "--verbose", action="store_true", help="list every skipped run")
    args = parser.parse_args(argv)

    paths = args.paths or [DEFAULT_LOGS]
    results = collect(paths, args.agent, args.task)
    shown = results.skipped if args.verbose else results.skipped[:_SHOW_SKIPPED]
    for where, why in shown:
        print(f"skipped {where}: {why}", file=sys.stderr)
    if len(results.skipped) > len(shown):
        print(
            f"... and {len(results.skipped) - len(shown)} more skipped (-v lists them)",
            file=sys.stderr,
        )
    if not results.rows:
        print(no_runs(paths), file=sys.stderr)
        return 1
    print(summary(results))
    page = Page(results, args.background_seed)
    if args.out:
        print(f"wrote {page.write(args.out)}")
        return 0
    serve(page, args.host, args.port, open_browser=not args.no_open)
    return 0
