# Changelog

All notable changes to OpenApps are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html): a major version
changes what an agent observes or how it is scored, so results are comparable
only within one major version.

Every release is a git tag. To reproduce a result, check out its tag and run
`uv sync` there; the tag pins the code, the configs and `uv.lock` together.

## [Unreleased]

**Contacts.** Names now live in one place. The messenger stores phone numbers
and emails, never names, and asks Contacts what to draw, so saving an unknown
number renames its thread everywhere. Messenger state changes shape, and
Contacts is an eighth app pinned to every task's desktop, so scores are not
comparable with 2.0.0.

### Added

- **Contacts** (`/contacts`): people with any number of phones and emails, a
  note and a favourite flag. List, card, edit and new forms, delete with a
  confirm step, search, and Copy pills on every number. Layouts `default`
  (list beside card) and `list_detail` (one pane at a time); content packs
  for every language and description variation, the misleading ones with
  decoys such as a second "Bob". Scored at `/contacts_all`.
- **Unknown senders**: a thread from a number not in Contacts shows the
  formatted number and a "Not in your contacts" banner whose Add link opens a
  prefilled contact form. The default seed has one, from +1 (555) 014-2290.
- **Contact tasks** (`tasks=contacts`): add, edit and delete a contact, save
  the unknown sender, reply to them by number, and save-then-message them by
  name, each with casual, formal and unrelated-context phrasings. Kept out of
  `all_tasks`, like the OpenBanking set.
- Known sender names in the messenger link to their contact card.

### Changed

- **Messenger threads are keyed by handle.** `/messages_all` is now
  `[{"handle", "messages": [[text, sender, timestamp], ...]}]`, where
  `sender` is `"you"` or a handle; it held `user` names before. Thread URLs
  are `/messages/<handle>/`; the old name URLs redirect when exactly one
  contact matches.
- **`SendMessageTask.to` resolves through Contacts**: a handle or group key
  first, then an exact display name, then a unique first name. A name that
  matches several contacts, or none, raises a `ValueError` naming the
  candidates instead of scoring against a guessed thread.
- Messenger content packs moved to the handle-keyed `threads:` / `groups:`
  format (`scripts/migrate_messenger_handles.py` converts old packs).

### Fixed

- Parallel sweeps launch every app: `config_parallel_tasks*.yaml` now include
  Contacts, and the goal-variation sweep includes `apps/theme` like
  `config.yaml` does, so its runs may differ from earlier ones.
- The todo "Add button" UI question's answer is the new-todo input row.

## [2.0.0] - 2026-10-23

