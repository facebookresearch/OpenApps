"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The OpenApps-Align leaderboard: frozen suites, submission bundles, and the
checks CI runs on them.

    uv run launch_parallel_agents.py benchmark=align_v1 agent=<name>
    uv run -m open_apps.leaderboard.pack <logs_dir> --org <org> --agent <agent>
    uv run -m open_apps.leaderboard.validate leaderboard/submissions/<bundle>
    uv run -m open_apps.leaderboard.build

Modules:
  suite     frozen suites (config/benchmark/*.yaml): cells, seeds, the sweep
  bundle    bundle layout, hashing, (de)serialization, mini JSON Schema check
  pack      logs_dir -> bundle
  validate  schema + coverage + re-score
  build     all bundles -> leaderboard/leaderboard.json

Scores are never trusted from a submission: ``open_apps.alignment.score`` is
the single authority, re-run on every record.
"""
