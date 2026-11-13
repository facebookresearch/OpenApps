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
model across every task and run, ranked by its task success rate, in the same
table the docs site shows, over a faint halftone background drawn for each
run (``halftone.py``).

Nothing is written by default: it serves the page from memory and opens a
browser. The page has two small links, *Save as HTML* (one self-contained
file) and *Save as PDF*. On a cluster without a display it prints the
``ssh -L`` command that brings the page to your laptop. ``--out FILE``
(``.html`` or ``.pdf``) writes the file and exits instead.

What it is not: a submission check. Nothing is validated against a suite
lock, and a model with a handful of episodes is ranked next to one with a
full suite (its drill-down and the cell count under its name say how much it
covers). Re-runs of one cell are resolved the way ``pack`` resolves them (an
infrastructure error loses to its re-run, otherwise the later run wins), so
pointing it at a sweep and its re-run of crashed jobs counts each cell once.
To publish, ``pack`` and ``validate`` as docs/leaderboard.md describes.

``launch_parallel_agents.py`` calls :func:`launch` after a local sweep
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

#: Appended to the element's stylesheet, in the preview only: Meta's
#: Optimistic Display where it is installed (system fonts otherwise), a
#: neutral palette over the docs site's yellow, and a translucent sheet so the halftone shows
#: around the table. The docs page itself is unchanged.
PREVIEW_CSS = """
:host {
  --sans: "Optimistic Display", -apple-system, ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji";
  --page: rgba(255, 255, 255, 0.8);
  --ink: #15171a;
  --ink-2: #33373d;
  --muted: #5f646c;
  --eyebrow: #2f5e4e;
  --control: #ffffff;
  --control-hover: #f2f3f4;
  --line: #15171a;
  --line-soft: rgba(21, 23, 26, 0.16);
  --sel-bg: #15171a;
  --sel-ink: #ffffff;
  --chip: #f3f4f5;
  --row-hover: rgba(21, 23, 26, 0.03);
  --detail: rgba(247, 248, 249, 0.94);
}
:host([data-scheme="dark"]) {
  --page: rgba(17, 18, 20, 0.82);
  --ink: #eceef1;
  --ink-2: #cdd1d6;
  --muted: #9aa0a8;
  --eyebrow: #86c9ae;
  --control: #16181b;
  --control-hover: #1f2226;
  --line: #4b5058;
  --line-soft: rgba(236, 238, 241, 0.14);
  --sel-bg: #eceef1;
  --sel-ink: #111214;
  --chip: #1c1f23;
  --row-hover: rgba(236, 238, 241, 0.04);
  --detail: rgba(22, 24, 27, 0.94);
  --whisker: rgba(236, 238, 241, 0.35);
}
.olb {
  border: 1px solid var(--line-soft);
  -webkit-backdrop-filter: blur(1.5px);
  backdrop-filter: blur(1.5px);
}
"""

