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

The preview is approximate: a short recording for each device and theme, with a still of the
last app layout or content pack you changed (`scripts/render_composer_media.py` renders them).
For the exact thing, run the composer locally:

```bash
uv run compose.py        # opens http://127.0.0.1:8765/?token=…
```

It prints the URL with a per-process token, like Jupyter; open that exact URL. The page trades the
token for a cookie, and the server only answers to that cookie on `127.0.0.1`/`localhost`.

The local page adds four buttons. **Validate** composes the selection with Hydra and lists what
differs from the defaults, or names the override Hydra rejected. **Preview** serves the apps for
the selection and frames them live (one preview at a time; closing the tab stops it). **Record**
runs one episode with the dummy agent and plays the video; tick the box to use the selected agent
instead. **Run** runs the command with a streaming log, except on SLURM, where it gives you the
command to submit yourself.

Because a link can carry extra overrides and environment variables, the local page holds Preview,
Record and Run until you confirm the ones a link brought in. The server refuses anything that could
run code regardless: Hydra's own settings (`hydra.*`, `hydra/launcher=...`), instantiation keys
(`_target_` and friends), resolvers other than `oc.env`, `oc.select`, `now` and `hydra`, and
environment variables other than the worker-pool knobs and W&B labels.

