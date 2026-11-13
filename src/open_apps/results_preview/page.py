"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The results page: one self-contained HTML file, served from memory.

Everything -- styles, the few lines of script, the halftone background -- is
inline, so the saved file opens from disk and fetches nothing. Each model's
details page is part of the same file, shown in place of the table when the
URL ends in ``#model/<name>``, so the saved file has them too. The server
(:func:`serve`) answers ``/`` (the page with its two save links),
``/results.html`` (the file to keep) and ``/results.pdf`` (rendered by
Playwright's Chromium on first request), and nothing else.
"""

from __future__ import annotations

import base64
import html
import os
import re
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Sequence

from open_apps.results_preview import reproduce
from open_apps.results_preview.halftone import tile_png
from open_apps.results_preview.runs import ModelRow, Results, Run, Tally

DEFAULT_PORT = 8765
TITLE = "OpenApps results · local preview"
_SHOW_SOURCES = 8
_SHOW_ERRORS = 12
_SHOW_COMMANDS = 8


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _breakable(value: object) -> str:
    """Escaped, with line-break opportunities after ``_`` and ``,`` so a long
    task name wraps between its words rather than mid-word."""
    text = _e(value)
    for mark in ("_", ","):
        text = text.replace(mark, mark + "<wbr>")
    return text


def _pct(value: Optional[float], digits: int = 1) -> str:
    return "–" if value is None else f"{value * 100:.{digits}f}%"


def _steps(value: Optional[float]) -> str:
    return "–" if value is None else f"{value:.1f}"


def _rate_cell(t: Tally, compact: bool = False) -> str:
    """The rate, a bar to it and the interval as a whisker over the bar."""
    if t.rate is None:
        return '<span class="none">no counted episodes</span>'
    lo, hi = t.ci
    bar = (
        f'<span class="bar" aria-hidden="true">'
        f'<span class="fill" style="width:{t.rate * 100:.2f}%"></span>'
        f'<span class="ci" style="left:{lo * 100:.2f}%;width:{(hi - lo) * 100:.2f}%"></span>'
        f"</span>"
    )
    rng = f"{lo * 100:.0f}–{hi * 100:.0f}"
    label = f"{_pct(t.rate)} success, 95% interval {rng}%"
    return (
        f'<span class="rate{" compact" if compact else ""}" title="{_e(label)}">'
        f'<span class="pct">{_pct(t.rate)}</span>{bar}'
        f'<span class="range">{rng}</span></span>'
    )


def _fold(title: str, count: str, body: str, warn: bool = False, open_: bool = False) -> str:
    """A section that folds: its heading and a count are the summary."""
    return (
        f'<section><details class="fold"{" open" if open_ else ""}><summary><h3>{_e(title)}</h3>'
        f'<span class="count{" warn" if warn else ""}">{_e(count)}</span></summary>'
        f"{body}</details></section>"
    )


def short_variation(label: str) -> str:
    """A variation label that reads at a glance: ``apps/`` dropped, the default
    theme left out beside other overrides, and one setting made in several apps
    said once (``content=german ×7 apps``)."""
    parts = [p.strip() for p in label.split(",") if p.strip()]
    if len(parts) > 1:
        parts = [p for p in parts if p != "apps/theme=default"]
    groups: dict[str, list[str]] = {}
    for part in parts:
        key, _, value = part.partition("=")
        bits = key.split("/")
        if len(bits) == 3 and bits[0] == "apps" and value:
            groups.setdefault(f"{bits[2]}={value}", []).append(bits[1])
        else:
            groups.setdefault(part.removeprefix("apps/"), [])
    out = []
    for setting, apps in groups.items():
        if len(apps) > 1:
            out.append(f"{setting} ×{len(apps)} apps")
        elif apps:
            out.append(f"{apps[0]} {setting}")
        else:
            out.append(setting)
    return ", ".join(out) or label


def _name_cell(label: str) -> str:
    return f'<td class="name">{_breakable(label)}</td>'


def _variation_cell(label: str) -> str:
    return f'<td class="name" title="{_e(label)}">{_breakable(short_variation(label))}</td>'


def _breakdown(title: str, tallies: Sequence[Tally], open_: bool = False) -> str:
    name = _variation_cell if title == "By variation" else _name_cell
    rows = "".join(
        f"<tr>{name(t.label)}"
        f"<td>{_rate_cell(t, compact=True)}</td>"
        f'<td class="num">{t.successes}/{t.episodes}</td>'
        f'<td class="num{" warn" if t.errored else ""}">{t.errored or ""}</td>'
        f'<td class="num">{_steps(t.mean_steps)}</td></tr>'
        for t in tallies
    )
    word = title.split()[-1]
    table = (
        f'<table class="sub">'
        f"<thead><tr><th>{_e(word.capitalize())}</th><th>Success</th>"
        f'<th class="num">Passed</th><th class="num">Errored</th>'
        f'<th class="num">Steps</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )
    return _fold(title, plural(len(tallies), word), table, open_=open_)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _grid(n_tasks: int, n_variations: int) -> str:
    return f"{plural(n_tasks, 'task')} × {plural(n_variations, 'variation')}"


def _reproduce(row: ModelRow, rid: str) -> str:
    """The Reproduce section: launcher commands that re-run the row's runs,
    each with a Copy button (hidden when printed)."""
    rep = reproduce.commands(row.runs)
    notes = []
    if rep.n_configs > 1:
        why = f" (they differ in {', '.join(rep.differs_in)})" if rep.differs_in else ""
        notes.append(f"These runs came from {rep.n_configs} configurations{_e(why)}: "
                     "one command each.")  # fmt: skip
    if len(rep.commands) > rep.n_configs:
        notes.append(
            "They do not form one tasks × variations grid, so the tasks that ran "
            "the same variations share a command."
        )
    guessed = sorted({c.agent for c in rep.commands if c.agent_guessed and c.agent})
    if guessed:
        notes.append(
            "The runs' configs do not record the agent config; "
            + ", ".join(f"<code>{_e(g)}</code>" for g in guessed)
            + " is the one in <code>config/agent/</code> with this model's name."
        )
    if any(c.agent_unknown for c in rep.commands):
        notes.append(
            "The runs' configs do not record the agent config: replace "
            f"<code>{_e(reproduce.UNKNOWN_AGENT)}</code> with its name."
        )
    if any("agent.hostname=" in c.text for c in rep.commands):
        notes.append(
            "<code>agent.hostname</code> is the model server the runs used; "
            "point it at yours."
        )
    if rep.cluster == "slurm":
        notes.append(
            "They ran on SLURM: add <code>mode=slurm_cluster</code>, a "
            "<code>logs_dir</code> and your <code>slurm_sweep_launcher</code> "
            "settings to launch there."
        )
    blocks = []
    for i, command in enumerate(rep.commands[:_SHOW_COMMANDS]):
        label = (
            f"{plural(command.n_runs, 'run')}: "
            f"{_grid(len(command.tasks), len(command.variations))}"
        )
        if rep.n_configs > 1:
            label = f"Configuration {command.config + 1} · {label}"
        cid = f"{rid}-cmd{i}"
        blocks.append(
            f'<p class="cmd-label">{_e(label)}</p>' if len(rep.commands) > 1 else ""
        )
        blocks.append(
            f'<div class="cmd"><pre class="code"><code id="{cid}">{_e(command.text)}'
            f'</code></pre><button type="button" class="copy" data-copy="{cid}" '
            f'aria-label="Copy the command">Copy</button></div>'
        )
    more = len(rep.commands) - _SHOW_COMMANDS
    if more > 0:
        blocks.append(f'<p class="note">… and {more} more</p>')
    return (
        '<section class="reproduce"><h3>Reproduce</h3>'
        + "".join(f'<p class="note">{n}</p>' for n in notes)
        + "".join(blocks)
        + "</section>"
    )


def _value(text: str) -> str:
    if text.startswith("varies: "):
        return f'<span class="varies">{_e(text)}</span>'
    return _e(text)


def _configuration(row: ModelRow) -> str:
    """The Configuration section: how the row's runs were set up, read from
    their config.yaml; a value that differs across runs reads ``varies``."""
    sections = reproduce.configuration(row.runs)
    if not sections:
        body = '<p class="none">Not recorded in the runs&#x27; config.yaml.</p>'
    else:
        body = '<dl class="config">' + "".join(
            f"<dt>{_e(title)}</dt><dd>"
            + '<span class="sep"> · </span>'.join(
                f'<span class="kv"><span class="k">{_e(label)}</span> {_value(value)}</span>'
                for label, value in items
            )
            + "</dd>"
            for title, items in sections
        ) + "</dl>"
    return f'<section class="configuration"><h3>Configuration</h3>{body}</section>'


def _outcome(run: Run) -> str:
    if run.errored:
        return '<span class="outcome errored">errored</span>'
    if run.success:
        return '<span class="outcome passed">passed</span>'
    return '<span class="outcome failed">failed</span>'


def _job_dir(run: Run) -> str:
    """The run's job directory under its sweep (``run-x/12``) without
    BrowserGym's long experiment dir; the full path is the cell's tooltip."""
    rel = os.path.relpath(os.path.dirname(run.exp_dir), run.source) if run.source else ".."
    return run.exp_dir if rel.startswith("..") or rel == "." else rel


def _episodes(row: ModelRow) -> str:
    """Every run the row counts (and its errored ones), by task then variation."""
    runs = sorted(row.runs, key=lambda r: (r.task, r.variation, r.seed))
    body = "".join(
        f"<tr>{_name_cell(r.task)}{_variation_cell(r.variation)}"
        f"<td>{_outcome(r)}</td>"
        f'<td class="num">{"" if r.n_steps is None else r.n_steps}</td>'
        f'<td class="num">{_e(r.seed)}</td>'
        f'<td class="path" title="{_e(r.exp_dir)}"><code>{_e(_job_dir(r))}</code></td></tr>'
        for r in runs
    )
    table = (
        '<table class="sub episodes"><thead><tr><th>Task</th><th>Variation</th>'
        '<th>Outcome</th><th class="num">Steps</th><th class="num">Seed</th>'
        f"<th>Job <small>(under Runs from)</small></th></tr></thead><tbody>{body}</tbody></table>"
    )
    return _fold("Episodes", str(len(runs)), table)


def _stats(row: ModelRow) -> str:
    return (
        '<div class="sheet"><table class="board stats"><thead><tr>'
        '<th>Success <small>(95% CI)</small></th><th class="num">Episodes</th>'
        '<th class="num">Tasks</th>'
        '<th class="num" title="Infrastructure errors, excluded from the rate">Errored</th>'
        '<th class="num">Mean steps</th></tr></thead><tbody><tr class="main">'
        f"<td>{_rate_cell(row)}</td>"
        f'<td class="num">{row.episodes}</td>'
        f'<td class="num">{row.tasks}</td>'
        f'<td class="num{" warn" if row.errored else ""}">{row.errored}</td>'
        f'<td class="num">{_steps(row.mean_steps)}</td>'
        "</tr></tbody></table></div>"
    )


def _model_page(row: ModelRow, slug: str, rid: str = "m") -> str:
    """A model's details page: its numbers, how its runs were configured and
    how to re-run them, success per task and per variation, and every episode."""
    facts = [
        f"rank {row.rank}",
        f"{plural(row.episodes, 'episode')} of {plural(row.tasks, 'task')}",
        plural(len(row.by_variation), "variation"),
    ]
    if row.errored:
        facts.append(f"{row.errored} errored (not counted)")
    if row.replaced:
        facts.append(f"{plural(row.replaced, 're-run')} replaced")
    parts = [
        '<nav class="back"><a href="#">← All models</a></nav>',
        '<header><p class="eyebrow">OpenApps · local preview · model</p>'
        f'<h1>{_e(row.label)}</h1><p class="meta">{" · ".join(map(_e, facts))}</p></header>',
        _stats(row),
        '<div class="sheet page-body">',
        _configuration(row),
        _reproduce(row, rid),
        _breakdown("By task", row.by_task, open_=True),
        _breakdown("By variation", row.by_variation, open_=True),
        _episodes(row),
    ]
    if row.errors:
        items = "".join(
            f"<li><b>{_e(task)}</b> {_e(msg) or 'error'}<br><code>{_e(where)}</code></li>"
            for task, where, msg in row.errors[:_SHOW_ERRORS]
        )
        more = len(row.errors) - _SHOW_ERRORS
        if more > 0:
            items += f"<li>… and {more} more</li>"
        errors = f'<ul class="errors">{items}</ul>'
        parts.append(_fold("Errored runs", str(len(row.errors)), errors, warn=True))
    sources = "".join(f"<li><code>{_e(s)}</code></li>" for s in row.sources[:_SHOW_SOURCES])
    more = len(row.sources) - _SHOW_SOURCES
    if more > 0:
        sources += f"<li>… and {more} more</li>"
    parts.append(_fold("Runs from", str(len(row.sources)), f'<ul class="sources">{sources}</ul>'))
    parts.append("</div>")
    return (
        f'<article class="model-page" id="page-{rid}" data-model="{_e(slug)}" '
        f'data-title="{_e(row.label)}" hidden>' + "".join(parts) + "</article>"
    )


def model_slugs(rows: Sequence[ModelRow]) -> list[str]:
    """One URL-safe name per row (``#model/<name>``), unique on the page."""
    slugs: list[str] = []
    for row in rows:
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", row.label).strip("-") or f"m{row.rank}"
        slug, n = base, 2
        while slug in slugs:
            slug, n = f"{base}-{n}", n + 1
        slugs.append(slug)
    return slugs


def _table(results: Results, slugs: Sequence[str]) -> str:
    bodies = []
    for row, slug in zip(results.rows, slugs):
        bodies.append(
            '<tbody class="model">'
            '<tr class="main">'
            f'<td class="rank">{row.rank}</td>'
            f'<td class="name"><a class="model-link" href="#model/{_e(slug)}">'
            f"{_e(row.label)}</a></td>"
            f"<td>{_rate_cell(row)}</td>"
            f'<td class="num">{row.episodes}</td>'
            f'<td class="num">{row.tasks}</td>'
            f'<td class="num{" warn" if row.errored else ""}">{row.errored}</td>'
            f'<td class="num">{_steps(row.mean_steps)}</td>'
            "</tr>"
            "</tbody>"
        )
    return (
        '<div class="sheet"><table class="board">'
        "<thead><tr>"
        '<th class="rank">#</th><th>Model</th><th>Success <small>(95% CI)</small></th>'
        '<th class="num">Episodes</th><th class="num">Tasks</th>'
        '<th class="num" title="Infrastructure errors, excluded from the rate">Errored</th>'
        '<th class="num">Mean steps</th>'
        "</tr></thead>" + "".join(bodies) + "</table></div>"
    )


_CSS = """
:root {
  color-scheme: light dark;
  --paper: #f6f6f4; --sheet: rgba(255,255,255,.86); --ink: #16181b; --ink-2: #3a3e44;
  --muted: #666b73; --line: rgba(22,24,27,.14); --line-strong: rgba(22,24,27,.32);
  --track: rgba(22,24,27,.08); --fill: #3b4048; --whisker: #16181b; --warn: #9a4a12;
  --hover: rgba(22,24,27,.035); --detail: rgba(244,245,246,.92);
  --code: rgba(22,24,27,.045); --button: #ffffff;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", ui-sans-serif, system-ui, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #0f1012; --sheet: rgba(20,21,24,.86); --ink: #eceef1; --ink-2: #c9cdd3;
    --muted: #979da6; --line: rgba(236,238,241,.12); --line-strong: rgba(236,238,241,.3);
    --track: rgba(236,238,241,.1); --fill: #aeb4bd; --whisker: #eceef1; --warn: #e3a36f;
    --hover: rgba(236,238,241,.04); --detail: rgba(26,28,31,.94);
    --code: rgba(236,238,241,.055); --button: #23252a;
  }
}
html { min-height: 100%; background: var(--paper) var(--tile) repeat; image-rendering: pixelated; }
body {
  margin: 0; padding: 1.25rem clamp(1rem, 4vw, 3rem) 3rem; color: var(--ink);
  font: 14px/1.45 var(--sans); image-rendering: auto;
}
main { max-width: 1180px; margin: 0 auto; }
.save { display: flex; justify-content: flex-end; gap: 1.25rem; font-size: 13px; margin-bottom: .5rem; }
.save a { color: var(--muted); text-decoration: none; border-bottom: 1px dotted currentColor; }
.save a:hover, .save a:focus-visible { color: var(--ink); }
header { margin: .5rem 0 1.25rem; }
.eyebrow { margin: 0; font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }
h1 { margin: .2rem 0 .35rem; font-size: 26px; font-weight: 650; letter-spacing: -.01em; }
.meta { margin: .1rem 0; color: var(--ink-2); }
code { font: 12px/1.4 var(--mono); overflow-wrap: anywhere; }
.sheet {
  background: var(--sheet); border: 1px solid var(--line); border-radius: 10px;
  -webkit-backdrop-filter: blur(2px); backdrop-filter: blur(2px); overflow-x: auto;
}
table { border-collapse: collapse; width: 100%; }
th { text-align: left; font-weight: 600; font-size: 12px; color: var(--muted); white-space: nowrap; }
th small { font-weight: 400; }
.board > thead th { padding: .7rem .8rem; border-bottom: 1px solid var(--line-strong); }
.board .main td { padding: .65rem .8rem; border-bottom: 1px solid var(--line); vertical-align: middle; }
.board .main:hover td { background: var(--hover); }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.rank { width: 2.5rem; color: var(--muted); font-variant-numeric: tabular-nums; }
.warn { color: var(--warn); font-weight: 600; }
.model-link { font-weight: 600; color: var(--ink); text-decoration: none; overflow-wrap: break-word; }
.model-link::after { content: " →"; color: var(--muted); }
.model-link:hover, .model-link:focus-visible { text-decoration: underline; text-underline-offset: 3px; }
.board .main td.name { min-width: 12rem; }
.back { margin-bottom: .25rem; font-size: 13px; }
.back a { color: var(--muted); text-decoration: none; }
.back a:hover, .back a:focus-visible { color: var(--ink); }
.page-body { margin-top: 1rem; padding: 1rem 1.25rem 1.25rem; }
.stats .main td { border-bottom: 0; }
.episodes th:nth-child(1) { width: 32%; } .episodes th:nth-child(2) { width: auto; }
.episodes th:nth-child(3) { width: 5.5rem; } .episodes th:nth-child(4) { width: 4rem; }
.episodes th:nth-child(5) { width: 5rem; } .episodes th:nth-child(6) { width: 10rem; }
.episodes td.path code { font-size: 11px; color: var(--muted); }
.outcome.passed { color: var(--ink); font-weight: 600; }
.outcome.failed { color: var(--muted); }
.outcome.errored { color: var(--warn); font-weight: 600; }
.rate { display: inline-grid; grid-template-columns: 4.2em 11rem 4.5em; align-items: center; gap: .6rem; }
.rate.compact { grid-template-columns: 3.8em 6rem 4em; gap: .45rem; }
.pct { font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
.range { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.bar { position: relative; height: 8px; border-radius: 4px; background: var(--track); }
.fill { position: absolute; inset: 0 auto 0 0; border-radius: 4px; background: var(--fill); opacity: .55; }
.ci { position: absolute; top: 50%; height: 2px; margin-top: -1px; background: var(--whisker); }
.ci::before, .ci::after { content: ""; position: absolute; top: -4px; width: 1.5px; height: 10px; background: var(--whisker); }
.ci::before { left: 0; } .ci::after { right: 0; }
.none { color: var(--muted); font-style: italic; }
h3 { margin: .2rem 0 .4rem; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); }
.fold > summary { display: flex; align-items: baseline; gap: .5rem; cursor: pointer; list-style: none; }
.fold > summary::-webkit-details-marker { display: none; }
.fold > summary::before { content: "▸"; width: .8em; color: var(--muted); font-size: 11px; transition: transform .12s; }
.fold[open] > summary::before { transform: rotate(90deg); }
.fold > summary h3 { margin: .2rem 0; }
.fold > summary:hover h3 { color: var(--ink); }
.fold > summary:focus-visible { outline: 2px solid var(--line-strong); outline-offset: 2px; border-radius: 3px; }
.fold[open] > summary { margin-bottom: .3rem; }
.count { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.sub { table-layout: fixed; }
.sub th:nth-child(2) { width: 14rem; } .sub th:nth-child(3) { width: 4.5rem; }
.sub th:nth-child(4) { width: 4.5rem; } .sub th:nth-child(5) { width: 4rem; }
.sub th, .sub td { padding: .28rem .45rem; border-bottom: 1px solid var(--line); }
.sub td.name { overflow-wrap: break-word; }
section + section { margin-top: 1rem; }
ul.errors, ul.sources { margin: 0; padding-left: 1.1rem; color: var(--ink-2); }
ul.errors li { margin-bottom: .35rem; }
.note { margin: 0 0 .45rem; color: var(--ink-2); max-width: 90ch; }
.cmd-label { margin: .6rem 0 .25rem; font-size: 12.5px; color: var(--muted); }
.cmd { position: relative; max-width: 900px; }
pre.code {
  margin: 0; padding: .6rem 4.6rem .6rem .8rem; max-height: 16rem; overflow: auto;
  background: var(--code); border: 1px solid var(--line); border-radius: 6px;
}
pre.code code { white-space: pre; overflow-wrap: normal; color: var(--ink); }
.copy {
  position: absolute; top: .4rem; right: .4rem; padding: .15rem .6rem; cursor: pointer;
  font: 600 12px/1.5 var(--sans); color: var(--ink-2); background: var(--button);
  border: 1px solid var(--line-strong); border-radius: 5px;
}
.copy:hover, .copy:focus-visible { color: var(--ink); border-color: var(--ink-2); }
.copy.done { color: var(--ink); }
dl.config { display: grid; grid-template-columns: 6rem minmax(0, 1fr); gap: .35rem 1rem; margin: 0; max-width: 900px; }
dl.config dt { font-weight: 600; color: var(--ink-2); }
dl.config dd { margin: 0; color: var(--ink); }
.kv { white-space: nowrap; } .kv .k { color: var(--muted); }
.sep { color: var(--line-strong); }
.varies { color: var(--warn); white-space: normal; }
.empty { padding: 2rem; color: var(--muted); }
footer { margin-top: 1.25rem; color: var(--muted); font-size: 12.5px; max-width: 80ch; }
footer p { margin: .3rem 0; }
@media print {
  .save, .copy, .back { display: none; } .sheet { backdrop-filter: none; }
  pre.code { max-height: none; padding-right: .8rem; }
}
"""

_JS = """
// #model/<name> shows that model's page in place of the table; anything else
// shows the table, back where it was scrolled to.
const board = document.getElementById("board"), baseTitle = document.title;
let boardScroll = 0;
function route() {
  const match = location.hash.match(/^#model\\/(.+)$/);
  const name = match ? decodeURIComponent(match[1]) : null;
  let shown = null;
  for (const page of document.querySelectorAll(".model-page")) {
    page.hidden = page.dataset.model !== name;
    if (!page.hidden) shown = page;
  }
  if (shown && !board.hidden) boardScroll = window.scrollY;
  const wasBoard = !board.hidden;
  board.hidden = Boolean(shown);
  document.title = shown ? `${shown.dataset.title} · ${baseTitle}` : baseTitle;
  window.scrollTo(0, shown ? 0 : (wasBoard ? window.scrollY : boardScroll));
}
window.addEventListener("hashchange", route);
route();
// Copy a command. The clipboard API needs a secure context (https, or
// localhost); elsewhere -- a saved file, a forwarded host -- select the text
// and fall back to the legacy copy command, leaving it selected if that fails.
for (const button of document.querySelectorAll(".copy")) {
  button.addEventListener("click", async () => {
    const code = document.getElementById(button.dataset.copy);
    let copied = false;
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(code.textContent); copied = true; } catch (e) {}
    }
    if (!copied) {
      const range = document.createRange();
      range.selectNodeContents(code);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      try { copied = document.execCommand("copy"); } catch (e) {}
    }
    button.textContent = copied ? "Copied" : "Selected";
    button.classList.add("done");
    clearTimeout(button.timer);
    button.timer = setTimeout(() => {
      button.textContent = "Copy";
      button.classList.remove("done");
    }, 1500);
  });
}
"""

_TOOLBAR = """<nav class="save" aria-label="Save this page">
  <a href="/results.html" download="{name}.html">Save as HTML</a>
  <a href="/results.pdf" data-pdf>Save as PDF</a>
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
      window.print();  // no Chromium on the server: the browser's own "Save as PDF"
    }} finally {{
      link.textContent = label;
    }}
  }});
</script>
"""


def _data_url(mime: str, payload: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode()}"


class Page:
    """The page for one :class:`Results`, with one halftone texture drawn at
    construction, so the served page, its saved HTML and its PDF match.
    ``background_seed=None`` draws a new texture each run."""

    def __init__(self, results: Results, background_seed: Optional[int] = None):
        self.results = results
        self._tiles = {
            scheme: _data_url("image/png", tile_png(scheme, background_seed))
            for scheme in ("light", "dark")
        }
        self._pdf: Optional[bytes] = None
        self.name = "openapps-results-" + results.generated[:10]

    def html(self, toolbar: bool = False) -> str:
        r = self.results
        n_tasks = len({run.task for run in r.runs if not run.errored})
        meta = (
            f"{plural(len(r.rows), 'model')} · {plural(r.n_episodes, 'episode')} · "
            f"{plural(n_tasks, 'task')}"
        )
        if r.n_errored:
            meta += f" · {r.n_errored} errored"
        meta += f" · generated {r.generated}"
        where = ", ".join(f"<code>{_e(p)}</code>" for p in r.paths)
        notes = [
            "Success is BrowserGym's <code>cum_reward ≥ 1.0</code> (from each run's "
            "<code>summary_info.json</code>), over the runs that did not error; the "
            "interval is a 95% Wilson score interval.",
            "<b>Errored</b> runs hit an infrastructure error (<code>err_msg</code> set: "
            "the web app or model server failed), not an agent failure. They are left "
            "out of the rate and counted separately.",
            "Re-runs of one cell (agent, task, app variation, seed) count once: an "
            "errored run loses to one that did not error, otherwise the later run wins.",
            "This is not a benchmark result: models are ranked side by side whatever "
            "tasks they ran, so compare <b>Episodes</b> and <b>Tasks</b> too.",
        ]
        extra = []
        if r.duplicates:
            extra.append(f"{plural(r.duplicates, 're-run')} replaced")
        if r.unfinished:
            extra.append(f"{len(r.unfinished)} unfinished (no summary_info.json yet)")
        if r.skipped:
            extra.append(f"{len(r.skipped)} skipped (unreadable or no config.yaml)")
        if extra:
            notes.append(" · ".join(map(_e, extra)) + ".")
        slugs = model_slugs(r.rows)
        body = _table(r, slugs) if r.rows else '<div class="sheet empty">No runs found.</div>'
        pages = "".join(
            _model_page(row, slug, f"m{row.rank}") for row, slug in zip(r.rows, slugs)
        )
        return (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{_e(TITLE)}</title>\n<style>{_CSS}\n"
            f':root {{ --tile: url("{self._tiles["light"]}"); }}\n'
            "@media (prefers-color-scheme: dark) { :root { "
            f'--tile: url("{self._tiles["dark"]}"); }} }}\n'
            "</style>\n</head>\n<body>\n<main>\n"
            + (_TOOLBAR.format(name=self.name) if toolbar else "")
            + '<div id="board"><header><p class="eyebrow">OpenApps · local preview</p>'
            "<h1>Task success by model</h1>"
            f'<p class="meta">{meta}</p><p class="meta">Runs under {where}</p></header>\n'
            + body
            + "\n<footer>"
            + "".join(f"<p>{n}</p>" for n in notes)
            + "</footer></div>\n"
            + pages
            + "\n</main>\n"
            f"<script>{_JS}</script>\n</body>\n</html>\n"
        )

    def pdf(self) -> bytes:
        """One tall PDF page as wide as the screen layout, rendered by the
        Playwright Chromium BrowserGym already installs."""
        if self._pdf is None:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                try:
                    page = browser.new_page(
                        viewport={"width": 1280, "height": 900}, color_scheme="light"
                    )
                    page.set_content(self.html(), wait_until="load")
                    height = page.evaluate("document.documentElement.scrollHeight")
                    self._pdf = page.pdf(
                        width="1280px",
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
            out.write_text(self.html(), encoding="utf-8")
        return out


# --- serving -------------------------------------------------------------------


def has_display() -> bool:
    return sys.platform in ("darwin", "win32") or bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def ssh_host() -> str:
    """The name a laptop reaches this machine by. A login node's own hostname
    is often an internal pod or node name, so ``$OPENAPPS_SSH_HOST`` (your ssh
    config alias for the cluster) wins when set."""
    return os.environ.get("OPENAPPS_SSH_HOST") or socket.gethostname()


def how_to_view(port: int) -> str:
    """What to run on a laptop to see a page served on this (remote) machine."""
    host = ssh_host()
    lines = [
        "No display here. From your laptop, forward the port and open the page:",
        f"  ssh -N -L {port}:localhost:{port} {host}",
        f"  open http://localhost:{port}/",
    ]
    if not os.environ.get("OPENAPPS_SSH_HOST"):
        lines.append(
            f"  ({host} is this machine's own name; set OPENAPPS_SSH_HOST to your "
            "ssh alias for it)"
        )
    return "\n".join(lines)


def make_server(
    page: Page, host: str = "127.0.0.1", port: int = DEFAULT_PORT
) -> ThreadingHTTPServer:
    """An HTTP server for ``page`` on ``port``, or the next free one."""
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, mime: str, filename: str = "") -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 -- http.server's naming
            route = self.path.split("?")[0]
            if route in ("/", "/index.html"):
                self._send(200, page.html(toolbar=True).encode(), "text/html; charset=utf-8")
            elif route == "/results.html":
                self._send(
                    200, page.html().encode(), "text/html; charset=utf-8", f"{page.name}.html"
                )
            elif route == "/results.pdf":
                try:
                    with lock:
                        body = page.pdf()
                except Exception as exc:  # noqa: BLE001 -- the page falls back to print
                    print(
                        f"PDF render failed ({type(exc).__name__}: {exc}); "
                        "the page falls back to the print dialog",
                        file=sys.stderr,
                    )
                    self._send(503, b"PDF rendering unavailable", "text/plain")
                    return
                self._send(200, body, "application/pdf", f"{page.name}.pdf")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args) -> None:  # quiet
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
    print(f"Results at {url}  (Ctrl-C to stop)")
    if not (open_browser and has_display() and webbrowser.open(url)):
        print(how_to_view(server.server_port))
    sys.stdout.flush()  # before blocking: under nohup or a pipe it is buffered
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
