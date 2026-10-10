title: Start with OpenApps

> Building Blocks for Digital Agents Research

New to agents? See our [Intro to UI Agents](Intro to UI Agents.md). We take you through the installation and running your first agent step-by-step.

Why OpenApps? Evaluate and train multimodal agents to use apps like humans do (by clicking, typing, and scrolling):

✅ **Unlimited data** (for evaluating and training UI-agents): Configurable state and design to generate thousands of versions of each app

✅ **Lightweight**: runs on a single CPU (and Python-based); no Docker or OS emulators needed

✅ **Ground truth rewards**: task rewards are based on the underlying state and all app logic is transparent in Python

### Install

Install the conda alternative [uv](https://docs.astral.sh/uv/getting-started/) and clone the repo:

```bash
   git clone https://github.com/facebookresearch/OpenApps.git
```

Install dependencies:

```bash
   uv sync
```

For other installation options and online shop setup see [Installation](installation.md).

### Run OpenApps

```bash
uv run launch.py
```
![landing](images/landing.png)

For an overview, checkout our [video tutorial](https://www.youtube.com/watch?v=gzNW_LXE7OE).

### App variations
Each app can be modified with variables available in `config/apps`. You can override any of these via command line:

```bash
uv run launch.py 'apps.todo.init_todos=[["Call Mom", false]]'
```

OpenApps also comes with pre-defined variations that can affect the content and appearance of apps.

Appearance is split along two axes:

* **Theme** -- *look*: colors, typography, shape. One shared set of design
  tokens in `config/apps/theme/`, applied to **every** app at once.
* **Layout** -- *structure*: how an individual app arranges itself. Per-app,
  under `config/apps/<app>/layout/`.

#### Theme

/// tab | challenging font

    ::bash
    uv run launch.py apps/theme=challenging_font

![landing](images/landing-challenging-font.png)
///
/// tab | dark theme

    ::bash
    uv run launch.py apps/theme=dark

![landing](images/landing-dark.png)
///
/// tab | default

    ::bash
    uv run launch.py apps/theme=default

![landing](images/landing.png)

///

`apps/theme=` is one override that themes every app. To theme one app only,
leaving the rest on the global theme, set its own field:
`uv run launch.py apps.calendar.theme=dark`.

Shipped themes: `default`, `dark`, `mono`, `challenging_font`, `colorblind`,
`solarized`, `material`, `bootstrap`, `meta`, `meta_dark`, `vscode_dark`.
Adding one means adding a yaml file to `config/apps/theme/` -- no app code
changes.

Precedence, highest first:

| Source | Example | Beats |
| --- | --- | --- |
| per-app pin | `apps.calendar.theme=dark` | everything |
| global selection | `apps/theme=dark` | the app's own default |
| the app's own default | `theme_default:` in its `default.yaml` | nothing |
| `default` | -- | -- |

The middle two are the ones worth understanding. An app may declare a
`theme_default` -- the code editor's is `vscode_dark`, so `uv run launch.py`
opens on something that reads as a code editor. It applies *only* when no
theme was selected, so sweeping the axis still moves every app, including the
`apps/theme=default` cell the rest of the sweep is compared against. A
per-app `theme` pin is the opposite: it wins over the sweep, which is what you
want for dressing one app differently on purpose and not much else.

This is why the `apps/theme` group defaults to `null` in `config/config.yaml`
rather than to `default` -- "nothing was selected" has to be distinguishable
from "`default` was selected". A null group default is still bindable without
a `+`.

A theme file is a set of design tokens plus a small `assets` block:

```yaml
# config/apps/theme/dark.yaml
name: dark
tokens:
  color-bg: "#121212"       # -> --color-bg, consumed as var(--color-bg)
  color-primary: "#bb86fc"
  font-family: "'Inter', system-ui, sans-serif"
  radius: "4px"
assets:
  tone: dark                # apps map this onto their own non-CSS assets
  icon_set: bw
```

Every `tokens` entry becomes a CSS custom property. `assets` covers the
choices a CSS variable cannot reach -- the start page's raster icons, the
Leaflet tile layer, the CodeMirror stylesheet. The keys are deliberately
app-agnostic: the theme says `tone: dark` and each app picks its own dark
asset, so a theme file never has to know which apps exist.

#### Layout

```shell
uv run launch.py apps/todo/layout=kanban_board
uv run launch.py apps/maps/layout=sidebar_left
uv run launch.py apps/start_page/layout=gallery
```

A layout changes *structure* only -- where things sit on the page. Colors and
fonts stay with the theme, and routes, element ids and the `/<app>_all` state
endpoints are identical across layouts, so rewards are unaffected by the
layout in play. Every app has `default`, plus:

| App | Layouts | What changes |
| --- | --- | --- |
| `start_page` | `desktop` (default) | Toolbar, wallpaper and pinnable shortcuts; a home screen on a phone |
| | `gallery` | The original tile grid the paper's figures show |
| | `broken_logos` | Gallery with icons detached from their tiles |
| | `clickable_logos` | Gallery with tile logos as their own click targets |
| `todo` | `kanban_board` | Status columns of cards instead of one list |
| `calendar` | `agenda_first` | Lands on the agenda, not the month grid |
| | `sidebar_nav` | Month nav and view toggle become a left rail |
| `messenger` | `split_inbox` | Chat list stays beside the open thread |
| | `compact_list` | Dense avatar-less rows; flat messages, not bubbles |
| `maps` | `sidebar_left` | Search and Saved Locations left of the map |
| | `bottom_sheet` | Sidebar becomes a panel under the map |
| `code_editor` | `sidebar_right` | File tree right of the editor |
| | `top_tree` | File tree as a strip above the editor, no side column |
| `onlineshop` | `grid` | Product cards in a grid instead of rows |
| | `compact_table` | Dense, text-only product table |

The start page is the landing surface an agent sees first, so it carries the
most:

* **`desktop`** — the default. A toolbar (launcher, clock, weather, light/dark
  toggle) over a generated wallpaper, with pinnable app shortcuts. On a phone
  it renders as a home screen instead; see [Devices](#devices).
* **`gallery`** — the original html5up tile grid: five coloured tiles under a
  "Welcome to OpenApps!" headline. Still a single override away, and it is what
  the paper's figures show.
* **`broken_logos`** / **`clickable_logos`** — variations *of the gallery*
  (they compose it), so selecting either also selects the tile grid.

Two pieces of desktop state are scoreable and served at `/desktop_all`: the
light/dark mode and the list of pinned app keys. `launcher_open` is
deliberately excluded — a task should not pass or fail on whether the agent
left a popover showing.

##### Pinning, and making the agent use the launcher

Every app is pinned by default (`pinned: all`), so each one has a shortcut on
the desktop and the launcher is a convenience. The launcher only *tests*
anything once something the agent needs is behind it, and that is one
override — name the few to hide rather than re-listing the many to keep:

```bash
# OpenMessages has no shortcut; the only route to it is the launcher menu
uv run launch_agent.py agent=dummy task_name=navigate_to_messenger \
    apps.start_page.desktop.unpinned=[messages]

# or sweep it as a variation axis
uv run launch_parallel_agents.py \
    'parallel_tasks.app_variations=[[],["apps.start_page.desktop.unpinned=[messages,maps]"]]'
```

`pinned` also takes an explicit list, and resolution always follows the app
inventory's order rather than the order you wrote — so "the third icon" means
the same thing however the override was typed. `all` expands to the apps that
actually render, so the online shop is not pinned while it is gated off, which
would otherwise put a key in `/desktop_all` with no tile on the page.

The phone home screen is the exception: it splits apps between a grid
(unpinned) and a dock (pinned), so `all` would dock everything and leave the
grid empty. It takes `pinned_by_variant.home_screen` instead. It is not the
composition for this experiment anyway — a phone's unpinned apps sit on the
grid in plain view, not behind the menu.

#### Migrating from `appearance`

The `appearance` group these two replaced was removed: there are no
`config/apps/<app>/appearance/` directories and no app renders from one.
`apps/<app>/appearance=...` is a Hydra composition error, not a silent
no-op. Translate overrides as:

| Old override | New override |
| --- | --- |
| `apps/<app>/appearance=default` | `apps/theme=default` |
| `apps/<app>/appearance=dark_theme` | `apps/theme=dark` |
| `apps/<app>/appearance=black_and_white` | `apps/theme=mono` |
| `apps/<app>/appearance=challenging_font` | `apps/theme=challenging_font` |
| `apps/code_editor/appearance=colorblind_access` | `apps/theme=colorblind` |
| `apps/todo/appearance=kanban_board` | `apps/todo/layout=kanban_board` |
| `apps/start_page/appearance=broken_logos` | `apps/start_page/layout=broken_logos` |
| `apps/start_page/appearance=clickable_logos` | `apps/start_page/layout=clickable_logos` |

The theme rows are global, so the six per-app overrides the old dark variation
needed collapse to one `apps/theme=dark`. Two renderings shift slightly:
`mono` picks white-page/black-ink for every app, where the old
`black_and_white` variants disagreed on polarity (calendar inverted the page,
the rest did not), and `colorblind` is now available to all apps rather than
the code editor alone.

##### Reproducing the paper

The paper's variation grid is indexed by `appearance` stem names, and the two
axes above do not reproduce it pixel-for-pixel — see the shifts noted just
above. **To reproduce the numbers in
[the paper](https://arxiv.org/abs/2511.20766), use the `v1.0-paper` tag**, the
last tree with `appearance` intact:

```bash
git checkout v1.0-paper
uv run launch.py apps/todo/appearance=dark_theme
```

Theme and layout are the supported axes going forward; `v1.0-paper` is frozen
and gets no fixes.

One exception to the removal: the MCP `reconfigure` tool still accepts an
`appearance=` argument, translates it onto `theme`/`layout` per the table
above, and raises a `DeprecationWarning`. It exists so existing MCP clients
keep working for one release and will be removed — see
[`src/open_apps/mcp/README.md`](https://github.com/facebookresearch/OpenApps/blob/main/src/open_apps/mcp/README.md).

#### Window chrome

Every page of every app is drawn as a desktop window: a **title bar** with
close / minimise / maximise controls, a **dock** of app shortcuts with their
names underneath, and a rendered **agent cursor**. None of it is drawn by the
apps; a response middleware on the web server injects it into each full page,
so the template-rendered apps (maps, the shop) get it too.

| Selection | Result |
| --- | --- |
| `apps/chrome=default` | macOS-style traffic lights, dock, glowing cursor |
| `apps/chrome=windows` | Windows-style caption buttons on the right |
| `apps/chrome=none` | no chrome — the pages exactly as the apps draw them |

The dock is the cross-app shortcut layer for long-horizon tasks: one click (or
`Alt+1`…`Alt+9`, advertised to the accessibility tree via
`aria-keyshortcuts`) to any app, `Alt+0` back to the desktop, and an
**All apps** panel listing everything. It shows the desktop's pinned apps by
default, so pinning on the desktop docks the app too.

```bash
uv run launch.py apps.chrome.dock.show=all              # every app, ignoring pins
uv run launch.py apps.chrome.dock.exclude=[messages]    # take one out of the dock
uv run launch.py apps.chrome.dock.labels=false          # bare icons: a visual-grounding probe
```

The cursor exists because Playwright teleports the real pointer and headless
Chromium never draws one. The rendered cursor eases from its last position to
each new one, so recordings show the agent's hand moving, and it persists
across page loads, so the screenshot after a click shows where the click
landed. It follows `fill()` into text fields too, and draws a ripple on
click. By default (`apps.chrome.cursor.show=auto`) it only appears in an
automated browser — agent runs, screenshot scripts, recordings — and never
over a person's own pointer. The glide finishes inside BrowserGym's 500 ms
post-action settle, so a screenshot never catches it mid-flight.

None of this is scoreable: which apps are "running" and which windows are
maximized are transient UI that never reaches `get_current_state()`. It changes
the *observation*, not the reward. Compare against `apps/chrome=none` like any
other appearance axis.

#### Content

/// tab | german

    ::bash
    uv run launch.py apps/start_page/content=german

![landing](images/landing-german.png)
///
/// tab | long_descriptions

    ::bash
    uv run launch.py apps/start_page/content=long_descriptions

![landing](images/landing-long-descriptions.png)
///
/// tab | pop-up

    ::bash
    uv run launch.py apps/pop_ups=adversarial_descriptions

![landing](images/landing-popup.png)

///

Content is per app, so each override names the app it changes, e.g.
`uv run launch.py apps/calendar/content=german`.

You can see the specific variables for each defined in the individual apps.
For example, `config/apps/theme/dark.yaml` for the shared design tokens,
`config/apps/start_page/layout/broken_logos.yaml` for a per-app structure
variant, and `config/apps/maps/default.yaml` for behaviour (map zoom, tile
layer, route planning) that is neither.

#### Layout

Where *appearance* varies colours and fonts, *layout* varies page structure —
what elements exist and how they are arranged — so an agent cannot rely on a
fixed DOM. Apps with a `layout` group:

| App | Layouts |
| --- | --- |
| `todo` | `default`, `kanban_board` |
| `onlineshop` | `default` (one product per row), `grid` (card grid), `compact_table` (dense text-only table) |

```shell
uv run launch.py apps/onlineshop/layout=grid
uv run launch.py apps/todo/layout=kanban_board apps/onlineshop/layout=compact_table
```

These apps render from the shared design tokens rather than from an
`appearance` group, so their colours and fonts come from `apps/theme=` instead:

```shell
uv run launch.py apps/theme=solarized              # every app
uv run launch.py apps.onlineshop.theme=dark        # just the shop
```

Optional: to save screenshots of all apps with a specific variation for testing, we offer `tests/save_screenshots.py --variation default --output-dir outputs/2026-04-13/default/` to make this easy.

## Exposing OpenApps as an MCP server

If you want an agent to interact with OpenApps using [MCP](https://modelcontextprotocol.io/docs/getting-started/intro) please see `src/open_apps/mcp/README.md`.

## Launch Agent

For agents to directly interact with apps, install: `playwright install chromium`.


Launch an agent to perform a task of *adding a meeting with Dennis to the calendar*:

/// tab | Random Click Agent

    ::bash
    uv run launch_agent.py agent=dummy task_name=add_meeting_with_dennis
///
/// tab | GPT-5.1 Agent

    ::bash
    # export OPENAI_API_KEY=""
    uv run launch_agent.py agent=GPT-5-1 task_name=add_meeting_with_dennis
///

You can specify the agent of your choice with the `agent=` argument. For example `agent=dummy` is a simple agent that clicks randomly on any buttons, great for exploration!

Learn more about launching with OpenAI, Claude, VLLM models, or specialized models such as UI-Tars in [agents guide](agents.md) and available tasks in our [task guide](tasks.md).

!!! info "Note:"
    To test the ability of a model to navigate the UI without simplified HTML, set: `agent.use_axtree=False`

To see the agent solving the task live:
```
uv run launch_agent.py browsergym_env_args.headless=False
```

![Live Agent](images/gif.gif)

To record the full episode as a video instead:
```
uv run launch_agent.py agent=dummy record_video=True
```

Each episode is saved as `<time>_<task>_<agent>_<pass|fail>[_job<N>].webm`, at
the device's viewport size, with the window chrome and the agent cursor in
frame (the cursor is parked mid-screen until the agent's first move). The
original also stays in the experiment directory under `task_video/`.

Where it goes is `record_video_dir`, which defaults to `<logs_dir>/recordings`:

| Launched with | Recordings land in |
| --- | --- |
| `launch_agent.py` | `log_outputs/<run>/recordings/` |
| `launch_parallel_agents.py` (local) | `<sweep logs_dir>/recordings/` — one folder for every job |
| `launch_parallel_agents.py mode=slurm_cluster` | the same, on the cluster's shared `logs_dir` |
| `scripts/conduct.sh record_video=True` / `conduct_slurm.sh` | each run's own `logs_dir` (point them at one folder with `record_video_dir=`) |

The destination is resolved once, in the process you launched, and handed to
every job as an absolute path. A relative `record_video_dir=videos` therefore
means `./videos` from where you ran the command, even for SLURM jobs that
start on a compute node in another directory. Set an absolute path to collect
recordings across launches:

```
uv run launch_parallel_agents.py mode=slurm_cluster record_video=True \
    record_video_dir=/path/on/shared/storage/recordings
```

With `use_wandb=True` each video is also logged to its run as
`episode_video`, which is usually the easiest way to watch cluster episodes
from a laptop.

### Devices

The device is a variation axis of its own, alongside theme, layout, content and
pop-ups. `config/device/` ships four:

| `device=` | Viewport | Form factor | Input | User agent |
| --- | --- | --- | --- | --- |
| `desktop` (default) | 1920×1080 | desktop | mouse | Chromium's own |
| `laptop` | 1280×800 | desktop | mouse | Chromium's own |
| `tablet` | 820×1180 | tablet | touch, no hover | Chrome, Android tablet |
| `phone` | 390×844 | phone | touch, no hover | Chrome, Android phone |

```bash
uv run launch.py +experiment=phone                      # browse the phone build
uv run launch_agent.py agent=dummy +experiment=phone    # run an agent on it
uv run launch_agent.py agent=dummy device=tablet        # just the device
```

The mobile devices set a matching UA because `is_mobile` alone leaves Chromium
announcing itself as desktop Chrome — touch input, phone width, desktop
browser, which is a contradiction anything UA-sniffing would see. They claim
Chrome rather than iOS Safari because the engine really is Blink, and the
version is pinned so a rendering does not change because the month did.
Opt out with `device.user_agent=null`, or set your own.

One setting moves two things:

* **the browser** — `browsergym_env_args.task_kwargs.screen_resolution` is
  `${device.viewport}`, and `open_apps.agent.env_args.DeviceEnvArgs` forwards
  `is_mobile`, `has_touch`, `device_scale_factor` and `user_agent` to the
  Playwright context. On a phone or tablet the page gets a real mobile visual
  viewport and a coarse pointer, so `@media (hover: none)` and
  `(pointer: coarse)` match and hover-only affordances correctly disappear;
* **the apps** — the node is mirrored to `apps.device`, so a server-rendered
  layout can pick a composition for the form factor rather than only reflowing
  to the width.

The start page's desktop shell does exactly that. On a phone it renders a home
screen: status bar, wordmark widget, an icon grid of the apps that are **not**
pinned, and a dock holding the ones that are — so pinning moves an app into the
dock, where pinning on a desktop moves it onto the desktop surface. The routes,
the test ids and `/desktop_all` are the same on both, so a task written against
one scores unchanged on the other; what differs is what the agent can see and
how far it has to travel. Which composition a form factor gets is config, not
code:

```bash
# the control condition: the desktop composition, in a phone-sized window
uv run launch.py +experiment=phone apps.start_page.desktop.variants.phone=shell
```

Adding a device is a file in `config/device/`; a form factor with no variant of
its own falls back to the desktop composition rather than to a blank page.

#### The preview window

`uv run launch.py` opens a browser on the apps once they answer, so "run it and
look at it" is one command rather than two plus copying a URL out of the log.
It is a Playwright Chromium, not the system browser, and it is handed the same
`config/device/` emulation the agent path gets — viewport, `is_mobile`,
`has_touch`, `device_scale_factor`, `user_agent`:

```bash
uv run launch.py                    # 1920×1080 desktop window
uv run launch.py device=phone       # 390×844, touch pointer, phone UA
uv run launch.py headless=True      # serve only, open nothing
```

`webbrowser.open` is not used because it can neither size a window nor make
`@media (hover: none)` match, so `launch.py device=phone` would have opened a
desktop-width page and the phone layout you asked to look at would not have
been the thing on screen.

It never opens for an agent run. `launch_agent.py` and
`launch_parallel_agents.py` re-invoke `launch.py` with `headless=True`, and so
does `tests/save_screenshots.py` — all three bring their own browser, and a
second window fighting for focus mid-episode is not something an eval needs.

Nothing here is fatal: with Playwright missing, its Chromium not installed, or
no display available, this falls back to the system browser and finally to
printing the URL. Serving the apps is the job; opening a window is a
convenience.

!!! warning "Keep `device_scale_factor` at 1"
    Screenshots are captured in *device* pixels and actions are dispatched in
    *CSS* pixels, and nothing in between divides by the ratio — the agent's
    coordinate space comes straight from the screenshot's shape. At scale 2 a
    grounded click lands at twice the intended offset. Every shipped device
    keeps it at 1, retina or not.

### Logs

By default, information about the number of steps an agent took, task success, etc. will be shown in the terminal:

```
...
Experiment results
exp_dir: /Users/m...
n_steps: 10
cum_reward: 0.0
stats.cum_agent_elapsed: 0.0017838478088378906
stats.max_agent_elapsed: 0.0002570152282714844
...
```

All logs are stored `log_outputs` will contain information about each run

![](https://raw.githubusercontent.com/wandb/assets/main/wandb-github-badge-gradient.svg)
You can also enable logging to weights and biases by logging into your account and setting the flag: `use_wandb=True`.



## Launch Agent(s) Across Multiple Tasks
> launch thousands of app variations to study agent behaviors in parallel

!!! info "Note:"
    Parallel launching works with SLURM. Be sure to update configs in `config/mode/slurm_cluster.yaml`.

You can launch one (or multiple) agents to solve many tasks in parallel, each in an isolated deployment of OpenApps, using SLURM:

```
uv run launch_parallel_agents.py mode=slurm_cluster agent=dummy use_wandb=True
```

This launches 6 parallel independent random click agents to solve each task in each app variation as defined in `config_parallel_tasks.yaml`

```yaml
parallel_tasks:
  _target_: open_apps.tasks.parallel_tasks.AppVariationParallelTasksConfig
  task_names:
    - add_meeting_with_dennis
    - add_call_mom_to_my_todo
    - save_paris_to_my_favorite_places
  app_variations:
    - ["apps/start_page/content=default", "apps/calendar/content=german"]
    - [
        "apps/theme=dark",
      ]
```

You can modify the set of tasks or app variation by updating the `config_parallel_tasks.yaml`. We ensure:

* Each deployment of OpenApps can have a different theme (global), plus layout and content per app.
* Each task is launched in an isolated environment for reproducible results.

To run **every** task in the loaded tasks config (rather than listing a subset by
hand), set `task_names` to `all`. This expands to all task names defined in the
selected `tasks=` group, so it composes with any tasks file:

```
uv run launch_parallel_agents.py \
  mode=slurm_cluster agent=dummy tasks=longer_horizon \
  parallel_tasks.task_names=all use_wandb=True
```

Passing an explicit list (e.g. `parallel_tasks.task_names=[task_a,task_b]`) still
runs only that subset.

You can also select a task group to run via `tasks=longer_horizon parallel_tasks.task_names=all`.

### Running across goal variations

Every task ships with **goal variations** — the same task with the goal reworded
in a different style. Styles are `casual`, `formal`, and `unrelated_context`
(the instruction wrapped in unrelated chit-chat), with 9 variations per task.
They live in `config/tasks/user_goal_variations.yaml`, keyed
`<original_task>__<style>_<n>` (e.g. `add_meeting_with_dennis__formal_1`), and
each one preserves the original task's reward — only the `goal` wording differs
and a `goal_style` field records the style.

To run agents across **all** tasks and their goal variations in parallel, use
the dedicated config, which lists every task name swept over a single default
app variation:

```
uv run launch_parallel_agents.py \
  --config-name=config_parallel_tasks_across_goal_variations mode=slurm_cluster
```

Use `mode=local` to run the jobs sequentially in the current process instead of
on SLURM. This expands to one isolated job per goal phrasing, letting you
measure how robust an agent is to how the same task is worded.

## Testing

Run all tests via:

```python
uv run -m pytest tests/
```

## Attribution

Our apps are built on top of several excellent frameworks:

- FastHTML [framework](https://github.com/AnswerDotAI/fasthtml) and [examples](https://github.com/AnswerDotAI/fasthtml-example) which allowed us to build fully functional apps in Python, the language most familiar to AI researchers.
- [Browser Gym](https://github.com/ServiceNow/BrowserGym/blob/main/LICENSE) and [AgentLab](https://github.com/ServiceNow/AgentLab/blob/main/LICENSE):
- [Open Street Maps](https://www.openstreetmap.org/copyright): for our Maps apps.
- (for the online shop) [WebShop](https://github.com/princeton-nlp/WebShop/blob/master/LICENSE.md), developed by Princeton University: our shop is a rewrite, and its catalog is converted from WebShop's item dump.

Some icons are have been designed using resources from Flaticon.com


Our work is licensed under CC-BY-NC, please refer to the [LICENSE](https://github.com/facebookresearch/OpenApps/blob/main/LICENSE) file in the top level directory.
Copyright © Meta Platforms, Inc. See the [Terms of Use](https://opensource.fb.com/legal/terms/) and [Privacy Policy](https://opensource.fb.com/legal/privacy/) for this project.
