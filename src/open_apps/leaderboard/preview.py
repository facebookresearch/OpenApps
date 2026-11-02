"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

A local leaderboard over your own runs: no bundle, no PR, no docs build.

    uv run -m open_apps.leaderboard.preview                      # everything under log_outputs/
    uv run -m open_apps.leaderboard.preview /checkpoint/$USER/logs/open_apps
    uv run -m open_apps.leaderboard.preview log_outputs --agent Qwen3.6-27B --task 'todo_*'
    uv run -m open_apps.leaderboard.preview alignment_aggregate.json   # a table you copied down

Point it at one directory and it finds every run below it: each
``episode.json`` (or a finished run without one, rebuilt the way ``pack``
does), however the sweeps under it are nested. The result is one row per
model across every task and run, ranked by its aligned rate, in the same
table the docs site shows. It is written as one self-contained HTML file
(page, styles and data inlined), so on a cluster you can copy that single
file to your laptop and open it, or pass ``--serve`` and forward the port.

What it is not: a submission check. Nothing is validated against a suite
lock, and a model with a handful of episodes is ranked next to one with a
full suite (its drill-down and the cell count under its name say how much it
covers). Re-runs of one cell are resolved the way ``pack`` resolves them (an
infrastructure error loses to its re-run, otherwise the later run wins), so
pointing it at a sweep and its re-run of crashed jobs counts each cell once.
To publish, ``pack`` and ``validate`` as docs/leaderboard.md describes.

``launch_parallel_agents.py`` calls :func:`open_preview` after a local sweep
(``open_leaderboard=false`` turns it off).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import fnmatch
import functools
import json
import os
import socket
import sys
import webbrowser
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from open_apps.leaderboard import build as LB
from open_apps.leaderboard import bundle as B

DEFAULT_OUT = Path("leaderboard_preview.html")
DEFAULT_LOGS = Path("log_outputs")
DEFAULT_PORT = 8765
# The tab for runs that were not launched with ``benchmark=<suite>``.
LOCAL_SUITE = "local"
TITLE = "OpenApps leaderboard · local preview"
# Unfinished runs pile up in a logs dir; list a few, count the rest.
_SHOW_SKIPPED = 5


@dataclass
class _Group:
    """One leaderboard row in the making: a model under one termination mode
    in one suite."""

    agent: str
    termination: str
    suite: str
    collected: list = field(default_factory=list)  # pack.Collected
    rows: list[dict] = field(default_factory=list)  # pre-scored (aggregate JSON)
    origins: set[str] = field(default_factory=set)


@dataclass
class Preview:
    doc: dict
    n_episodes: int
    skipped: list[tuple[str, str]]
    duplicates: int


# --- discovery -------------------------------------------------------------------


def _is_aggregate(path: Path) -> Optional[dict]:
    """The parsed file if ``path`` is an ``alignment.aggregate`` table (it
    keeps its per-episode ``rows``), else None."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if (
        isinstance(data, dict)
        and isinstance(data.get("rows"), list)
        and "agents" in data
    ):
        return data
    return None


def _matches(value: Optional[str], patterns: Sequence[str]) -> bool:
    return not patterns or any(fnmatch.fnmatchcase(str(value), p) for p in patterns)


def _run_root(origin: Path, root: Path) -> str:
    """The sweep an episode came from: the first directory below the path
    the user gave, or that path when it is the sweep itself (its children
    are the launcher's numbered job dirs, or the episode dirs)."""
    try:
        rel = Path(origin).resolve().relative_to(root.resolve())
    except ValueError:
        return str(origin)
    if len(rel.parts) < 2 or rel.parts[0].isdigit():
        return str(root)
    return str(root / rel.parts[0])


def discover(
    paths: Sequence[str | Path],
    agents: Sequence[str] = (),
    tasks: Sequence[str] = (),
) -> tuple[dict[tuple, _Group], list[tuple[str, str]]]:
    """Every run under ``paths``, grouped by (suite, model, termination)."""
    from open_apps.leaderboard.pack import collect

    groups: dict[tuple, _Group] = {}
    skipped: list[tuple[str, str]] = []

    def group(agent: Any, mode: Any, suite: Any) -> _Group:
        from open_apps.alignment.aggregate import DEFAULT_TERMINATION

        agent = str(agent or "unknown")
        mode = str(mode or DEFAULT_TERMINATION)
        suite = str(suite or LOCAL_SUITE)
        key = (suite, agent, mode)
        if key not in groups:
            groups[key] = _Group(agent, mode, suite)
        return groups[key]

    for root in map(Path, paths):
        if root.is_file():
            table = _is_aggregate(root)
            if table is not None:
                for row in table["rows"]:
                    if _matches(row.get("agent"), agents) and _matches(
                        row.get("task_name"), tasks
                    ):
                        g = group(row.get("agent"), row.get("task_termination"), None)
                        g.rows.append(row)
                        g.origins.add(str(root))
                continue
            # A single episode.json: collect its directory.
            root = root.parent
        if not root.is_dir():
            skipped.append((str(root), "no such directory"))
            continue
        collected, bad = collect([root])
        skipped += [(str(p), why) for p, why in bad]
        for c in collected:
            ident = c.record.identity
            if not (
                _matches(ident.agent_name, agents) and _matches(ident.task_name, tasks)
            ):
                continue
            g = group(ident.agent_name, ident.task_termination, ident.suite_version)
            g.collected.append(c)
            g.origins.add(_run_root(Path(c.origin), root))
    return groups, skipped


# --- the document ------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def _suite_blocks(
    suite: str, override: Optional[str] = None
) -> Optional[dict[tuple, str]]:
    """``{cell key: block}`` for a suite in config/benchmark (or ``override``,
    a suite YAML whose file name is the suite's), else None."""
    if suite == LOCAL_SUITE:
        return None
    from open_apps.leaderboard.suite import suite_cells

    if override and Path(override).stem == suite:
        suite = str(override)
    try:
        return {c.key: c.block for c in suite_cells(suite)}
    except Exception:  # noqa: BLE001 -- an unknown or old suite name just has no blocks
        return None


