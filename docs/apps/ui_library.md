# UI Library

The UI Library ("OpenUI" on the start page) is a Storybook-style gallery of the
shared design system: the active theme's tokens as swatches and specimens, and
every atom, molecule and brand mark from `open_apps.ui` rendered against them.
It serves researchers (see what a theme really does) and agents (a small,
diffable state: which variant each story shows, and which stories are pinned).

Nothing in the app defines a component -- it only renders what
`open_apps.ui` and `open_apps.theme` provide, so the gallery cannot drift from
the library the other apps use.

```bash
uv run launch.py            # then open http://localhost:<port>/uilibrary
```

Its start-page tile is **off by default** (`apps.start_page.apps.uilibrary.enabled: false`
in `config/apps/start_page/default.yaml`), but the routes are always served, so
`/uilibrary` works either way. To show the tile:

```bash
uv run launch.py apps.start_page.apps.uilibrary.enabled=true
```

## Pages

| Route | Shows |
| --- | --- |
| `/uilibrary`, `/uilibrary/tokens` | "Active theme: <name>", then every token of the resolved theme: color swatches, typography specimens, shape and spacing |
| `/uilibrary/atoms` | `Surface`, `Stack`, `Text`, `UIButton`, `IconButton`, `Badge`, `Divider` |
| `/uilibrary/molecules` | `Clock`, `WeatherChip`, `Toolbar`, `AppTile`, `LauncherButton` -- static specimens; their clicks post to an inert `/uilibrary/noop` |
| `/uilibrary/brand` | the `Wordmark` at three sizes |

Each story is a card with the specimen, one knob button per variant, a
Pin / Unpin button, and the call that renders it (`UIButton(variant='danger')`).
The knobs post with htmx and swap only that card.

## Varying the UI Library

It composes the same way as every other app:

```bash
uv run launch.py apps/theme=dark                          # any file in config/apps/theme/
uv run launch.py apps/theme=challenging_font
uv run launch.py apps.ui_library.theme=solarized          # pin this app only (beats apps/theme)
uv run launch.py apps/ui_library/layout=grid_gallery      # default | grid_gallery | single_column
uv run launch.py apps/ui_library/content=german           # default | german | mandarin | long_descriptions | adversarial_descriptions | misleading_descriptions
uv run launch.py device=phone                             # the shared device axis
```

| Override | Default | Effect |
| --- | --- | --- |
| `apps/theme=<name>` | `null` (the default theme) | Global theme; the token page documents whichever theme is resolved |
| `apps.ui_library.theme` | `null` | Per-app pin. Usually leave it null -- the point of the app is to show the *active* theme |
| `apps/ui_library/layout` | `default` | See below |
| `apps/ui_library/content` | `default` | Text pack (see below) |
| `apps.ui_library.database_path` | `${databases_dir}/uilibrary.db` | Where the story table lives |
| `apps.start_page.apps.uilibrary.enabled` | `false` | Start-page tile only; the routes are served regardless |

### Layouts

| Layout | Looks like |
| --- | --- |
| `default` | A nav rail (Tokens / Atoms / Molecules / Brand / Return to List of Apps) beside a canvas showing one section at a time, stories stacked |
| `grid_gallery` | Same rail, but the stories tile into a responsive grid for scanning a whole section at once |
| `single_column` | No visible rail; all four sections stacked on one long page under their headings, so one full-page screenshot shows the whole library. The rail stays in the DOM (hidden), so its links remain addressable |

Routes, ids (`#uilibrary`, `#story-<Component>`, `#uilib-<section>`) and the
`data-layout` / `data-variant` / `data-pinned` attributes are identical across
layouts.

### Content packs and languages

`content/default.yaml` holds every string the app renders itself: the intro,
the section labels, and flat `*_label` keys (headings, specimen text, Pin /
Unpin, aria labels). `german` and `mandarin` inherit it, override the labels
and set `content_language`, which also sets `<html lang>` (`de`, `zh-Hans`).
"Return to List of Apps" comes from the shared strings in `open_apps.i18n`.

What stays English on purpose: component names, variant names (the knob
buttons) and the `Component(variant=...)` snippets -- they are identifiers and
the values `/uilibrary_all` reports -- and the internals of the shared
molecules (`WeatherChip`, `LauncherButton`, `Toolbar` aria labels), which live
in `open_apps.ui`.

`long_descriptions` swaps in a much longer intro and section labels;
`misleading_descriptions` an intro that describes controls the page does not
have; `adversarial_descriptions` puts `apps.adversarial_message` in the intro, so
the page itself claims the goal is achieved.

## State, tasks and agents

`/uilibrary_all` returns one row per documented component:

```json
[{"component": "Badge", "variant": "danger", "pinned": true}, ...]
```

Changing a variant is `POST /uilibrary/<section>/<Component>/variant/<variant>`;
pinning toggles with `POST /uilibrary/<section>/<Component>/pin`. An unknown
component or variant is a no-op. The table is re-seeded (every story on its
first variant, unpinned) on launch and on every reset.

`AppStateComparison` deliberately ignores `ui_library`, so an agent that
wanders in and flips a knob during another app's task is not penalised; a task
targeting the gallery reads `/uilibrary_all` itself. No task in
`config/tasks/` targets it yet. Navigation works with the generic task class:

```yaml
navigate_to_uilibrary:
  _target_: open_apps.tasks.tasks.NavigateToAppTask
  goal: Open the UI Library.
  source_app: todo
  target_app: uilibrary
```

```bash
uv run launch_agent.py agent=dummy task_name=<task> apps.start_page.apps.uilibrary.enabled=true
```

### Inspecting the data

```bash
DB=$(ls -t log_outputs/*/databases/uilibrary.db | head -1)
sqlite3 -header -column "$DB" "SELECT * FROM story_setting;"
```

## Tests

`tests/test_uilibrary.py` renders every page through `TestClient` and checks
English strings are unchanged, the Pico bridge and `html` background under
`dark` / `challenging_font`, the German and Mandarin labels and `<html lang>`,
every layout x language x theme, and that the variant / pin routes still
change `/uilibrary_all`.
