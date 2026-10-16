"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""The config composer: pick options, get a correct, minimal launch command.

Three pieces, each usable on its own:

* :mod:`~open_apps.compose.catalog` walks ``config/`` through Hydra's own
  config loader and describes every group, option, default and free-form
  scalar -- the single source the UI renders from, so its option lists cannot
  drift from the YAML.
* :mod:`~open_apps.compose.rules` evaluates ``config/compose_rules.yaml``
  (constraints-as-data) against a selection.
* :mod:`~open_apps.compose.command` renders a selection to a shell command
  and parses one back.

The Python implementations are the reference; the docs-site JavaScript port is
tested against the same fixtures (``tests/fixtures/compose/``).
"""