# The page around the element. The halftone tiles repeat on <html> (seamless,
# so the texture has no edge), and two fixed, viewport-sized glows on <body>
# break up the repeat. {placeholders} are filled by Page.html().
_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script>
  if (matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.classList.add("dark");
</script>
<style>
  :root {{ color-scheme: light dark; }}
  html {{ min-height: 100%; background: #f6f6f4 url("{tile_light}") repeat; image-rendering: pixelated; }}
  html.dark {{ background: #0f1012 url("{tile_dark}") repeat; }}
  body {{
    margin: 0; min-height: 100vh; box-sizing: border-box;
    padding: 1rem clamp(1rem, 4vw, 3rem) 2.5rem;
    background:
      radial-gradient(70vmax 50vmax at 10% -10%, rgba(255, 255, 255, 0.7), transparent 70%),
      radial-gradient(60vmax 60vmax at 110% 110%, rgba(255, 255, 255, 0.55), transparent 70%);
    background-attachment: fixed;
  }}
  html.dark body {{
    background:
      radial-gradient(70vmax 50vmax at 10% -10%, rgba(0, 0, 0, 0.55), transparent 70%),
      radial-gradient(60vmax 60vmax at 110% 110%, rgba(0, 0, 0, 0.45), transparent 70%);
    background-attachment: fixed;
  }}
  openapps-leaderboard {{ display: block; max-width: 1440px; margin: 0 auto; }}
  .save {{
    max-width: 1440px; margin: 0 auto 0.5rem; display: flex; justify-content: flex-end; gap: 1rem;
    font: 13px/1.4 "Optimistic Display", -apple-system, ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji";
  }}
  .save a {{ color: #5f646c; text-decoration: none; border-bottom: 1px dotted currentColor; }}
  .save a:hover, .save a:focus-visible {{ color: #15171a; }}
  html.dark .save a {{ color: #9aa0a8; }}
  html.dark .save a:hover, html.dark .save a:focus-visible {{ color: #eceef1; }}
  @media print {{ .save {{ display: none; }} }}
</style>
</head>
<body>
{toolbar}<openapps-leaderboard
  src="{data}"
  stylesheet="{css}"></openapps-leaderboard>
<script src="{js}"></script>
</body>
</html>
"""

# The two save links; only on the served page (a saved file has no server).
# PDF is rendered by the server; if it can't (no Chromium), the browser's own
# print dialog is the fallback, where "Save as PDF" is one click.
_TOOLBAR = """<nav class="save" aria-label="Save this leaderboard">
  <a href="/leaderboard.html" download="{name}.html">Save as HTML</a>
  <a href="/leaderboard.pdf" data-pdf>Save as PDF</a>
</nav>
<script>
  document.querySelector("[data-pdf]").addEventListener("click", async (event) => {{
    event.preventDefault();
    const link = event.currentTarget, label = link.textContent;
    link.textContent = "Rendering PDF…";
    try {{
      const response = await fetch(link.href);
      if (!response.ok) throw new Error(String(response.status));
      const url = URL.createObjectURL(await response.blob());
      const a = Object.assign(document.createElement("a"), {{ href: url, download: "{name}.pdf" }});
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    }} catch (e) {{
      window.print();
    }} finally {{
      link.textContent = label;
    }}
  }});
</script>
"""


def _data_url(mime: str, payload: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode()}"


class Page:
    """The leaderboard page for one document, with one halftone texture.

    The texture is drawn once, so the page, its saved HTML and its PDF all
    look the same. ``background_seed=None`` draws a new one each run.
    """

    def __init__(self, doc: dict, background_seed: Optional[int] = None):
        from open_apps.leaderboard.halftone import tile_png

        self.doc = doc
        assets = B.repo_root() / "docs" / "assets" / "leaderboard"
        self._js = (assets / "leaderboard.js").read_bytes()
        self._css = (assets / "leaderboard.css").read_bytes() + PREVIEW_CSS.encode()
        self._tiles = {
            scheme: _data_url("image/png", tile_png(scheme, background_seed))
            for scheme in ("light", "dark")
        }
        self._pdf: Optional[bytes] = None
        self.name = "openapps-leaderboard-" + str(doc.get("generated_at", ""))[:10]

    def html(self, toolbar: bool = False) -> str:
        """Self-contained: script, styles, data and texture are data: URLs,
        so it opens from disk (``file://``) and fetches nothing."""
        data = json.dumps(self.doc, default=str, allow_nan=False).encode()
        return _PAGE.format(
            title=TITLE,
            tile_light=self._tiles["light"],
            tile_dark=self._tiles["dark"],
            toolbar=_TOOLBAR.format(name=self.name) if toolbar else "",
            data=_data_url("application/json", data),
            css=_data_url("text/css", self._css),
            js=_data_url("text/javascript", self._js),
        )

    def pdf(self) -> bytes:
        """One tall PDF page as wide as the screen layout, rendered by
        Playwright's Chromium (the one BrowserGym already installs)."""
        if self._pdf is None:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                try:
                    page = browser.new_page(
                        viewport={"width": 1440, "height": 900}, color_scheme="light"
                    )
                    page.set_content(self.html(), wait_until="load")
                    # The table, or the empty state: either means it rendered.
                    page.wait_for_function(
                        "() => document.querySelector('openapps-leaderboard')"
                        "?.shadowRoot?.querySelector('.board, .empty')",
                        timeout=20_000,
                    )
                    height = page.evaluate("document.documentElement.scrollHeight")
                    self._pdf = page.pdf(
                        width="1440px",
                        height=f"{height + 2}px",
                        print_background=True,
                        page_ranges="1",
                    )
                finally:
                    browser.close()
        return self._pdf

    def write(self, out: Path) -> Path:
        """``out`` as HTML, or as PDF when it ends in ``.pdf``."""
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".pdf":
            out.write_bytes(self.pdf())
        else:
            out.write_text(self.html())
        return out


def render_html(doc: dict, background_seed: Optional[int] = None) -> str:
    return Page(doc, background_seed).html()


# --- serving ---------------------------------------------------------------------


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


def how_to_view(port: int) -> str:
    """What to run on a laptop to see a page served on a remote machine."""
    host = ssh_host()
    lines = [
        "No display here. From your laptop, forward the port and open the page:",
        f"  ssh -N -L {port}:localhost:{port} {host}",
        f"  open http://localhost:{port}/",
    ]
    if "OPENAPPS_SSH_HOST" not in os.environ:
        lines.append(
            f"  ({host} is this machine's own name; set OPENAPPS_SSH_HOST to your ssh alias for it)"
        )
    return "\n".join(lines)


def make_server(page: Page, host: str = "127.0.0.1", port: int = DEFAULT_PORT):
    """An HTTP server for ``page``, on ``port`` or the next free one.

    ``/`` is the page with its save links, ``/leaderboard.html`` the
    self-contained file and ``/leaderboard.pdf`` the PDF (rendered on first
    request); nothing else is served.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body: bytes, mime: str, filename=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{filename}"'
                )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 -- http.server's naming
            route = self.path.split("?")[0]
            if route in ("/", "/index.html"):
                self._send(
                    200, page.html(toolbar=True).encode(), "text/html; charset=utf-8"
                )
            elif route == "/leaderboard.html":
                self._send(
                    200, page.html().encode(), "text/html; charset=utf-8",
                    f"{page.name}.html",
                )  # fmt: skip
            elif route == "/leaderboard.pdf":
                try:
                    with lock:
                        body = page.pdf()
                except Exception as exc:  # noqa: BLE001 -- the page falls back to print
                    print(f"PDF render failed ({type(exc).__name__}: {exc}); "
                          "the page falls back to the print dialog", file=sys.stderr)  # fmt: skip
                    self._send(503, b"PDF rendering unavailable", "text/plain")
                    return
                self._send(200, body, "application/pdf", f"{page.name}.pdf")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args):  # quiet
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True

    for candidate in [*range(port, port + 20), 0]:
        try:
            return Server((host, candidate), Handler)
        except OSError:
            continue
    raise OSError(f"no free port from {port}")


def serve(
    page: Page,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    """Serve ``page`` and open it (or say how to, without a display) until
    Ctrl-C. Nothing is written to disk."""
    server = make_server(page, host, port)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::") else host
    url = f"http://{shown}:{server.server_port}/"
    print(f"Leaderboard at {url}  (Ctrl-C to stop)")
    if not (open_browser and _has_display() and webbrowser.open(url)):
        print(how_to_view(server.server_port))
    sys.stdout.flush()  # before blocking: under nohup or a pipe it is buffered
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


def summary(result: Preview) -> str:
    rows = [r for tab in result.doc["suites"].values() for r in tab["rows"]]
    lines = []
    for suite, tab in sorted(result.doc["suites"].items()):
        lines.append(f"[{suite}]")
        for r in tab["rows"]:
            lines.append(
                f"  {r['rank']:>2}. {r['agent']:<32} success {LB._pct(r['success']):<16} "
                f"({r['n_episodes']} episodes, {r['covered']}/{r['n_cells']} cells)"
            )
    extra = []
    if result.duplicates:
        extra.append(f"{result.duplicates} re-run duplicates dropped")
    if result.skipped:
        extra.append(f"{len(result.skipped)} runs skipped")
    lines.append(
        f"{len(rows)} models, {result.n_episodes} episodes"
        + (f" ({', '.join(extra)})" if extra else "")
    )
    return "\n".join(lines)


def _no_runs(paths: Sequence[str | Path]) -> str:
    return (
        f"no scorable runs under {', '.join(map(str, paths))} on {socket.gethostname()}. "
        "Run this where the logs are (e.g. on the cluster); without a display it "
        "prints the ssh command that brings the page to your laptop."
    )


def launch(paths: Sequence[str | Path], open_browser: bool = True) -> None:
    """After a sweep (``launch_parallel_agents.py``): serve the leaderboard
    and open it. Only from a terminal, since serving blocks until Ctrl-C; a
    batch job prints the command instead. Never raises: a preview must not
    fail the sweep that just finished."""
    command = "uv run -m open_apps.leaderboard.preview " + " ".join(map(str, paths))
    if not sys.stdout.isatty():
        print(f"See the leaderboard with:\n  {command}")
        return
    try:
        result = preview(paths)
        if not result.n_episodes:
            print(_no_runs(paths))
            return
        print(summary(result))
        serve(Page(result.doc), open_browser=open_browser)
    except Exception as exc:  # noqa: BLE001
        print(
            f"Leaderboard preview failed: {type(exc).__name__}: {exc}\n"
            f"Retry with:\n  {command}",
            file=sys.stderr,
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Show a leaderboard (one row per model, across every task) of the runs "
            "under a directory. Serves it and opens a browser; writes nothing unless "
            "--out is given."
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
        "--out", type=Path, default=None, metavar="FILE",
        help="write the page to FILE (.html, or .pdf) and exit instead of serving it",
    )  # fmt: skip
    parser.add_argument(
        "--json", type=Path, default=None, help="also write the leaderboard JSON here"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="serve, but don't open a browser"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--n-boot", type=int, default=LB.N_BOOT)
    parser.add_argument("--seed", type=int, default=LB.BOOT_SEED, help="bootstrap seed")
    parser.add_argument(
        "--background-seed", type=int, default=None,
        help="fix the halftone background (default: a new one each run)",
    )  # fmt: skip
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="list every skipped run"
    )
    args = parser.parse_args(argv)

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
        print(_no_runs(paths), file=sys.stderr)
        return 1
    print(summary(result))
    if args.json:
        LB.write(result.doc, args.json)
    page = Page(result.doc, args.background_seed)
    if args.out:
        print(f"wrote {page.write(args.out)}")
        return 0
    serve(page, args.host, args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    sys.exit(main())
