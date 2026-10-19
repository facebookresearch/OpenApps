"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The OpenApps-Align leaderboard: frozen suites, submission bundles, and the
checks CI runs on them.

    uv run launch_parallel_agents.py benchmark=align_v1 agent=<name>
    uv run -m open_apps.leaderboard.pack <logs_dir> --org <org> --agent <agent>
    uv run -m open_apps.leaderboard.import_wandb <entity>/<project> --group <g> --org <org> --agent <agent>
    uv run -m open_apps.leaderboard.validate leaderboard/submissions/<bundle>
    uv run -m open_apps.leaderboard.build
    uv run -m open_apps.leaderboard.verify plan|record ...      # maintainers

Modules:
  suite     frozen suites (config/benchmark/*.yaml): cells, seeds, the sweep
  bundle    bundle layout, hashing, (de)serialization, mini JSON Schema check
  pack      logs_dir -> bundle (and write_bundle, shared by every ingestion path)
  import_wandb  W&B episode_record artifacts -> bundle, through pack's code
  reference the repo's config for a suite cell (what a record must embed)
  validate  schema + provenance + re-score + coverage
  build     all bundles -> docs/assets/leaderboard/leaderboard.json (generated)
  verify    held-out re-run -> verified: {date, seeds_hash, aligned, delta} in meta.yaml

Scores are never trusted from a submission: ``open_apps.alignment.score`` is
the single authority, re-run on every record -- under the repo's task, event
and irreversibility config for the record's suite cell, never the record's
own copies.
"""
