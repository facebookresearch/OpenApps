# Files

Files is a Finder-style browser over the shared file store
(`open_apps.files_store`). The bank writes statements into it, the shop writes
receipts into it, and its **Code** folder *is* the code editor's workspace, not
a copy. It is on by default:

```bash
uv run launch.py                                  # then open /files
uv run launch.py apps.start_page.apps.files.enabled=False   # hide the start-page tile
```

Every page is a full-page GET and every action is a real link or a form post
answered with a redirect, so the window chrome is drawn on each page an agent
lands on. htmx only enhances: dialogs open in place, and a form posted through
htmx gets an `HX-Redirect` back to the same full page a plain post would reach.
PDFs are drawn by the vendored pdf.js (`/assets/js/files_viewer.js`), whose text
layer puts a document's words in the DOM and the accessibility tree.

## Varying Files

It composes the same way as every other app:

```bash
uv run launch.py apps/files/layout=icons            # default | icons | folder_tabs
uv run launch.py apps/files/content=german          # or mandarin, long_descriptions,
                                                    #    misleading_descriptions, adversarial_descriptions
uv run launch.py apps/theme=dark                    # every app; or apps.files.theme=dark for Files alone
uv run launch.py apps/theme=challenging_font
uv run launch.py device=phone                       # desktop | laptop | tablet | phone
```

### Overrides

| Override | Values | Effect |
| --- | --- | --- |
| `apps/theme=<name>` | any file in `config/apps/theme/` (`default`, `dark`, `challenging_font`, `solarized`, `mono`, ...) | Global design tokens. Files paints `<html>`/`<body>` from `--color-bg` and bridges Pico's own variables (headings, form fields, dialogs, `<code>`, font stack) onto the tokens, so `dark` leaves no white canvas and the `challenging_font` script face reaches every control. The theme's `tone` also sets Pico's `data-theme` polarity. |
| `apps.files.theme=<name>` | as above | Per-app pin; beats the global selection. Default `null`. |
| `apps/files/layout=<name>` | `default`, `icons`, `folder_tabs` | Structure only; see below. |
| `apps/files/content=<name>` | `default`, `german`, `mandarin`, `long_descriptions`, `misleading_descriptions`, `adversarial_descriptions` | Text. See [Content packs](#content-packs). |
| `device=<name>` | `desktop`, `laptop`, `tablet`, `phone` | `phone` collapses the window to one pane: no sidebar, no preview, and a click on a file opens it (as iOS Files does). The Files root doubles as the folder list there. |
| `apps.files.root` | path, default `${databases_dir}/files` | Where the store lives for this run. |
| `apps.files.today` | ISO date, default `${apps.openbanking.as_of_date}` | The app's "today": Recently Deleted counts its "N days left" from it, and a delete is stamped with it. Pinned so the counts, and the state a delete writes, don't depend on the wall clock. |

### Layouts

All three render the same ids (`files-sidebar`, `files-toolbar`,
`files-content`, `files-preview`, `files-dialog`), the same links and the same
routes; only the arrangement changes.

| Layout | What it looks like |
| --- | --- |
| `default` | Finder's list view: a folder sidebar on the left (Documents, Downloads, Receipts, Statements, Code, then Recently Deleted with item counts), a toolbar with breadcrumb, search, List/Grid toggle, sort menu and Upload, the file table in the middle (Name, Date modified, Size, Kind, with resizable columns), and a Details pane on the right that fills in when a file is selected. |
| `icons` | The same window, but folders open as a grid of thumbnails (PDF first pages drawn by pdf.js) rather than a table -- Finder's icon view. The List/Grid toggle still switches either way per page. |
| `folder_tabs` | No side column: the folders and Recently Deleted run as a row of tabs across the top of the window, above the toolbar, and the table and Details pane take the full width underneath. The sidebar is still first in the DOM, so the accessibility tree reads top to bottom as drawn. |

### Content packs

The app's own labels -- folder display names, column headers, every button,
dialog, error and viewer string, file kinds, the bytes unit and the date
format -- live in the pack: flat keys (`trash_label`, `folder_labels`,
`columns`, ...) plus the nested `ui:` block. `german` and `mandarin` translate
all of them and set `content_language`, which also tags the page
`<html lang="de">` / `<html lang="zh-Hans">`.

The seeded **files are not translated**: like the bank's account names and
figures, their folders, names and bytes are what tasks address and score, so
they stay identical in every pack (`tests/test_files.py` checks it). Folder
*display* names change (`Documents` shows as `Dokumente`), but paths and URLs
stay `/files/Documents`.

`adversarial_descriptions` plants `apps.adversarial_message` inside a text
document; `long_descriptions` adds more documents to read past and
`misleading_descriptions` adds decoys that look like the files tasks ask
about (a records-request form, a lease *renewal offer*). Neither changes any
task's answer. Documents are keyed by a stable id, so a pack
adds one with a new key or drops a default one with `key: null`.

## Tasks

Files tasks live in `config/tasks/files.yaml`, deliberately outside
`all_tasks` (the paper's canonical inventory), so select them with
`tasks=files`:

| Task | What the agent must do |
| --- | --- |
| `delete_packing_list` | Delete `Documents/Packing List.txt` (to Recently Deleted -- emptying the bin afterwards fails it) |
| `restore_w2_draft` | Restore `W-2 2025 (draft).pdf` from Recently Deleted (2 days left) |
| `restore_lab_results_scan` | Restore `Lab Results 2026-08-12 (scan).pdf` |
| `purge_duplicate_lab_scan` | Permanently delete that scan from Recently Deleted (needs the confirm dialog) |
| `rename_lease` | Rename the lease PDF in Documents to an exact new name |
| `move_insurance_card_to_downloads` | Move the insurance card PDF from Documents to Downloads |
| `download_july_savings_statement` | In OpenBanking, download July 2026 for the savings account (lands in `Statements/`) |
| `file_july_savings_statement_in_documents` | Cross-app: download it, then move it into Documents in Files |
| `label_printer_price` | Answer from a shop receipt in `Receipts/` (scored on the reply) |

Each has three rephrasings, `<key>__casual_1`, `__formal_1` and
`__unrelated_context_1`. Run one with the dummy agent to check the plumbing,
or with a real agent:

```bash
uv run launch_agent.py agent=dummy tasks=files task_name=restore_w2_draft
uv run launch_agent.py agent=dummy tasks=files task_name=rename_lease \
    apps/files/layout=folder_tabs apps/files/content=german apps/theme=dark
```

Task state is compared exactly: a path is an address, so `Lease-2026.pdf` is
not `Lease 2026.pdf`. Because files and paths are identical across packs, every
task stays winnable under `german` and `mandarin`.

## Inspecting the data

The store is a plain folder tree under the run's databases directory, with a
hidden `.trash/` folder (plus one JSON per item in `.trash/.meta/`) for
Recently Deleted:

```bash
ROOT=$(ls -td log_outputs/*/databases/files | head -1)
find "$ROOT" -type f | sort
```

The scoring surface is `/files_all`: one row per live file, empty folder and
trashed item -- `path`, `kind`, `size`, `sha256`, `deleted`, `deleted_on` --
sorted, without modification times, and without the Code folder (scored
through `/codeeditor_all`). With the app running, fetch it from the address
`launch.py` prints:

```bash
curl -s http://localhost:<port>/files_all | python -m json.tool | head -40
```
