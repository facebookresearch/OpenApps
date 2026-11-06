"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Alignment scoring for OpenApps episodes.

Every metric is a deterministic function of a persisted ``EpisodeRecord``
(``exp_dir/episode.json``), so scores can be recomputed offline on any bundle
of runs without the apps, a browser, or the model:

    record = load_record("exp_dir/episode.json")
    scores = score(record)                       # one episode
    table = aggregate([row_from_record(r) ...])  # per agent, with CIs

Modules:
  record        EpisodeRecord (record_version 1), JSON Schema, builder
  changes       keyed initial/target/final diffs over the cross-app state
  irreversible  per-app ``irreversible:`` maps (config/apps/*/default.yaml)
  predicates    declarative ``forbidden`` matcher used by adversarial events
  score         Scores + score(record, task=None)
  aggregate     per-agent rates, bootstrap CIs, worst case, per-axis; CLI
"""

# Resolved lazily so ``python -m open_apps.alignment.record`` (and the
# aggregate CLI) do not import their own module twice via the package, and so
# importing the record schema does not drag in hydra/deepdiff.
_EXPORTS = {
    "EpisodeRecord": "open_apps.alignment.record",
    "load_record": "open_apps.alignment.record",
    "save_record": "open_apps.alignment.record",
    "Scores": "open_apps.alignment.score",
    "score": "open_apps.alignment.score",
    "aggregate": "open_apps.alignment.aggregate",
    "row_from_record": "open_apps.alignment.aggregate",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    if name in _EXPORTS:
        import importlib

        return getattr(importlib.import_module(_EXPORTS[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
