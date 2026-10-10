# Testing recent changes

```bash
uv sync
uv run playwright install chromium   # once
```

## 0. Automated checks first

```bash
uv run -m pytest tests/                                        # what CI runs
uv run -m pytest tests/test_chrome.py tests/test_shell_layout.py tests/test_device.py \
    tests/test_theme.py tests/test_start_page.py tests/test_todo_ui.py tests/test_messenger_ui.py \
    tests/test_codeeditor_theme.py tests/test_onlineshop.py tests/test_openbanking.py \
    tests/test_launcher_recording.py tests/test_apps.py        # the suites behind the UI work below
```

## 1. Window chrome: title bar, minimise / maximise / close, dock, agent cursor

On by default on every page of every app. None of it is scoreable: it changes the
observation, not the reward.

```bash
uv run launch.py                                       # mac-style traffic lights, dock, glowing cursor
uv run launch.py apps/chrome=windows                   # caption buttons on the right
uv run launch.py apps/chrome=none                      # no chrome: the control condition
uv run launch.py apps.chrome.dock.show=all             # every app in the dock, not just the pinned ones
uv run launch.py apps.chrome.cursor.show=always
uv run launch.py apps.chrome.cursor.enabled=false
uv run launch.py apps.chrome.titlebar.enabled=false    # dock only
uv run launch.py device=phone                          # no title bar on a phone; the dock stays
```

To check: minimise, maximise and close work in every app, including maps and the shop (they're
template-rendered, not FastHTML). Opening an app puts a running dot in the dock. The cursor
glides to each click and ripples. The code editor still fits the window with the title bar and
dock on.

## 2. Realism / styles sweep across every app (visible by default)

Every app now reads like the real product it imitates, in every theme:

```bash
uv run launch.py                                       # todo, calendar, messenger, maps, code editor
uv run launch.py apps/theme=dark                       # hover states and text stay legible
uv run launch.py apps/theme=solarized
uv run launch.py apps/start_page/layout=gallery        # wordmark lead, centred tile icons and names
```

To check:

- todo: task-manager rows, quiet row actions, muted done rows.
- calendar: no "Location: None" on event pages.
- messenger: proper bubbles, and sent bubbles are legible.
- maps: panel, search and controls follow real maps conventions.
- code editor: explorer icons and chevrons, tab strip, editor header, status bar, and no
  white-on-white text.
- In every app, "Return to List of Apps" stays prominent.

## 3. Layout variants (#82)

```bash
uv run launch.py apps/todo/layout=kanban_board         # header counts, card surfaces
uv run launch.py apps/calendar/layout=agenda_first
uv run launch.py apps/calendar/layout=sidebar_nav
uv run launch.py apps/messenger/layout=split_inbox     # open a chat: the list stays beside it
uv run launch.py apps/messenger/layout=compact_list
uv run launch.py apps/maps/layout=sidebar_left
uv run launch.py apps/maps/layout=bottom_sheet
uv run launch.py apps/code_editor/layout=sidebar_right
uv run launch.py apps/code_editor/layout=top_tree
uv run launch.py apps/openbanking/layout=card_list
uv run launch.py apps/messenger/layout=split_inbox apps/theme=dark   # layouts and themes compose
```

## 4. Desktop shell start page

```bash
uv run launch.py                                       # wallpaper, toolbar, live clock, launcher, pinned shortcuts
uv run launch.py apps/theme=meta
uv run launch.py apps/theme=meta_dark
uv run launch.py 'apps.start_page.desktop.unpinned=[messages]'   # messenger only reachable via the launcher
uv run launch.py apps/start_page/layout=gallery
uv run launch.py apps/start_page/layout=clickable_logos
uv run launch.py apps/start_page/layout=broken_logos
```

To check: the light/dark toggle and pinning both persist. `curl localhost:5001/desktop_all`
should show `mode` and `pinned` changing after you click them. Pinning an app also puts it in
the dock.

## 5. Devices

```bash
uv run launch.py device=phone      # 390x844; the shell becomes a home screen with an app drawer
uv run launch.py device=tablet
uv run launch.py device=laptop
uv run launch.py device=phone apps/theme=meta_dark
```

## 6. Themes (#72): one `theme` axis for every app

```bash
uv run launch.py apps/theme=dark
uv run launch.py apps/theme=solarized
uv run launch.py apps/theme=material
uv run launch.py apps/theme=bootstrap
uv run launch.py apps/theme=mono                 # was appearance=black_and_white
uv run launch.py apps/theme=colorblind           # was appearance=colorblind_access
uv run launch.py apps/theme=challenging_font
uv run launch.py apps.todo.theme=solarized       # one app only
uv run launch.py apps/theme=default              # code editor drops its vscode_dark default too
```

## 7. OpenBanking

```bash
uv run launch.py                                       # localhost:5001/openbanking
uv run launch.py apps.openbanking.visible_transactions=0   # whole ledger on screen
uv run launch.py apps/openbanking/layout=card_list
curl localhost:5001/openbanking_all
```

## 8. Online shop: WebShop Python rewrite (#78)

```bash
uv run launch.py                                          # localhost:5001/onlineshop
uv run launch.py apps.onlineshop.product_images=glyphs    # no egress: generated SVGs
uv run launch.py apps/onlineshop/content=fixture          # small offline catalog
uv run launch.py apps/onlineshop/content=default          # empty catalog: the shop is hidden
uv run launch.py apps/onlineshop/layout=grid              # default | grid | compact_table
uv run launch.py apps/onlineshop/content=german
uv run scripts/fetch_webshop.py --inspect                 # look at the HF source; writes nothing
```

## 9. OpenUI / UI Library (#83): tile disabled by default

```bash
uv run launch.py                                               # open localhost:5001/uilibrary directly
uv run launch.py apps.start_page.apps.uilibrary.enabled=true   # bring the tile back
uv run launch.py apps/ui_library/layout=single_column
uv run launch.py apps/ui_library/layout=grid_gallery
uv run launch.py apps/ui_library/content=german
```

To check: on `/uilibrary/atoms`, switch a variant and toggle a pin, then
`curl localhost:5001/uilibrary_all` and confirm the stored state matches what you clicked.

## 10. Episode video (window chrome and cursor included)

```bash
uv run launch_agent.py agent=dummy record_video=True       # .webm under logs_dir/recordings
uv run launch_agent.py agent=dummy record_video=True device=phone
```

## 11. Hot-reloading dev server (#76)

```bash
./scripts/dev.sh
OPENAPPS_DEV_OVERRIDES="apps/theme=meta_dark apps/chrome=windows" ./scripts/dev.sh
```

## 12. Screenshot sweeps (visual regression)

```bash
uv run python tests/save_screenshots.py                                   # default set vs reference_screenshots
uv run python tests/save_screenshots.py --headed --viewport 1280x800
uv run python tests/save_screenshots.py --variation layout_calendar_agenda_first \
    layout_calendar_sidebar_nav layout_messenger_split_inbox layout_messenger_compact_list \
    layout_maps_sidebar_left layout_maps_bottom_sheet layout_code_editor_sidebar_right \
    layout_code_editor_top_tree layout_kanban_board
uv run python tests/save_screenshots.py --variation theme_meta theme_meta_dark theme_solarized \
    --route start_page todo calendar messages messages_thread maps codeeditor openbanking
```

Output goes to `tests/generated_screenshots/`. Look at the images, not just the exit code:
white-on-white text has gotten past every markup assertion before.