def _scored(
    group: _Group, blocks: Optional[dict[tuple, str]]
) -> tuple[list[dict], int, int, list[tuple[str, str]]]:
    """``(rows, cells covered, duplicates dropped, skipped)`` for a group."""
    from open_apps.alignment.aggregate import row_from_record
    from open_apps.leaderboard.pack import dedupe

    by_key, dupes = dedupe(group.collected)
    rows, skipped = list(group.rows), []
    for key, c in sorted(by_key.items()):
        try:
            row = row_from_record(c.record)
        # One unscorable record must not sink the page.
        except Exception as exc:  # noqa: BLE001
            skipped.append(
                (str(c.origin), f"scorer failed: {type(exc).__name__}: {exc}")
            )
            continue
        row["block"] = blocks.get(key) if blocks else None
        rows.append(row)
    covered = len(set(by_key) & set(blocks)) if blocks else len(rows)
    return rows, covered, len(dupes), skipped


def _date(group: _Group) -> Optional[str]:
    stamps = [c.recency[0] for c in group.collected if c.recency]
    if not stamps:
        return None
    return dt.datetime.fromtimestamp(max(stamps), tz=dt.timezone.utc).strftime(
        "%Y-%m-%d"
    )


def preview(
    paths: Sequence[str | Path],
    agents: Sequence[str] = (),
    tasks: Sequence[str] = (),
    n_boot: int = LB.N_BOOT,
    seed: int = LB.BOOT_SEED,
    when: Optional[str] = None,
    suite: Optional[str | Path] = None,
) -> Preview:
    """The leaderboard document for every run under ``paths``. ``suite``
    is a suite YAML outside config/benchmark (records name it by its file
    name)."""
    groups, skipped = discover(paths, agents, tasks)
    # A model run under both termination modes gets one row per mode (they
    # measure different things; see aggregate()), labelled with the mode.
    modes: dict[tuple, set] = defaultdict(set)
    for name, agent, mode in groups:
        modes[(name, agent)].add(mode)

    tabs: dict[str, dict] = {}
    n_episodes = duplicates = 0
    for (name, agent, mode), group in sorted(groups.items()):
        blocks = _suite_blocks(name, str(suite) if suite else None)
        rows, covered, dupes, bad = _scored(group, blocks)
        skipped += bad
        duplicates += dupes
        if not rows:
            continue
        split = len(modes[(name, agent)]) > 1
        label = f"{agent} ({mode})" if split else agent
        for r in rows:
            r["agent"] = label
        row = {
            "id": f"{agent}@{mode}" if split else agent,
            "org": None,
            "agent": label,
            "model": None,
            "date": _date(group),
            "verified": False,
            "bundle": None,
            "composer_agent": LB.composer_agent(agent),
            "wandb_report": None,
            "videos": [],
            "n_episodes": len(rows),
            "covered": covered,
            "n_cells": len(blocks) if blocks else len(rows),
            # Local-only: where the episodes came from (the page lists them
            # in the drill-down instead of bundle links).
            "logs": sorted(group.origins),
            "duplicates_dropped": dupes,
            **LB.scored_columns(rows, label, n_boot, seed),
        }
        n_episodes += len(rows)
        tabs.setdefault(
            name,
            {
                "suite": name,
                "n_cells": len(blocks) if blocks else None,
                "rows": [],
                "partial": [],
                "invalid": [],
            },
        )["rows"].append(row)
    for tab in tabs.values():
        tab["rows"] = LB._rank(tab["rows"])
    doc = LB.document(tabs, when or LB.generated_at(), n_boot, seed)
    doc["title"] = TITLE
    doc["local"] = True
    return Preview(doc, n_episodes, skipped, duplicates)


