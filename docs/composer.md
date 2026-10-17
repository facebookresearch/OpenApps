---
title: Config Composer
# Not `summary:`: the theme prints that under the (hidden) H1, where it would
# repeat the component's own lede. `description:` still feeds the meta tags.
description: Pick an agent, tasks and app variations; get the shortest command that runs them.
extra_css:
  - assets/composer/composer.css
extra_javascript:
  - assets/composer/composer.js
---

<div class="composer-host">
<openapps-composer catalog="../assets/composer/catalog.json"></openapps-composer>
</div>

Every option above is generated from the YAML under `config/` when the docs are built
(`uv run -m open_apps.compose.catalog`), so the lists cannot drift from the code. Labels and
descriptions come from each file's leading comment. The command only spells out what differs
from the defaults, and the composer picks the launcher for you:

- one value per axis runs `launch_agent.py`;
- several themes, layouts, content packs or tasks become a `launch_parallel_agents.py` sweep
  (only `apps/*` groups and the task can vary in one sweep);
- several agents, a repeat count or a parallelism cap use the `scripts/conduct.sh` worker pool,
  or `sbatch scripts/conduct_slurm.sh` with the SLURM mode.

The selection lives in the page URL, so a link reproduces it. Paste an existing command into
**Paste a command** to load it; anything the composer does not model is kept as an extra
override and stays in the output. Warnings come from `config/compose_rules.yaml`, and the
**Fix** button applies the suggested change. Press `c` to copy the command.
