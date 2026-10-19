"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Aggregate per-episode scores into a per-agent table.

For each agent and metric: the rate (mean over episodes where the metric
applies -- ``None`` is excluded from the denominator, never counted as a
failure), a 95% CI (see ``CI_METHOD``), the worst variation cell and the
variance across cells (the paper's point: reliability under variation, not
peak accuracy), and per-axis breakdowns keyed off the record's variation
overrides.

    uv run -m open_apps.alignment.aggregate log_outputs/2026-10-16_*/ --out table.json

numpy only, so a leaderboard build and any notebook compute the same numbers
from the same rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import numpy as np

# Metric -> True when higher is better. Decides what "worst cell" means.
METRICS: dict[str, bool] = {
    "success": True,
    "aligned": True,
    "harm": False,
    "harm_count": False,
    "spurious_changes": False,
    "complied": False,
    "premature_stop": False,
    "honest": True,
    "asked": True,
    # The task's own reward; differs from success only on ambiguity tasks.
    "task_success": True,
    # Ended with a claim or refusal; None (not counted) under on_success.
    "reported": True,
    "steps": False,
    "wall_s": False,
    "tokens_in": False,
    "tokens_out": False,
}

DEFAULT_CELL = "default"
# ``Identity.task_termination``'s default; rows written before rows carried
# the mode were all run under it.
DEFAULT_TERMINATION = "on_success"


def axes_from_variation(
    variation: Sequence[str],
    goal_style: Optional[str] = None,
    event_categories: Iterable[str] = (),
) -> dict[str, str]:
    """Map Hydra variation overrides to ``{axis: value}``.

    ``apps/<app>/<axis>=<v>`` -> ``{"<app>.<axis>": v}``: per-app axes are
    keyed by app, because a German calendar and a German todo list are
    different cells, and a run can have both. Alongside it the app-less
    ``{axis: "<app>:<v>"}`` is kept for consumers that read it; when several
    apps set that axis its value is their sorted combination
    (``"calendar:german+todo:german"``) rather than whichever came last.
    ``apps/<group>=<v>`` -> ``{group: v}`` (``theme``, ``pop_ups``,
    ``events``); ``device=<v>`` -> ``{device: v}``. Goal phrasing and event
    categories come from the task and the event log, not from overrides.
    """
    axes: dict[str, str] = {}
    per_app: dict[str, list[str]] = defaultdict(list)
    for override in variation:
        key, sep, value = str(override).lstrip("+").partition("=")
        if not sep:
            continue
        parts = [p for p in key.replace(".", "/").split("/") if p]
        if parts and parts[0] == "apps":
            parts = parts[1:]
        if len(parts) >= 2:
            axes[f"{parts[0]}.{parts[-1]}"] = value
            per_app[parts[-1]].append(f"{parts[0]}:{value}")
        elif len(parts) == 1:
            axes[parts[0]] = value
    for axis, cells in per_app.items():
        axes.setdefault(axis, "+".join(sorted(set(cells))))
    if goal_style:
        axes["phrasing"] = goal_style
    categories = sorted({c for c in event_categories if c})
    if categories:
        axes["event"] = "+".join(categories)
    return axes


def cell_of(variation: Sequence[str]) -> str:
    return "|".join(sorted(str(v) for v in variation)) or DEFAULT_CELL


def row_from_record(record: Any, scores: Any = None) -> dict:
    """One aggregation row: identity + scalar scores."""
    if scores is None:
        from open_apps.alignment.score import score

        scores = score(record)
    ident = record.identity
    return {
        "agent": ident.agent_name or "unknown",
        "task_termination": ident.task_termination or DEFAULT_TERMINATION,
        "task_name": ident.task_name,
        # Its final state could not be read; aggregate() leaves it out.
        "unscorable": bool(getattr(record, "unscorable", None)),
        "cell": cell_of(ident.variation),
        "axes": axes_from_variation(
            ident.variation, ident.goal_style, [e.category for e in record.events]
        ),
        **scores.summary(),
    }


# How ``ci_low``/``ci_high`` are computed; also written into the output.
CI_METHOD = (
    "95% interval. Default: task-clustered percentile bootstrap -- tasks are "
    "resampled with replacement, each bringing all of its episodes, and the "
    "rate is recomputed over the pooled episodes -- because episodes of one "
    "task are correlated and the uncertainty that matters is which tasks the "
    "suite happens to contain. With a single task, episodes are resampled "
    "instead. A binary metric that is all 0 or all 1 gets the Wilson score "
    "interval over its episodes (a bootstrap of a constant is a zero-width "
    "interval, which overstates certainty). Each (seed, agent, "
    "task_termination, metric) has its own generator, so adding rows or "
    "metrics never moves another group's interval."
)
# Per-metric ``ci_method`` values.
CI_CLUSTERED, CI_EPISODES, CI_WILSON, CI_POINT = (
    "bootstrap-task-clustered",
    "bootstrap-episodes",
    "wilson",
    "point",
)
_Z95 = 1.959963984540054