# --- the page ----------------------------------------------------------------------


def _data_url(mime: str, payload: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode()}"


def render_html(doc: dict) -> str:
    """One file with the docs site's ``<openapps-leaderboard>`` element, its
    stylesheet and ``doc`` inlined as data: URLs, so it opens from disk
    (``file://``) with no server and nothing fetched from the network."""
    assets = B.repo_root() / "docs" / "assets" / "leaderboard"
    js = (assets / "leaderboard.js").read_bytes()
    css = (assets / "leaderboard.css").read_bytes()
    data = json.dumps(doc, default=str, allow_nan=False).encode()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE}</title>
<script>
  if (matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.classList.add("dark");
</script>
<style>
  body {{ margin: 0; padding: 1.5rem clamp(1rem, 4vw, 3rem); background: #faf6e8; }}
  html.dark body {{ background: #15140f; }}
  openapps-leaderboard {{ display: block; max-width: 1440px; margin: 0 auto; }}
</style>
</head>
<body>
<openapps-leaderboard
  src="{_data_url("application/json", data)}"
  stylesheet="{_data_url("text/css", css)}"></openapps-leaderboard>
<script src="{_data_url("text/javascript", js)}"></script>
</body>
</html>
"""


def write_html(doc: dict, out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(doc))
    return out


def _has_display() -> bool:
    return sys.platform in ("darwin", "win32") or bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def ssh_host() -> str:
    """The name your laptop reaches this machine by. A login node's own
    hostname is often an internal one (a pod or node name) that only
    resolves inside the cluster, so ``$OPENAPPS_SSH_HOST`` (e.g. the
    ``fair-sc-3`` alias from your ssh config) wins when set."""
    return os.environ.get("OPENAPPS_SSH_HOST") or socket.gethostname()


def how_to_view(out: Path, port: int = DEFAULT_PORT) -> str:
    """What to run on a laptop to see a page written on a remote machine."""
    host = ssh_host()
    path = Path(out).resolve()
    note = (
        ""
        if "OPENAPPS_SSH_HOST" in os.environ
        else f"  ({host} is this machine's own name; set OPENAPPS_SSH_HOST to your ssh alias for it)\n"
    )
    return (
        f"To view it from your laptop:\n"
        f"  scp {host}:{path} . && open {path.name}\n"
        f"or serve it here and forward the port:\n"
        f"  uv run -m open_apps.leaderboard.preview --serve-only {path} --port {port}\n"
        f"  ssh -L {port}:localhost:{port} {host}   # then open http://localhost:{port}/\n"
        + note
    ).rstrip("\n")


def serve(out: Path, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    """Serve the page at ``http://host:port/`` until interrupted."""
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    out = Path(out).resolve()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(out.parent), **kwargs)

        def do_GET(self):  # noqa: N802 -- http.server's naming
            if self.path in ("/", "/index.html"):
                self.path = "/" + out.name
            if self.path.split("?")[0] != "/" + out.name:
                self.send_error(404)
                return
            super().do_GET()

        def log_message(self, *args):  # quiet
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving {out.name} at http://{host}:{port}/ (Ctrl-C to stop)")
    print(f"From your laptop: ssh -L {port}:localhost:{port} {ssh_host()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def summary(result: Preview, out: Path) -> str:
    rows = [r for tab in result.doc["suites"].values() for r in tab["rows"]]
    lines = []
    for suite, tab in sorted(result.doc["suites"].items()):
        lines.append(f"[{suite}]")
        for r in tab["rows"]:
            aligned = r["aligned"]["rate"]
            success = r["success"]["rate"]
            fmt = lambda v: "-" if v is None else f"{v * 100:5.1f}%"  # noqa: E731
            lines.append(
                f"  {r['rank']:>2}. {r['agent']:<32} aligned {fmt(aligned)}  "
                f"success {fmt(success)}  ({r['n_episodes']} episodes, "
                f"{r['covered']}/{r['n_cells']} cells)"
            )
    extra = []
    if result.duplicates:
        extra.append(f"{result.duplicates} re-run duplicates dropped")
    if result.skipped:
        extra.append(f"{len(result.skipped)} runs skipped")
    lines.append(
        f"wrote {out} ({len(rows)} models, {result.n_episodes} episodes"
        + (", " + ", ".join(extra) if extra else "")
        + ")"
    )
    return "\n".join(lines)


def open_preview(
    paths: Sequence[str | Path],
    out: Optional[Path] = None,
    open_browser: bool = True,
) -> Optional[Path]:
    """Build, write and (with a display) open the page; used by the launcher.
    Never raises: a preview must not fail the sweep that just finished."""
    try:
        out = Path(out or DEFAULT_OUT)
        result = preview(paths)
        if not result.n_episodes:
            print("No scorable episodes to show on the leaderboard preview.")
            return None
        write_html(result.doc, out)
        print(summary(result, out))
        if open_browser and _has_display() and webbrowser.open(out.resolve().as_uri()):
            return out
        print(how_to_view(out))
        return out
    except Exception as exc:  # noqa: BLE001
        print(
            f"Leaderboard preview failed: {type(exc).__name__}: {exc}", file=sys.stderr
        )
        return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Render a leaderboard (one row per model, across every task) from the "
            "runs under a directory, as one self-contained HTML file."
        )
    )
    parser.add_argument(
        "paths", nargs="*", type=Path,
        help=f"directories to search for runs, episode.json files, or aggregate JSON "
        f"tables (default: {DEFAULT_LOGS}/)",
    )  # fmt: skip
    parser.add_argument(
        "--agent", action="append", default=[], metavar="NAME",
        help="only this model (agent_name; glob ok; repeatable)",
    )  # fmt: skip
    parser.add_argument(
        "--task", action="append", default=[], metavar="NAME",
        help="only this task (glob ok, e.g. 'todo_*'; repeatable)",
    )  # fmt: skip
    parser.add_argument(
        "--suite", default=None, metavar="YAML",
        help="a suite YAML outside config/benchmark, for coverage and the per-axis breakdown",
    )  # fmt: skip
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help=f"default: {DEFAULT_OUT}"
    )
    parser.add_argument(
        "--json", type=Path, default=None, help="also write the leaderboard JSON here"
    )
    parser.add_argument(
        "--open", action="store_true", help="open the page in a browser"
    )
    parser.add_argument(
        "--serve", action="store_true", help="serve the page after writing it"
    )
    parser.add_argument(
        "--serve-only", type=Path, default=None, metavar="HTML",
        help="serve an already written page; build nothing",
    )  # fmt: skip
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--n-boot", type=int, default=LB.N_BOOT)
    parser.add_argument("--seed", type=int, default=LB.BOOT_SEED)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="list every skipped run"
    )
    args = parser.parse_args(argv)

    if args.serve_only:
        serve(args.serve_only, args.host, args.port)
        return 0

    paths = args.paths or [DEFAULT_LOGS]
    result = preview(
        paths, args.agent, args.task, args.n_boot, args.seed, suite=args.suite
    )
    shown = result.skipped if args.verbose else result.skipped[:_SHOW_SKIPPED]
    for where, why in shown:
        print(f"skipped {where}: {why}", file=sys.stderr)
    if len(result.skipped) > len(shown):
        print(
            f"... and {len(result.skipped) - len(shown)} more skipped (-v lists them)",
            file=sys.stderr,
        )
    if not result.n_episodes:
        print(
            f"no scorable runs under {', '.join(map(str, paths))} on {socket.gethostname()}. "
            "Run this where the logs are (e.g. on the cluster) and copy the HTML it "
            "writes, not the logs.",
            file=sys.stderr,
        )
        return 1
    out = write_html(result.doc, args.out)
    if args.json:
        LB.write(result.doc, args.json)
    print(summary(result, out))
    if args.serve:
        serve(out, args.host, args.port)
    elif args.open and _has_display():
        webbrowser.open(out.resolve().as_uri())
    else:
        print(how_to_view(out, args.port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
