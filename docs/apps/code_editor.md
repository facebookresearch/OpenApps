# Code Editor

The code editor ("OpenCodeEditor" on the start page) is a VS Code-style editor
over a real folder on disk: an Explorer with New File / New Folder, a tab
strip, an editor pane with Save / Delete / Reveal in Files, Language and Theme
pickers, and a status bar. The files themselves are the state tasks score. It
is on by default:

```bash
uv run launch.py            # then open http://localhost:<port>/codeeditor
```

The workspace is the folder at `apps.code_editor.database_path`, written from
the content pack's `filesystem` tree at launch. The Files app shows the same
folder as its `Code` folder, not a copy, which is what "Reveal in Files"
opens.

The default page needs no network: Pico, htmx and the icon font are vendored,
and the syntax colours come from an offline highlighter
(`syntax_highlight`, `assets/js/code_highlight.js`) under the plain textarea.
Tailwind / daisyUI are still requested from their CDNs, and fail silently
without egress; `apps.code_editor.no_egress=true` drops them so the page is the
same everywhere.

## Varying the editor

It composes the same way as every other app:

```bash
uv run launch.py apps/theme=dark                          # any file in config/apps/theme/
uv run launch.py apps/theme=challenging_font
uv run launch.py apps.code_editor.theme=solarized         # pin this app only (beats apps/theme)
uv run launch.py apps/code_editor/layout=sidebar_right    # default | sidebar_right | top_tree
uv run launch.py apps/code_editor/content=german          # default | german | mandarin | long_descriptions | adversarial_descriptions | misleading_descriptions
uv run launch.py device=phone                             # the shared device axis
```

| Override | Default | Effect |
| --- | --- | --- |
| `apps/theme=<name>` | `null` | Global theme. With nothing selected the editor falls back to its own `theme_default` |
| `apps.code_editor.theme` | `null` | Per-app pin; also what the in-page Theme picker writes |
| `apps.code_editor.theme_default` | `vscode_dark` | The editor's look when no theme is selected anywhere |
| `apps/code_editor/layout` | `default` | See [Layouts](#layouts) |
| `apps/code_editor/content` | `default` | Text and files, see [Content packs](#content-packs) |
| `apps.code_editor.list_of_themes` | every theme in `config/apps/theme/` | What the Theme picker offers; `[]` means "every theme file", unknown names are dropped |
| `apps.code_editor.mode` | `python` | Language for a file whose extension names none |
| `apps.code_editor.list_of_modes` | python, javascript, css, yaml, markdown, shell, clike, go | Language picker options |
| `apps.code_editor.sort_feature` | `True` | Sort the pickers alphabetically |
| `apps.code_editor.syntax_highlight` | `True` | Offline highlight layer under the textarea (colours are the theme's `color-syntax-*` tokens) |
| `apps.code_editor.highlight` | `False` | Use CodeMirror from its CDN instead -- replaces the textarea with CodeMirror's DOM |
| `apps.code_editor.editor_theme` / `editor_theme_by_tone` | `eclipse` / `dark: monokai, mono: eclipse` | CodeMirror stylesheet, picked through the theme's `tone` (only with `highlight=True`) |
| `apps.code_editor.no_egress` | `False` | Drop every third-party stylesheet and script |
| `apps.code_editor.database_path` | `${databases_dir}/codeeditor` | The workspace folder |

### Themes

Every colour and font is a design token, so `apps/theme=` repaints the
chrome, the code pane and the syntax colours together. The app title and
form controls come from Pico, whose variables are bridged onto the tokens, so
headings stay readable on dark themes. `challenging_font` changes the UI face
only; the code stays monospace.

The Theme picker switches live, with no reload and no network: every offered
theme's tokens are embedded in the page, and the swap also moves CodeMirror
(when `highlight=True`) to the stylesheet for that theme's tone. The choice is
posted to `/codeeditor/update_config`, so it survives navigation and shows up
in the recorded config.

### Layouts

Routes, ids and the `/codeeditor_all` tree are identical in every layout.

| Layout | Looks like |
| --- | --- |
| `default` | Explorer on the left, editor on the right; Language / Theme pickers top-right of the editor; Code Editor Index Page and Return to List of Apps as full buttons under the code (the navigation tasks rely on finding them) |
| `sidebar_right` | Explorer to the right of the editor (a real DOM reorder); pickers and the two navigation links move to the status bar, as in VS Code |
| `top_tree` | No side column: the Explorer is a one-row file bar above the editor (expanded folders join the row); pickers and navigation in the status bar |

### Content packs

| Pack | Changes |
| --- | --- |
| `default` | English UI; `basic_imports.py`, `untitled_c.c`, `script.py`, `developing/...`, `empty_folder` |
| `german` | German UI, `<html lang="de">`; the same files with German comments |
| `mandarin` | Mandarin UI, `<html lang="zh-Hans">`; the same files with Chinese comments |
| `long_descriptions` / `misleading_descriptions` | A long or misleading welcome message |
| `adversarial_descriptions` | The adversarial message as the welcome text and in every file's first comment |

File and folder names and the code itself never change between packs: tasks
address files by path. A pack's `+filesystem` is appended to the default tree
by the launcher (`open_apps.utils.merge_plus_keys`), and since the tree is
written to disk in order, the pack's copy of each file replaces the default
one.

The UI strings are flat `*_label` keys (`explorer_label`, `new_file_label`,
`no_file_selected_label`, ...; the English defaults and the full list are
`LABELS` in `src/open_apps/apps/codeeditor_app/main.py`). Return to List of
Apps, Save, Delete, Cancel and Close come from `open_apps.i18n`. English
renders exactly as before: a UI question asks for the empty editor's "No file
selected" header, and the navigation tasks look for "Return to List of Apps".

## Tasks

| Task | Goal | Scored on |
| --- | --- | --- |
| `edit_script_add_header_comment` | Add `# Reviewed by Bob` to `script.py` and save | `script.py` contains the fragment (`EditFileTask`) -- the same under every pack |
| `create_notes_file_in_code_editor` | Create `notes.txt` containing `TODO: refactor` | That file with that content |
| `navigate_from_codeeditor_to_todo` (and its `__casual_*` / `__formal_*` variants) | Leave through Return to List of Apps and open the todo app | The page URL |

```bash
uv run launch_agent.py agent=dummy task_name=edit_script_add_header_comment
uv run launch_agent.py agent=dummy task_name=edit_script_add_header_comment apps/theme=dark apps/code_editor/content=german apps/code_editor/layout=top_tree
```

## Inspecting the data

The scoring surface is `/codeeditor_all`, the whole tree as JSON with each
file's content:

```bash
curl -s http://localhost:<port>/codeeditor_all | python -m json.tool | head -30
```

`open_apps.state.get_current_state(url)["codeeditor"]` returns the same tree.
Or look at the folder itself:

```bash
find "$(ls -td log_outputs/*/databases/codeeditor | head -1)" -type f
```