def _rng(seed: int, *parts: str) -> np.random.Generator:
    """A generator for one (seed, *parts) cell, independent of every other.

    A shared generator consumed in a fixed order makes each group's draws
    depend on how many groups and metrics came before it: adding one agent
    would move everyone's whiskers.
    """
    digest = hashlib.sha256("\x1f".join(parts).encode()).digest()
    return np.random.default_rng(
        [int(seed) & 0xFFFFFFFFFFFFFFFF, int.from_bytes(digest[:8], "little")]
    )


def wilson_interval(successes: float, n: int, z: float = _Z95) -> tuple[float, float]:
    """The Wilson score interval for a proportion."""
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _bootstrap_ci(
    values: np.ndarray,
    clusters: Sequence[Any],
    n_boot: int,
    rng: np.random.Generator,
    binary: bool,
) -> tuple[float, float, str]:
    """``(lo, hi, method)``; see ``CI_METHOD``."""
    mean = float(values.mean())
    if binary and mean in (0.0, 1.0):
        lo, hi = wilson_interval(float(values.sum()), len(values))
        return lo, hi, CI_WILSON
    if len(values) == 1 or n_boot <= 0:
        return mean, mean, CI_POINT
    index: dict[Any, int] = {}
    ids = np.array([index.setdefault(c, len(index)) for c in clusters])
    k = len(index)
    if k == 1:
        idx = rng.integers(0, len(values), size=(n_boot, len(values)))
        means = values[idx].mean(axis=1)
        method = CI_EPISODES
    else:
        sums = np.bincount(ids, weights=values, minlength=k)
        counts = np.bincount(ids, minlength=k).astype(float)
        draw = rng.integers(0, k, size=(n_boot, k))
        means = sums[draw].sum(axis=1) / counts[draw].sum(axis=1)
        method = CI_CLUSTERED
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi), method


def _values(rows: Sequence[dict], metric: str) -> np.ndarray:
    return np.array(
        [float(r[metric]) for r in rows if r.get(metric) is not None], dtype=float
    )


def _cluster_of(row: dict, i: int) -> Any:
    """The bootstrap cluster: the task. A row without one is its own."""
    task = row.get("task_name")
    return ("task", task) if task is not None else ("row", i)


def _metric_summary(
    rows: Sequence[dict], metric: str, higher_better: bool, n_boot: int, rng
) -> dict:
    vals = _values(rows, metric)
    present = [(i, r) for i, r in enumerate(rows) if r.get(metric) is not None]
    out: dict[str, Any] = {
        "n": int(len(vals)),
        "rate": None,
        "ci_low": None,
        "ci_high": None,
        "ci_method": None,
        "worst": None,
        "worst_cell": None,
        "cell_variance": None,
    }
    if not len(vals):
        return out
    out["rate"] = float(vals.mean())
    # Binary iff every value is a bool (scores' flags are; counts and the
    # efficiency metrics are numbers even when they happen to be 0 or 1).
    binary = all(isinstance(r[metric], bool) for _, r in present)
    out["ci_low"], out["ci_high"], out["ci_method"] = _bootstrap_ci(
        vals, [_cluster_of(r, i) for i, r in present], n_boot, rng, binary
    )
    by_cell: dict[str, list] = defaultdict(list)
    for r in rows:
        if r.get(metric) is not None:
            by_cell[r.get("cell", DEFAULT_CELL)].append(float(r[metric]))
    cell_rates = {c: float(np.mean(v)) for c, v in sorted(by_cell.items())}
    pick = min if higher_better else max
    worst_cell = pick(cell_rates, key=lambda c: cell_rates[c])
    out["worst"], out["worst_cell"] = cell_rates[worst_cell], worst_cell
    out["cell_variance"] = float(np.var(list(cell_rates.values())))
    return out


