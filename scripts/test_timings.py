#!/usr/bin/env python3
"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Where the test suite's time goes, per app, file and test.

Reads the JUnit XML pytest writes, so it costs nothing beyond the run you were
doing anyway:

    uv run -m pytest tests/ --junitxml=/tmp/junit.xml -o junit_family=xunit1
    uv run scripts/test_timings.py /tmp/junit.xml
    uv run scripts/test_timings.py /tmp/before.xml /tmp/after.xml   # compare

The times are what pytest records per test (setup + call + teardown), summed.
That is serial time: under ``-n`` the wall clock is lower, and collection is
not in it at all.

Apps are matched from the test file's name (``APPS`` below, first match wins),
which is a heuristic: a new file whose name names no app lands in "Other",
and that row is the hint to add a pattern.
"""

import argparse
import collections
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

APPS = [
    ("Composer", r"compose"),
    ("Long-horizon tasks", r"longer_horizon"),
    ("Files", r"file|pdf|receipt|medical"),
    ("Todo", r"todo|kanban"),
    ("Calendar", r"calendar"),
    ("OpenBanking", r"bank|card|payment|send_money|statement"),
    ("Messenger", r"messag|messenger|handles"),
    ("Contacts", r"contact"),
    ("Passwords", r"password"),
    ("Online shop", r"shop|webshop|catalog"),
    ("Maps", r"map"),
    ("Code editor", r"codeeditor|code_editor"),
    (
        "Start page / shell / chrome",
        r"start_page|shell|chrome|desktop|home_screen|dock|wallpaper|phone|device|window",
    ),
    (
        "Themes / i18n / UI library",
        r"theme|i18n|language|ui_?library|font|pop_up|icon|brand",
    ),
    (
        "Agents / parsers / prompts",
        r"agent|parser|uitars|prompt|vllm|report_action|abstention",
    ),
    (
        "Launcher / sweeps / infra",
        r"launch|parallel|port|sweep|wandb|egress|state|mcp|config|preview|leaderboard"
        r"|screenshot|goto_guard|static_route|availability|^test_apps$|questions",
    ),
]


def app_of(stem: str) -> str:
    for name, pattern in APPS:
        if re.search(pattern, stem):
            return name
    return "Other"


@dataclass
class Run:
    files: dict = field(
        default_factory=lambda: collections.defaultdict(lambda: [0, 0.0])
    )
    tests: list = field(default_factory=list)  # (seconds, "file::name")
    failed: list = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(t for _, t in self.files.values())

    def per_app(self) -> dict:
        apps = collections.defaultdict(lambda: [0, 0.0])
        for stem, (n, t) in self.files.items():
            row = apps[app_of(stem)]
            row[0] += n
            row[1] += t
        return apps


def load(path: str) -> Run:
    run = Run()
    for case in ET.parse(path).getroot().iter("testcase"):
        parts = case.get("classname", "").split(".")
        # The file is the first ``test_*`` part; a class inside it comes after.
        stem = next((p for p in parts if p.startswith("test_")), parts[-1] or "?")
        seconds = float(case.get("time") or 0)
        run.files[stem][0] += 1
        run.files[stem][1] += seconds
        name = f"{stem}::{case.get('name')}"
        run.tests.append((seconds, name))
        if case.find("failure") is not None or case.find("error") is not None:
            run.failed.append(name)
    run.tests.sort(reverse=True)
    return run


def report(run: Run, top: int) -> None:
    n_total = sum(n for n, _ in run.files.values())
    total = run.seconds or 1.0
    print(f"{n_total} tests, {total / 60:.1f} min of test time (serial)\n")
    print(f"{'App':32} {'tests':>6} {'min':>7} {'%time':>6} {'s/test':>7}")
    for name, (n, t) in sorted(run.per_app().items(), key=lambda kv: -kv[1][1]):
        print(
            f"{name:32} {n:6d} {t / 60:7.1f} {100 * t / total:5.1f}% {t / max(n, 1):7.2f}"
        )

    print(f"\nTop {top} files by time:")
    for stem, (n, t) in sorted(run.files.items(), key=lambda kv: -kv[1][1])[:top]:
        print(f"  {stem:42} {n:5d} tests {t / 60:6.1f} min {t / max(n, 1):6.2f} s/test")

    print("\nWhere the time is:")
    for cut in (0.1, 0.5, 1, 2, 5, 10):
        sel = [t for t, _ in run.tests if t >= cut]
        print(
            f"  tests >= {cut:>4}s: {len(sel):5d} ({100 * len(sel) / max(n_total, 1):4.1f}%)"
            f"  {sum(sel) / 60:6.1f} min ({100 * sum(sel) / total:4.1f}% of time)"
        )

    print(f"\nSlowest {top} tests:")
    for t, name in run.tests[:top]:
        print(f"  {t:7.1f}s  {name}")
    if run.failed:
        print(f"\n{len(run.failed)} failed or errored:")
        for name in run.failed:
            print(f"  {name}")


def compare(before: Run, after: Run) -> None:
    b_apps, a_apps = before.per_app(), after.per_app()
    print(f"{'App':32} {'before':>8} {'after':>8} {'change':>8}   (minutes)")
    for name in sorted(
        set(b_apps) | set(a_apps), key=lambda k: -b_apps.get(k, [0, 0])[1]
    ):
        b, a = b_apps.get(name, [0, 0.0])[1], a_apps.get(name, [0, 0.0])[1]
        change = f"{100 * (a - b) / b:+.0f}%" if b else "new"
        print(f"{name:32} {b / 60:8.1f} {a / 60:8.1f} {change:>8}")
    b, a = before.seconds, after.seconds
    print(
        f"{'TOTAL':32} {b / 60:8.1f} {a / 60:8.1f} {100 * (a - b) / max(b, 1e-9):+7.0f}%"
    )
    gone = sorted(set(n for _, n in before.tests) - set(n for _, n in after.tests))
    new = sorted(set(n for _, n in after.tests) - set(n for _, n in before.tests))
    print(f"\n{len(gone)} tests only in before, {len(new)} only in after")
    if after.failed:
        print(f"{len(after.failed)} failed or errored in after:")
        for name in after.failed:
            print(f"  {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument(
        "junit", nargs="+", help="one JUnit XML to report, or two to compare"
    )
    parser.add_argument("--top", type=int, default=25, help="files and tests to list")
    args = parser.parse_args()
    if len(args.junit) > 2:
        parser.error("give one file to report or two to compare")
    runs = [load(p) for p in args.junit]
    if len(runs) == 1:
        report(runs[0], args.top)
    else:
        compare(*runs)


if __name__ == "__main__":
    main()
