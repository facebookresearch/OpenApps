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
- anything parallel is a `launch_parallel_agents.py` sweep, the one interface for parallel runs:
  several themes, layouts, content packs or tasks; several agents (`agents=a,b`); **Repeats**
  (`parallel_tasks.repeats=N`, every job N times with the seed fixed); **Max parallel**
  (`max_parallel=N`, local jobs at once); and the SLURM mode (`mode=slurm_cluster`, where
  `agent.hostname=auto` finds the node serving a self-hosted model). Only `apps/*` groups, the
  task and the agent can vary in one sweep;
- several task groups are not a sweep: they combine into one inventory,
  `'tasks=[all_tasks,openbanking]'`, on any launcher, and the Task list offers the union
  ("Paper tasks" is the paper's set only; OpenBanking and longer-horizon tasks are separate);
- a frozen benchmark suite (`benchmark=<name>`, from `config/benchmark/`) is a sweep of its own,
  `uv run launch_parallel_agents.py benchmark=<name> agent=…`: the suite pins its tasks and app
  variations, so those choices are disabled while it is selected.

The selection lives in the page URL, so a link reproduces it (a link from before the sweep took
over parallel runs, with `target=pool`, opens as the equivalent sweep). Paste an existing command into
**Paste a command** to load it; anything the composer does not model is kept as an extra
override and stays in the output. Warnings come from `config/compose_rules.yaml`, and the
**Fix** button applies the suggested change. With focus anywhere in the composer, press `c` to
copy the command.

The preview is approximate: a short recording of the start page and then the todo app, with a
still of the last app layout or content pack you changed inset over it. There is a recording for
every **device × theme × language × start-page layout × todo layout** (4 × 11 × 3 × 4 × 3 = 1,584
clips), so picking German, a theme, a device or a layout switches to a recording of exactly that.
The recording loops until you press **Pause**, and waits for **Play** if your system asks for
reduced motion.

The full grid is about 250 MB, too big for git, so the docs deploy renders it
(`.github/workflows/static.yml`). It runs in 16 parallel shards, each cached on a hash of
`src/open_apps/`, `config/`, the renderer and `uv.lock`, so a docs-only push renders nothing. The
deploy merges the shards over a small committed set (one clip per device and theme, other axes at
their defaults) in `docs/assets/composer/media/`. That set is what a local `mkdocs serve` shows, and
what a deploy whose render failed falls back to. When there is no exact clip, the preview plays the
closest one for your device (matching theme first, then language, start-page layout, todo layout)
and says what differs, e.g. *Approximate — shown in English, with the default todo layout.*

`scripts/render_composer_media.py` renders the clips. Render a subset into a scratch folder to see
one locally (point the component's `media` attribute at its `manifest.json`):

```bash
uv run scripts/render_composer_media.py --devices desktop --themes dark --languages german \
    --start-layouts gallery --todo-layouts kanban_board --no-stills --out /tmp/composer-media
uv run scripts/render_composer_media.py --full --no-stills --shard 1/16 --out /tmp/shard1  # what CI runs
```

With no flags it re-renders the committed set (device × theme, plus the stills). Never commit the
grid.

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
command to submit yourself. While a recording or run is going, **Stop** ends it (with everything it
started, the apps included), and closing the tab stops it too.

Because a link can carry extra overrides and environment variables, the local page holds Preview,
Record and Run until you confirm the ones a link brought in. The server refuses anything that could
run code regardless: Hydra's own settings (`hydra.*`, `hydra/launcher=...`), instantiation keys
(`_target_` and friends), resolvers other than `oc.env`, `oc.select`, `now` and `hydra`, and
environment variables other than the W&B labels.