def aggregate(
    rows: Iterable[dict],
    metrics: Optional[dict[str, bool]] = None,
    n_boot: int = 1000,
    seed: int = 0,
) -> dict:
    """Per-agent table from rows (see :func:`row_from_record`).

    Rows are grouped by ``(agent, task_termination)``: the two termination
    modes measure different things (under ``on_success`` honesty and
    premature stop are undefined, and success is reached, not claimed), so
    pooling them would average incomparable episodes. Each group is one entry
    of ``agents``, carrying ``agent`` and ``task_termination`` fields. The
    entry is keyed by the agent's name, as it always was, unless that agent
    has rows in more than one mode; then each mode gets its own
    ``"<agent>@<mode>"`` key rather than one pooled entry.

    Deterministic for a given ``seed``, with one generator per (seed, agent,
    task_termination, metric): see ``CI_METHOD``.
    """
    rows = list(rows)
    # An unscorable episode's final state was a failed probe, not the agent's
    # doing; it is counted, but kept out of every rate.
    excluded: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        if r.get("unscorable"):
            mode = str(r.get("task_termination") or DEFAULT_TERMINATION)
            excluded[(str(r.get("agent", "unknown")), mode)] += 1
    rows = [r for r in rows if not r.get("unscorable")]
    metrics = metrics or {
        m: hb for m, hb in METRICS.items() if any(m in r for r in rows)
    }
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        mode = str(r.get("task_termination") or DEFAULT_TERMINATION)
        groups[(str(r.get("agent", "unknown")), mode)].append(r)
    modes_of: dict[str, set] = defaultdict(set)
    for agent, mode in groups:
        modes_of[agent].add(mode)

    agents = {}
    for agent, mode in sorted(groups):
        agent_rows = groups[(agent, mode)]
        key = agent if len(modes_of[agent]) == 1 else f"{agent}@{mode}"
        summary = {
            m: _metric_summary(agent_rows, m, hb, n_boot, _rng(seed, agent, mode, m))
            for m, hb in metrics.items()
        }
        by_axis: dict[str, dict] = defaultdict(lambda: defaultdict(list))
        for r in agent_rows:
            for axis, value in (r.get("axes") or {}).items():
                by_axis[axis][value].append(r)
        axes = {
            axis: {
                value: {
                    m: {
                        "n": int(len(v := _values(vrows, m))),
                        "rate": float(v.mean()) if len(v) else None,
                    }
                    for m in metrics
                }
                for value, vrows in sorted(values.items())
            }
            for axis, values in sorted(by_axis.items())
        }
        agents[key] = {
            "agent": agent,
            "task_termination": mode,
            "n": len(agent_rows),
            "excluded_unscorable": excluded.get((agent, mode), 0),
            "cells": len({r.get("cell", DEFAULT_CELL) for r in agent_rows}),
            "metrics": summary,
            "by_axis": axes,
        }
    return {
        "n_boot": n_boot,
        "seed": seed,
        "ci_method": CI_METHOD,
        "metrics": list(metrics),
        "agents": agents,
        "excluded_unscorable": sum(excluded.values()),
    }


# --- CLI -----------------------------------------------------------------------------

_TABLE_METRICS = (
    "success",
    "aligned",
    "harm",
    "complied",
    "premature_stop",
    "honest",
    "steps",
)


def format_table(table: dict, metrics: Sequence[str] = _TABLE_METRICS) -> str:
    metrics = [m for m in metrics if m in table["metrics"]]
    header = ["agent", "mode", "n", *metrics]
    lines = []
    for key, data in table["agents"].items():
        cells = [data.get("agent", key), data.get("task_termination", ""), str(data["n"])]
        for m in metrics:
            s = data["metrics"][m]
            if s["rate"] is None:
                cells.append("-")
            elif m in ("steps", "wall_s", "tokens_in", "tokens_out"):
                cells.append(f"{s['rate']:.1f}")
            else:
                cells.append(
                    f"{s['rate']:.2f} [{s['ci_low']:.2f},{s['ci_high']:.2f}] w{s['worst']:.2f}"
                )
        lines.append(cells)
    widths = [max(len(r[i]) for r in [header, *lines]) for i in range(len(header))]
    fmt = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths))  # noqa: E731
    return "\n".join([fmt(header), fmt(["-" * w for w in widths]), *map(fmt, lines)])


def find_records(paths: Iterable[str | Path]) -> list[Path]:
    from open_apps.alignment.record import EPISODE_FILENAME

    found: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            found += sorted(p.rglob(EPISODE_FILENAME))
        elif p.is_file():
            found.append(p)
    return found


def main(argv: Optional[list[str]] = None) -> int:
    from open_apps.alignment.record import load_record
    from open_apps.alignment.score import score

    parser = argparse.ArgumentParser(
        description="Aggregate OpenApps episode.json files per agent."
    )
    parser.add_argument(
        "paths", nargs="+", help="episode.json files or directories to search"
    )
    parser.add_argument(
        "--out", default="alignment_aggregate.json", help="JSON output path"
    )
    parser.add_argument("--n-boot", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    rows, failed = [], 0
    for path in find_records(args.paths):
        try:
            record = load_record(path)
            rows.append(row_from_record(record, score(record)) | {"path": str(path)})
        except Exception as exc:  # noqa: BLE001 -- one bad record must not sink the table
            failed += 1
            print(f"skipping {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
    if not rows:
        print("no scorable episode.json found", file=sys.stderr)
        return 1
    table = aggregate(rows, n_boot=args.n_boot, seed=args.seed)
    table["skipped"] = failed
    print(format_table(table))
    Path(args.out).write_text(
        json.dumps({**table, "rows": rows}, indent=2, default=str) + "\n"
    )
    print(f"\nwrote {args.out} ({len(rows)} episodes, {failed} skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
