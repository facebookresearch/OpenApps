# OpenTodos

A to-do list: add a task, tick it off, edit it, remove it. It is on by default
and lives at `/todo`:

```bash
uv run launch.py                                   # every app; the list at /todo
uv run src/open_apps/apps/todo_app/main.py         # the todo app on its own (standalone mode)
```

Its state is one SQLite table of `(id, title, done)` rows, seeded from the
content pack on every launch and every reset, and served as JSON at
`/todo_all`. Every todo task is scored on that endpoint.

## Varying the app

The app composes the same way as every other app:

```bash
uv run launch.py apps/todo/layout=grouped_sections     # default | kanban_board | grouped_sections
uv run launch.py apps/todo/content=german              # or mandarin, long_descriptions, adversarial_descriptions, misleading_descriptions
uv run launch.py apps/theme=dark                       # every app; or apps.todo.theme=dark for just this one
uv run launch.py apps/theme=challenging_font
uv run launch.py device=phone                          # or tablet, laptop
```

| Override | Values | What it changes |
| --- | --- | --- |
| `apps/theme=` | `default`, `dark`, `challenging_font`, `solarized`, `mono`, ... | Colours and fonts of every app (`config/apps/theme/`) |
| `apps.todo.theme=` | any theme stem, or `null` (default: inherit) | The theme for this app only |
| `apps/todo/layout=` | `default`, `kanban_board`, `grouped_sections` | Page structure (below) |
| `apps/todo/content=` | `default`, `german`, `mandarin`, `long_descriptions`, `adversarial_descriptions`, `misleading_descriptions` | The seeded todos, and the UI language |
| `device=` | `desktop`, `laptop`, `tablet`, `phone` | Viewport, touch and user agent |
| `apps.todo.database_path=` | path (default `${databases_dir}/todo.db`) | Where the SQLite file is written |
| `apps.todo.init_todos=` | list of `[title, done]` | The seeded todos, overriding the pack |

## Layouts

A layout changes structure only. The routes (`/todo`, `/todo/toggle/{id}`,
`/todo/edit/{id}`, `/todo/todos/{id}`), the row ids (`todo-<id>`) and
`/todo_all` are the same in every layout, so every todo task scores the same
way whichever one is in play.

| Layout | What it looks like |
| --- | --- |
| `default` | One centred card: the "New Todo" field and **Add** on top, then one row per todo -- checkbox and title, with **Edit** and **Remove** beneath the title. Ticking a box restyles the row in place; the row never moves. A full-size "Return to List of Apps" button sits under the card. |
| `kanban_board` | Four columns of cards (To Do, In Progress, Review, Done), each with a count, its own "Add task" field and click-to-rename heading. A card carries **Mark Done** / **Reopen**, **Edit** and **Remove**, but no checkbox. Marking a card done moves it to the Done column. |
| `grouped_sections` | The add field on top, then two headed sections, **Open** and **Completed**, each with a count. One line per todo: checkbox, title, and **Edit** / **Remove** at the right edge. Ticking a box moves the row to the other section, so an item's position and neighbours change with its state. |

`kanban_board` and `grouped_sections` demote the back-to-apps button to a
quiet link (same text, `href` and `role`); only `default` keeps the
prominent button the navigation tasks were written against.

## Languages

`apps/todo/content=german` and `apps/todo/content=mandarin` translate the
whole app:

* **The UI.** Every label is a flat `*_label` / `*_placeholder` key in the
  content pack (`edit_label`, `remove_label`, `kanban_review_label`,
  `open_section_label`, ...), English in `content/default.yaml` and overridden
  by `german.yaml` / `mandarin.yaml`. The back-to-apps link uses the shared
  string `i18n.t("return_to_apps")`. The pack's `content_language` sets
  `<html lang>` (`de`, `zh-Hans`).
* **The data.** Every seeded todo is translated, in the default pack's order
  and with its done flags, so a task starts from the same state in every
  language.

Task goals stay in English. `Mark 'Visit grandma' as done` under the german
pack means the row titled "Oma besuchen": each translated pack carries a
`todo_translations` map from the English title to the rendered one, and
`MarkToDoDoneTask` / `DeleteToDoTask` resolve the goal's name through it. The
agent has to make the cross-language match; the task does not get easier or
harder to score. `AddToDoTask` adds the title the goal quotes, verbatim.

```yaml
# config/apps/todo/content/german.yaml (excerpt)
content_language: german
edit_label: "Bearbeiten"
init_todos:
  - ["Oma besuchen", false]
todo_translations:
  "Visit grandma": "Oma besuchen"
```

Adding a todo to a pack means adding it to `init_todos` in all three
language packs, at the same position and with the same done flag, and to both
`todo_translations` maps; `tests/test_todo_i18n_layouts.py` checks all of it.

## Tasks

The todo tasks live in `config/tasks/original_tasks.yaml` (in the default
`all_tasks` set), `user_goal_variations.yaml` (rephrasings of the same
tasks), `longer_horizon.yaml` and, for the ones that read a
figure out of the bank first, `openbanking.yaml`. Three classes score them:

| Class | Target state |
| --- | --- |
| `AddToDoTask(todo_name, is_done)` | the initial todos plus `{todo_name, is_done}` |
| `MarkToDoDoneTask(todo_name)` | the named todo with `done` set |
| `DeleteToDoTask(todo_name)` | the named todo removed |

Everything else in the cross-app state has to be unchanged, so a spurious
edit elsewhere fails the task.

```bash
uv run launch_agent.py agent=dummy task_name=mark_water_plants_as_done
uv run launch_agent.py agent=dummy task_name=delete_call_aunt_lisa apps/todo/layout=grouped_sections
uv run launch_agent.py agent=dummy task_name=add_call_mom_to_my_todo apps/todo/content=german apps/theme=dark
uv run launch_agent.py agent=dummy tasks=longer_horizon task_name=<key>   # a task outside all_tasks
```

## Inspecting the data

```bash
curl -s localhost:5001/todo_all | python -m json.tool     # what rewards read (the port launch.py prints; 5001 unless taken)

DB=$(ls -t log_outputs/*/databases/todo.db | head -1)     # the newest run
sqlite3 -header -column "$DB" "SELECT * FROM todo;"
```

`done` is stored as `0`/`1`; the comparison treats it as a boolean. The kanban
column a card sits in is kept in memory, not in the table, so it is not part
of the state a task is scored on.