**Realism.** Every app now looks and behaves like the product it imitates,
under every theme, layout and device. Two new apps (OpenBanking, OpenUI) join
the five from the paper, the online shop runs without Java, and cross-app
tasks can carry a value from one app to another. Observations differ from
1.0.0 on every page, so 1.0.0 numbers are not comparable with 2.0.0 ones;
reproduce the paper from [`v1.0-paper`](#100---2026-09-08).

### Added

- **OpenBanking**: an app for business deposit accounts and a credit card. It
  has a ledger with filters, search and "See more activity", account and card
  numbers that stay masked until clicked, and a statement cycle. It comes with
  its own task set (#85).
- **Card payments in the shop.** Checkout charges an OpenBanking card and
  refuses expired cards, wrong card details and charges over the credit limit
  (beyond a small grace amount). Cross-app purchase tasks score both apps
  (#86).
- **OpenUI** (`/uilibrary`), a gallery of the shared component library whose
  state can be scored. It is off by default (#83).
- **Themes**: one shared design-token theme for all apps (`apps/theme=`), with
  a per-app override (`apps.<app>.theme=`). Ships `default`, `dark`, `mono`,
  `solarized`, `material`, `bootstrap`, `colorblind`, `challenging_font`,
  `vscode_dark`, `meta` and `meta_dark` (#72).
- **Layouts** per app, varied independently of the theme: kanban todo, agenda-
  or sidebar-first calendar, split or compact messenger, left or bottom map
  panel, right-hand or top file tree, card-list ledger, grid or table shop
  (#72, #82).
- **Desktop shell** start page: toolbar, clock, weather, a light/dark toggle,
  a launcher menu and pinnable apps over a generated wallpaper. On a phone it
  becomes a home screen (#83, #85).
- **Devices** (`device=desktop|laptop|tablet|phone`): viewport, input model
  and composition per form factor (#83, #85).
- **Window chrome** on every page: a title bar with minimise, maximise and
  close, a dock of pinned apps, and an animated agent cursor
  (`apps/chrome=default|windows|none`). Also episode video recording
  (`record_video=True`) (#85).
- **Abstention**: an opt-in agent config that can ask the user
  (`send_msg_to_user`) or decline (`report_infeasible`), and an
  `AbstentionTask` that scores whether the agent acted, asked or stopped
  (#91).
- **Dev server** with hot reloading for app development (#76).
- **Realism sweep** (#98):
  - Banking: icon-and-text Copy buttons for account, routing and card numbers.
  - Code editor: offline syntax highlighting that follows the file's language
    and can be switched from the Language picker.
  - App icons: a new set of Black Tie–style SVG icons in colour and greyscale,
    plus bare versions of the same glyphs in the launcher menu.
  - Shop: two past orders seeded by default.

### Changed

- **Online shop rewritten in Python** on SQLite full-text search. It needs no
  JDK and no dataset download, is on by default, and ships a 999-product
  catalog (#78).
- The **start page defaults to the desktop shell**; the 1.0.0 tile grid stays
  available as `apps/start_page/layout=gallery` (#85).
- The per-app `appearance` group is replaced by `theme` and `layout` (#72).
- **Desktop icons removed**: pinned apps live in the dock, and the pinned-app
  UI questions now ask about the dock (#98).
- **Maps** dark and mono themes use Esri's gray canvases (#98).
- Hydra configs **ship inside the wheel**, so OpenApps works as an installed
  dependency (#96).
- `wait()` sleeps 3 seconds and is no longer rejected as an invalid action
  (#91).

### Fixed

- Opening an account in OpenBanking no longer drops the title bar and dock
  (#98).
- Static files are served only under `/assets/`. Code-editor files ending in
  `.yaml`, `.js`, `.css` or `.txt` used to open blank and fail to save (#98).
- Dark mode in the desktop shell: icon buttons and the weather chip no longer
  render black on the dark toolbar (#98).
- The agent cursor follows `cursor.form_factors` again, so phones show no
  cursor (#97, #98).
- Parallel runs: each `conduct.sh` run gets its own port window, logs and
  databases (#88). Command-line overrides reach every parallel run (#90).
  Cleanup stops the web app's whole process group (#89).
- The preview window fits the screen, and the agent cursor is hidden from
  people browsing and on phones (#87).

### Removed

- CartoDB basemaps (#98).
- The `appearance` config group, replaced by `theme` and `layout` (#72).
- The Java 21 and dataset prerequisites for the online shop (#78).

### Docs

- Environment variables and the `internal-*` SLURM configs are documented in
  the README (#77). `v1.0-paper` is pinned as the reproducibility tag (#79).

## [1.0.0] - 2026-09-08

The release behind *OpenApps: Simulating Environment Variations to Measure
UI-Agent Reliability* ([arXiv:2511.20766](https://arxiv.org/abs/2511.20766)).
Tagged `v1.0-paper`. Check it out to reproduce the paper's results.

### Added

- Seven apps (todo, calendar, messenger, maps, code editor, online shop and
  the start page), each rewarding from its own state.
- Variation axes: per-app appearance and content (German, Mandarin, long,
  misleading and adversarial descriptions), adversarial pop-ups, and goal
  phrasings.
- Tasks: the original set, user-goal variations, navigation tasks and the
  longer-horizon composite set (#4, #8, #18, #42, #54).
- Agent harness on BrowserGym and AgentLab, with OpenAI, Azure, Bedrock,
  Gemini and self-hosted vLLM models. Includes computer-use agents, Qwen3.6-VL
  and UI-TARS adapters, and per-model coordinate spaces (#14, #64, #71).
- Parallel sweeps, locally and on SLURM through submitit, with W&B tracking
  (#13, #17, #66).
- OpenApps as an MCP server (#19).
- Approximate (10 km) coordinate matching for map rewards (#22).
- Variation screenshot and set-of-marks utilities (#15, #16).

[Unreleased]: https://github.com/facebookresearch/OpenApps/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/facebookresearch/OpenApps/compare/v1.0-paper...v2.0.0
[1.0.0]: https://github.com/facebookresearch/OpenApps/releases/tag/v1.0-paper
