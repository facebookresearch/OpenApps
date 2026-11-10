"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

A local, ad-hoc results table over your own runs: one row per model, ranked
by task success.

    uv run -m open_apps.results_preview                       # everything under log_outputs/
    uv run -m open_apps.results_preview /checkpoint/$USER/logs/open_apps
    uv run -m open_apps.results_preview sweep_a sweep_a_rerun --agent 'Qwen*' --task 'todo_*'
    uv run -m open_apps.results_preview runs/ --out results.html   # or .pdf

It finds every run below the directories given, however nested
(:mod:`.runs` says what it reads), serves the page from memory and opens a
browser. The page has *Save as HTML* and *Save as PDF* links; without a
display it prints the ``ssh -N -L`` command that brings it to your laptop
(``OPENAPPS_SSH_HOST`` names the host). A local ``launch_parallel_agents.py``
sweep opens it when it finishes (``open_leaderboard=false`` turns that off).

It is a quick look, not a benchmark: nothing checks that models ran the same
tasks, so the Episodes and Tasks columns sit beside the rate.
"""

from open_apps.results_preview.cli import launch, main
from open_apps.results_preview.runs import Results, collect

__all__ = ["Results", "collect", "launch", "main"]
