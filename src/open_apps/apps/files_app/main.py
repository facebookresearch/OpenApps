"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The Files app: a browser over the shared file store.

The store (:mod:`open_apps.files_store`) is the state; this module seeds it
and serves it. Seeding copies every document in the content pack into the
store on each launch and reset -- a committed file from ``seed/``, or a text
body from the pack -- so the store always starts from the same files with the
same checksums. ``/files_all`` carries those checksums, and a store that
drifted between launches would hand every task a spurious diff.

The store is configured here, before any other app needs it: the start page
sets apps up in ``AVAILABLE_APPS`` order and registers the shop last, so the
bank and shop find it ready when they write statements and receipts into it.

``/files_all`` is the scoring surface: :func:`files_store.snapshot`, sorted,
without modification times, and without the Code folder (that is the code
editor's workspace, scored through ``/codeeditor_all``).

The pages follow the lesson of the banking fix: every navigation and every
action is a real link or a form post answered with a redirect, so each page an
agent lands on is a full-page GET and the window chrome is drawn on it. htmx
only enhances -- dialogs open in place, and a form posted through htmx gets an
``HX-Redirect`` back to the same full page a plain post would reach.

PDFs are drawn by the vendored pdf.js (``/assets/js/files_viewer.js``), whose
text layer puts the document's words in the DOM and the accessibility tree.
The browser's own viewer can't do that for an agent: headless Chromium turns
a PDF navigation into a download. "Open in new tab" still serves the raw file
for people and headed runs.
"""
import json
import re
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import quote, urlsplit

from fasthtml.common import *

from open_apps import device, files_store
from open_apps.apps.files_app.styles import styles
from open_apps.apps.start_page.helper import create_logo_header
from open_apps.frontend import local_hdrs
from open_apps.icons import Icon, icon
from open_apps.theme import theme_asset, theme_style

app, rt = fast_app(default_hdrs=False, hdrs=local_hdrs())

logo_title_container = None

VIEWER_SCRIPT_URL = "/assets/js/files_viewer.js"
BROWSER_SCRIPT_URL = "/assets/js/files_browser.js"
HIGHLIGHT_SCRIPT_URL = "/assets/js/code_highlight.js"
TRASH_URL = "/files/recently-deleted"

# The seeded documents' bytes, named by the content packs' `source` keys.
SEED_DIR = Path(__file__).parent / "seed"


def _plain(node):
    """An OmegaConf node (or None) as plain Python containers."""
    from omegaconf import OmegaConf

    return OmegaConf.to_container(node, resolve=True) if OmegaConf.is_config(node) else node


def set_environment(config):
    """Point the store at this run's root and re-seed it from the config.

    Called again on reset, so the store is emptied first; the code editor's
    workspace is left alone (``files_store.reset`` never touches it).
    """
    global logo_title_container
    app.config = config
    files_store.configure(config.files.root, config.code_editor.database_path, config.files.today)
    files_store.reset()
    seed(_plain(config.files.get("documents")) or {}, str(config.files.today))
    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.files,
        base_url="/files",
        current_file_path=__file__,
    )


def seed(specs: dict, today: Optional[str] = None) -> None:
    """Store every document, then move the pre-deleted ones to Recently
    Deleted with their own ``deleted_on``.

    In key order, which is deterministic (the pack's order, then each pack's
    additions), though nothing depends on it: no two specs share a path, and
    a duplicate fails the launch rather than seeding a silent " (2)".

    Each file's modification time is pinned to its spec's ``modified`` date
    (else the app's today), so "Date modified" and the date sort read the
    same on every launch. Display only: ``/files_all`` carries no times.
    """
    seen: set[str] = set()
    for key, spec in specs.items():
        if not spec:
            continue  # a pack can drop a default document with `key: null`
        path = f"{spec['folder']}/{spec['name']}"
        if path in seen:
            raise ValueError(f"files seed: {key!r} repeats {path!r}")
        seen.add(path)
        stored = files_store.save(spec["folder"], spec["name"], _seed_bytes(key, spec))
        _pin_mtime(stored, spec.get("modified") or today)
        if spec.get("deleted_on"):
            files_store.delete(stored, deleted_on=str(spec["deleted_on"]))


def _seed_bytes(key: str, spec: dict) -> bytes:
    """A document's contents: its ``source`` file, else its text ``body``."""
    if ("source" in spec) == ("body" in spec):
        raise ValueError(f"files seed: {key!r} needs exactly one of `source` or `body`")
    if "body" in spec:
        return str(spec["body"]).encode("utf-8")
    return (SEED_DIR / spec["source"]).read_bytes()


def _pin_mtime(path: str, day) -> None:
    files_store.pin_modified(path, day)


def cfg():
    return app.config.files


def ui():
    return cfg().ui


def current_layout() -> str:
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return str(getattr(config.files, "layout", "default"))


def is_phone() -> bool:
    return device.form_factor(app.config) == "phone"


def pico_theme() -> str:
    """Pico's palette polarity for the active theme (see OpenBanking)."""
    tone = theme_asset(app.config, "files", "tone", "light")
    return "dark" if str(tone) == "dark" else "light"


# ---------------------------------------------------------------------------
# Small helpers.


def href(prefix: str, path: str = "") -> str:
    """``prefix/path`` with the path URL-encoded segment by segment."""
    return f"{prefix}/{quote(path)}" if path else prefix


def folder_url(folder: str, **params) -> str:
    base = href("/files", folder)
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items() if v not in (None, ""))
    return f"{base}?{query}" if query else base


def parent_of(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


def folder_label(folder: str) -> str:
    """The sidebar and breadcrumb name of a folder: translated for the
    built-in ones, the folder's own name for anything a user made."""
    labels = _plain(cfg().folder_labels) or {}
    return labels.get(folder, PurePosixPath(folder).name or ui().root_label)


def kind_label(kind: str) -> str:
    kinds = _plain(ui().kinds) or {}
    return kinds.get(kind, kinds.get("other", kind))


def format_size(n: int) -> str:
    if n < 1000:
        return f"{n} bytes"
    if n < 1000 * 1000:
        return f"{n / 1000:.0f} KB"
    return f"{n / 1000 / 1000:.1f} MB"


def modified_on(path: str) -> Optional[date]:
    try:
        return date.fromtimestamp(files_store.resolve(path).stat().st_mtime)
    except (OSError, ValueError):
        return None


def format_date(day: Optional[date]) -> str:
    if day is None:
        return ""
    return day.strftime(str(ui().date_format)).replace(" 0", " ")


def page_count(data: bytes) -> int:
    """Pages in a PDF, by counting page objects. Exact for the PDFs this repo
    writes; a fair estimate for anything uploaded."""
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data))


def is_pdf(kind: str) -> bool:
    return kind == "application/pdf"


def is_image(kind: str) -> bool:
    return kind.startswith("image/")


def is_text(kind: str) -> bool:
    return kind.startswith("text/") or kind == "application/json"


def entry_glyph(entry, size: int = 18):
    return icon(Icon.FOLDER if entry.is_folder else Icon.FILE, size=size, cls="fs-glyph")


def walk(folder: str = ""):
    """Every live file under ``folder``, depth first."""
    for entry in files_store.list_folder(folder):
        if entry.is_folder:
            yield from walk(entry.path)
        else:
            yield entry


def get_entry(path: str):
    """The live entry at ``path``, or ``None`` (missing, or an escape)."""
    try:
        parent = parent_of(path)
        return next((e for e in files_store.list_folder(parent) if e.path == path), None)
    except (ValueError, FileNotFoundError, NotADirectoryError):
        return None


def folder_exists(folder: str) -> bool:
    try:
        files_store.list_folder(folder)
        return True
    except (ValueError, FileNotFoundError, NotADirectoryError):
        return False


def sorted_entries(entries, sort: str):
    folders = [e for e in entries if e.is_folder]
    files = [e for e in entries if not e.is_folder]
    if sort == "size":
        files.sort(key=lambda e: (-e.size, e.name.casefold()))
    elif sort == "date":
        files.sort(key=lambda e: (-(files_store.resolve(e.path).stat().st_mtime), e.name.casefold()))
    return folders + files


def is_htmx(req) -> bool:
    return bool(req is not None and req.headers.get("HX-Request"))


def go(req, url: str):
    """Answer a successful action: an ``HX-Redirect`` for htmx, else a 303 --
    either way the browser lands on a full page, so the chrome is redrawn."""
    if is_htmx(req):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def page(*content, status: int = 200, viewer: bool = False):
    """The frame every route renders: header, then the window or viewer."""
    factor = device.form_factor(app.config)
    body = Div(
        theme_style(app.config, "files"),
        styles,
        Div(logo_title_container, cls="fs-top"),
        *content,
        Script(src=BROWSER_SCRIPT_URL),
        cls=f"fs-root is-{factor}",
        id="files-app",
        data_theme=pico_theme(),
        data_layout=current_layout(),
    )
    title = Title(cfg().title)
    if status != 200:
        return FtResponse((title, body), status_code=status)
    return title, body


def not_found():
    return page(
        Div(P(ui().not_found, cls="fs-empty"), A(ui().back_to_files, href="/files", cls="fs-btn"), cls="fs-viewer is-plain"),
        status=404,
    )


# ---------------------------------------------------------------------------
# Pieces of the browser.


def sidebar(current: str):
    """Folders, then Recently Deleted. Built-in folders in their fixed order,
    with their item counts; folders a user made follow by name."""
    folders = list(files_store.DEFAULT_FOLDERS) + [files_store.CODE]
    extra = [
        e.path for e in files_store.list_folder("")
        if e.is_folder and e.path not in folders
    ]
    items = []
    for folder in folders + extra:
        count = sum(1 for e in files_store.list_folder(folder)) if folder_exists(folder) else 0
        entry = get_entry(folder)
        items.append(
            Li(
                A(
                    icon(Icon.FOLDER, size=17, cls="fs-glyph"),
                    Span(folder_label(folder)),
                    Span(str(count), cls="fs-side-count", aria_label=ui().items_label.format(n=count)),
                    href=folder_url(folder),
                    cls="fs-side-link",
                    aria_current="page" if current == folder else None,
                ),
                # Right-click opens the folder's menu, as in Finder's sidebar.
                # No visible trigger here; the same menu is on the folder's row.
                menu(entry) if entry is not None else None,
                data_menu_host="1" if entry is not None else None,
            )
        )
    trash_count = len(files_store.list_trash())
    items.append(Li(Div(cls="fs-side-rule"), role="separator"))
    items.append(
        Li(
            A(
                trash_glyph(17),
                Span(cfg().trash_label),
                Span(str(trash_count), cls="fs-side-count", aria_label=ui().items_label.format(n=trash_count)),
                href=TRASH_URL,
                cls="fs-side-link",
                aria_current="page" if current == TRASH_URL else None,
            )
        )
    )
    return Nav(Ul(*items), cls="fs-sidebar", id="files-sidebar", aria_label=ui().folders_label)


def trash_glyph(size: int = 18):
    """A bin. Drawn here rather than added to the shared icon set: only this
    app has a Recently Deleted."""
    return NotStr(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{size}" height="{size}"'
        ' class="fs-glyph" fill="none" stroke="currentColor" stroke-width="1.5"'
        ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
        '<path d="M4.5 7h15"/><path d="M9.5 7V5.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V7"/>'
        '<path d="M6.5 7l.8 11.6a1.5 1.5 0 0 0 1.5 1.4h6.4a1.5 1.5 0 0 0 1.5-1.4L17.5 7"/>'
        '<path d="M10 10.5v6M14 10.5v6"/></svg>'
    )


def breadcrumb(folder: str, *, label: Optional[str] = None):
    """Files > Documents > ...: every step a link but the current one."""
    steps = [Li(A(ui().root_label, href="/files"))] if folder or label else []
    parts = [p for p in folder.split("/") if p] if folder else []
    for i, part in enumerate(parts):
        path = "/".join(parts[: i + 1])
        name = folder_label(path) if i == 0 else part
        is_last = i == len(parts) - 1 and label is None
        steps.append(Li(Span("/", cls="fs-crumb-sep", aria_hidden="true"), A(name, href=folder_url(path), aria_current="page" if is_last else None)))
    if label is not None:
        steps.append(Li(Span("/", cls="fs-crumb-sep", aria_hidden="true") if steps else "", Span(label, aria_current="page")))
    if not steps:
        steps = [Li(Span(ui().root_label, aria_current="page"))]
    return Nav(Ol(*steps), cls="fs-crumbs", id="files-breadcrumb", aria_label=ui().breadcrumb_label)


def toolbar(folder: str, *, view: str, sort: str, q: str = "", label: Optional[str] = None, upload: bool = True):
    u = ui()
    controls = [breadcrumb(folder, label=label)]
    controls.append(
        Form(
            Label(u.search_label, fr="files-search-input", cls="sr-only"),
            Input(type="search", name="q", value=q, placeholder=u.search_placeholder, id="files-search-input"),
            action="/files/search",
            method="get",
            cls="fs-search",
            role="search",
        )
    )
    if label is None:
        controls.append(
            Div(
                A(u.view_list, href=folder_url(folder, view="list", sort=sort if sort != "name" else None), aria_pressed="true" if view == "list" else "false"),
                A(u.view_grid, href=folder_url(folder, view="grid", sort=sort if sort != "name" else None), aria_pressed="true" if view == "grid" else "false"),
                cls="fs-segment",
                role="group",
                aria_label=u.view_label,
            )
        )
        controls.append(sort_menu(folder, view, sort))
    if upload and label is None and not folder.startswith(files_store.CODE):
        controls.append(
            Form(
                Input(type="hidden", name="folder", value=folder or "Downloads"),
                Input(type="file", name="file", id="files-upload-input", onchange="this.form.submit()"),
                Label(u.upload, fr="files-upload-input", cls="fs-btn is-primary", role="button"),
                Noscript(Button(u.upload, type="submit", cls="fs-btn")),
                action="/files/upload",
                method="post",
                enctype="multipart/form-data",
                cls="fs-upload",
            )
        )
    return Div(*controls, cls="fs-toolbar", id="files-toolbar")


def sort_menu(folder: str, view: str, sort: str):
    u = ui()
    sorts = _plain(u.sort) or {}
    items = [
        Li(A(name, href=folder_url(folder, view=view if view != default_view() else None, sort=key if key != "name" else None),
             aria_current="true" if key == sort else None))
        for key, name in sorts.items()
    ]
    return Details(
        Summary(u.sort_label.format(by=sorts.get(sort, sort)), cls="fs-btn"),
        Ul(*items, cls="fs-menu-list"),
        cls="fs-menu fs-sort",
    )


def default_view() -> str:
    return "grid" if current_layout() == "icons" else "list"


def item_actions(entry, *, for_menu: bool = True):
    """Open, Get Info, Rename..., Move to..., Duplicate, Delete -- one list,
    drawn as the row's menu and as the preview's buttons, so both always agree."""
    u = ui()
    path = entry.path
    back = folder_url(parent_of(path))
    open_href = folder_url(path) if entry.is_folder else href("/files/view", path)
    dialog = lambda route: {  # noqa: E731 - three uses, same shape
        "href": f"{route}?path={quote(path)}",
        "hx_get": f"{route}?path={quote(path)}",
        "hx_target": "#files-dialog",
        "hx_swap": "innerHTML",
    }
    cls = "" if for_menu else "fs-btn"
    pieces = [
        A(u.open, href=open_href, cls=("fs-btn is-primary" if not for_menu else "")),
    ]
    if not for_menu and not entry.is_folder:
        pieces.append(A(u.download, href=href("/files/raw", path) + "?download=1", cls=cls))
    if for_menu:
        # The preview pane already is the details; the menu needs a way to them.
        pieces.append(A(u.get_info, **dialog("/files/info")))
    # The built-in folders are fixed places, like Finder's sidebar: tasks
    # address them by path, so they can't be renamed, moved or deleted. A
    # copy is an ordinary folder -- except of Code, the code editor's
    # workspace, which lives outside the store and would be copied out of it.
    if path in files_store.DEFAULT_FOLDERS:
        pieces.append(Form(Input(type="hidden", name="path", value=path), Button(u.duplicate, type="submit", cls=cls),
                           action="/files/duplicate", method="post"))
    if path not in (files_store.CODE, *files_store.DEFAULT_FOLDERS):
        pieces += [
            A(u.rename, cls=cls, **dialog("/files/rename")),
            A(u.move, cls=cls, **dialog("/files/move")),
            Form(Input(type="hidden", name="path", value=path), Button(u.duplicate, type="submit", cls=cls), action="/files/duplicate", method="post"),
            Form(Input(type="hidden", name="path", value=path), Input(type="hidden", name="back", value=back),
                 Button(u.delete, type="submit", cls=(cls + " is-danger").strip()), action="/files/delete", method="post"),
        ]
    if for_menu:
        return Ul(*[Li(p) for p in pieces], cls="fs-menu-list")
    return Div(*pieces, cls="fs-actions")


def menu(entry):
    return Details(
        Summary(NotStr("&#8943;"), aria_label=ui().more_actions.format(name=entry.name)),
        item_actions(entry),
        cls="fs-menu",
    )


def item_href(entry, folder: str, *, opens: bool = False) -> str:
    """Where clicking an item goes: into a folder; on a phone straight into
    the file (as iOS Files does); elsewhere it selects, and the preview pane
    offers Open, as Finder does. Double-click opens either way.

    ``opens`` is for views with no preview pane to select into -- search
    results, whose files live in other folders: selecting one there pointed
    at a name the current folder doesn't have, so the click went nowhere."""
    if entry.is_folder:
        return folder_url(entry.path)
    if opens or is_phone():
        return href("/files/view", entry.path)
    return folder_url(folder, selected=entry.name)


def thumb(entry, *, large: bool = False):
    cls = "fs-thumb is-large" if large else "fs-thumb"
    if is_pdf(entry.kind):
        return Div(icon(Icon.FILE, size=40), cls=cls, data_pdf_thumb=href("/files/raw", entry.path), aria_hidden="true")
    if is_image(entry.kind):
        return Div(Img(src=href("/files/raw", entry.path), alt="", loading="lazy"), cls=cls, aria_hidden="true")
    return Div(entry_glyph(entry, size=44 if large else 40), cls=cls, aria_hidden="true")


def list_view(entries, folder: str, selected: str, *, show_where: bool = False):
    u = ui()
    cols = cfg().columns
    rows = []
    for e in entries:
        is_sel = (not e.is_folder) and e.name == selected
        rows.append(
            Tr(
                Td(A(entry_glyph(e), Span(e.name), href=item_href(e, folder, opens=show_where), cls="fs-name",
                     aria_current="true" if is_sel else None)),
                Td(folder_label(parent_of(e.path)) or u.root_label, cls="is-muted") if show_where else None,
                Td(format_date(modified_on(e.path)) if not e.is_folder else "", cls="is-muted is-date"),
                Td(format_size(e.size) if not e.is_folder else "", cls="is-num is-muted"),
                Td(kind_label(e.kind), cls="is-muted is-kind"),
                Td(menu(e), cls="is-menu"),
                cls="fs-row" + (" is-folder" if e.is_folder else ""),
                aria_selected="true" if is_sel else None,
                data_menu_host="1",
                data_open=None if e.is_folder else href("/files/view", e.path),
                data_size=None if e.is_folder else str(e.size),
            )
        )
    # Columns resize like Finder's: drag the divider at a header's right
    # edge, double-click it to fit the column to its contents (Name's divider
    # fits every other column instead, so Name gets the most room). Widths
    # live in the browser only -- they never reach app state or a reward.
    columns = [("name", cols.name, "")] + ([("where", u.where_label, "")] if show_where else []) + [
        ("date", cols.date, "is-date"), ("size", cols.size, "is-num"), ("kind", cols.kind, "is-kind"),
    ]
    head = Tr(
        *[
            Th(
                Span(label, cls="fs-col-label"),
                Span(cls="fs-col-resize", role="separator", aria_orientation="vertical", tabindex="0",
                     aria_label=u.resize_column.format(column=label), data_col=key)
                if i < len(columns) - 1 else None,
                cls=cls or None,
                data_col_key=key,
            )
            for i, (key, label, cls) in enumerate(columns)
        ],
        Th(Span(u.actions_label, cls="sr-only"), cls="is-menu"),
    )
    colgroup = Colgroup(*[Col(cls=f"is-col-{key}") for key, _, _ in columns], Col(cls="is-col-menu"))
    return Table(colgroup, Thead(head), Tbody(*rows), cls="fs-table",
                 data_cols=",".join(key for key, _, _ in columns),
                 data_columns_key="search" if show_where else "folder")


def grid_view(entries, folder: str, selected: str):
    tiles = []
    for e in entries:
        is_sel = (not e.is_folder) and e.name == selected
        tiles.append(
            Li(
                A(
                    thumb(e),
                    Span(e.name, cls="fs-tile-name"),
                    Span(format_size(e.size) if not e.is_folder else kind_label(e.kind), cls="fs-tile-meta"),
                    href=item_href(e, folder),
                    aria_current="true" if is_sel else None,
                ),
                menu(e),
                cls="fs-tile",
                aria_selected="true" if is_sel else None,
                data_menu_host="1",
                data_open=None if e.is_folder else href("/files/view", e.path),
                data_size=None if e.is_folder else str(e.size),
            )
        )
    return Ul(*tiles, cls="fs-grid", role="list")


def preview_pane(entry):
    u = ui()
    if entry is None:
        body = [P(u.select_prompt, cls="fs-preview-empty")]
    else:
        facts = [
            Dt(cfg().columns.kind), Dd(kind_label(entry.kind)),
            Dt(cfg().columns.size), Dd(format_size(entry.size)),
            Dt(cfg().columns.date), Dd(format_date(modified_on(entry.path))),
            Dt(u.where_label), Dd(folder_label(parent_of(entry.path))),
        ]
        if is_pdf(entry.kind):
            n = page_count(files_store.read(entry.path))
            facts += [Dt(u.pages_label), Dd(u.page_one if n == 1 else u.pages.format(n=n))]
        body = [
            thumb(entry, large=True),
            H2(entry.name, cls="fs-preview-name"),
            Dl(*facts, cls="fs-facts"),
            item_actions(entry, for_menu=False),
        ]
    return Aside(*body, cls="fs-preview", id="files-preview", aria_label=u.preview_label)


def window(folder_key: str, toolbar_el, content, *, preview=None, show_preview: bool = True):
    """Sidebar | toolbar over content | preview. On a phone, the content alone
    under its toolbar -- folders are reached from the Files root instead."""
    phone = is_phone()
    parts = []
    if not phone:
        parts.append(sidebar(folder_key))
    parts.append(toolbar_el)
    parts.append(content)
    if not phone and show_preview:
        parts.append(preview)
    cls = "fs-window" + ("" if (show_preview and not phone) else " no-preview")
    scripts = [Script(src=VIEWER_SCRIPT_URL, type="module")]
    return Div(*parts, Div(id="files-dialog"), *scripts, cls=cls)


def browser(folder: str, *, view: str, sort: str, selected: str = "", dialog=None):
    entries = sorted_entries(files_store.list_folder(folder), sort)
    if folder == "" and is_phone():
        # The phone's Files root doubles as the sidebar: folders, then the bin.
        trash = files_store.list_trash()
        extra = A(trash_glyph(18), Span(cfg().trash_label), Span(str(len(trash)), cls="fs-side-count"),
                  href=TRASH_URL, cls="fs-side-link")
    else:
        extra = None
    if not entries:
        body = P(cfg().empty_folder_label, cls="fs-empty")
    elif view == "grid":
        body = grid_view(entries, folder, selected)
    else:
        body = list_view(entries, folder, selected)
    content = Section(body, extra, cls="fs-content", id="files-content", aria_label=folder_label(folder))
    chosen = next((e for e in entries if not e.is_folder and e.name == selected), None)
    win = window(folder, toolbar(folder, view=view, sort=sort), content, preview=preview_pane(chosen))
    if dialog is not None:
        win = _with_dialog(win, dialog)
    return win


def _with_dialog(win, dialog):
    """Put a dialog into the window's dialog slot (the no-htmx path)."""
    for i, child in enumerate(win.children):
        if getattr(child, "attrs", {}).get("id") == "files-dialog":
            win.children = (*win.children[:i], Div(dialog, id="files-dialog"), *win.children[i + 1:])
            break
    return win


# ---------------------------------------------------------------------------
# Dialogs. Each is a GET (htmx swaps it into #files-dialog; without htmx the
# same URL renders the whole page with it open) and a POST that does the work.


def dialog_frame(title: str, *body, footer, action: str):
    return Dialog(
        Article(
            Header(H3(title, style="margin:0")),
            Form(
                *body,
                Footer(*footer),
                action=action,
                method="post",
                hx_post=action,
                hx_target="#files-dialog",
                hx_swap="innerHTML",
            ),
        ),
        open=True,
        cls="fs-dialog",
    )


def cancel_button(back: str):
    return A(ui().cancel, href=back, cls="fs-btn", onclick="event.preventDefault(); this.closest('dialog').remove();")


def rename_dialog(entry, value: str = "", error: str = ""):
    u = ui()
    return dialog_frame(
        u.rename_title.format(name=entry.name),
        Input(type="hidden", name="path", value=entry.path),
        Label(u.new_name_label, Input(type="text", name="new_name", value=value or entry.name, required=True, autofocus=True,
                                      aria_invalid="true" if error else None)),
        P(error, cls="fs-error", role="alert") if error else None,
        footer=[cancel_button(folder_url(parent_of(entry.path))), Button(u.rename_submit, type="submit", cls="fs-btn is-primary")],
        action="/files/rename",
    )


def info_dialog(entry):
    """Finder's Get Info, as a read-only sheet: what the item is, where it is,
    how big, and when it last changed."""
    u = ui()
    cols = cfg().columns
    if entry.is_folder:
        children = files_store.list_folder(entry.path)
        count = u.info_item if len(children) == 1 else u.info_items.format(n=len(children))
        size = f"{format_size(sum(e.size for e in walk(entry.path)))} ({count})"
        kind = kind_label("folder")
        # A directory's own mtime is the real clock, not the run's pinned
        # calendar; its newest file says when it last changed in this world.
        modified = max((modified_on(e.path) for e in walk(entry.path)), default=None, key=lambda d: d or date.min)
    else:
        size, kind = format_size(entry.size), kind_label(entry.kind)
        modified = modified_on(entry.path)
    facts = [
        (cols.kind, kind),
        (u.where_label, folder_label(parent_of(entry.path)) or u.root_label),
        (cols.size, size),
    ]
    if modified is not None:
        facts.append((cols.date, format_date(modified)))
    if is_pdf(entry.kind):
        n = page_count(files_store.read(entry.path))
        facts.append((u.pages_label, u.page_one if n == 1 else u.pages.format(n=n)))
    back = folder_url(parent_of(entry.path))
    return Dialog(
        Article(
            Header(H3(u.info_title.format(name=entry.name), style="margin:0")),
            Dl(*[part for label, value in facts for part in (Dt(label), Dd(value))], cls="fs-info"),
            Footer(A(u.done, href=back, cls="fs-btn is-primary",
                     onclick="event.preventDefault(); this.closest('dialog').remove();")),
        ),
        open=True,
        cls="fs-dialog",
    )


def move_dialog(entry, error: str = ""):
    u = ui()
    here = parent_of(entry.path)
    folders = list(files_store.DEFAULT_FOLDERS) + [files_store.CODE] + [
        e.path for e in files_store.list_folder("") if e.is_folder and e.path not in (*files_store.DEFAULT_FOLDERS, files_store.CODE)
    ]
    choices = [
        Li(Label(Input(type="radio", name="folder", value=f, checked=(f == here), disabled=(f == here)),
                 icon(Icon.FOLDER, size=16), folder_label(f), cls="fs-choice"))
        for f in folders
    ]
    return dialog_frame(
        u.move_title.format(name=entry.name),
        Input(type="hidden", name="path", value=entry.path),
        Fieldset(Legend(u.move_to_label, cls="sr-only"), Ul(*choices)),
        P(error, cls="fs-error", role="alert") if error else None,
        footer=[cancel_button(folder_url(here)), Button(u.move_submit, type="submit", cls="fs-btn is-primary")],
        action="/files/move",
    )


def purge_dialog(entry):
    u = ui()
    return dialog_frame(
        u.purge_title.format(name=entry.name),
        P(u.cannot_undo),
        Input(type="hidden", name="trash_name", value=entry.trash_name),
        Input(type="hidden", name="confirm", value="1"),
        footer=[cancel_button(TRASH_URL), Button(u.delete_forever, type="submit", cls="fs-btn is-danger")],
        action="/files/purge",
    )


def empty_dialog(count: int):
    u = ui()
    return dialog_frame(
        u.empty_title,
        P(u.empty_body.format(n=count)),
        Input(type="hidden", name="confirm", value="1"),
        footer=[cancel_button(TRASH_URL), Button(u.empty_trash, type="submit", cls="fs-btn is-danger")],
        action="/files/empty-trash",
    )


def show_dialog(req, dialog, *, folder: str = "", trash: bool = False):
    """The dialog alone for htmx; otherwise its page with the dialog open."""
    if is_htmx(req):
        return dialog
    if trash:
        return page(trash_window(dialog=dialog))
    return page(browser(folder, view=default_view(), sort="name", dialog=dialog))


def trash_entry(trash_name: str):
    return next((e for e in files_store.list_trash() if e.trash_name == trash_name), None)


# ---------------------------------------------------------------------------
# Recently Deleted.


def trash_window(dialog=None):
    u = ui()
    entries = files_store.list_trash()
    head = Div(
        Span(cfg().retention_note),
        A(u.empty_trash, href="/files/empty-trash", hx_get="/files/empty-trash", hx_target="#files-dialog", hx_swap="innerHTML",
          cls="fs-btn is-danger", aria_disabled="true" if not entries else None),
        cls="fs-trash-head",
    )
    if entries:
        rows = []
        for e in entries:
            left = files_store.days_left(e)
            rows.append(
                Tr(
                    Td(Span(entry_glyph(e), Span(e.name), cls="fs-name")),
                    Td(folder_label(parent_of(e.path)), cls="is-muted is-kind"),
                    Td(format_date(date.fromisoformat(e.deleted_on)), cls="is-muted is-date"),
                    Td(Span(cfg().days_left_label.format(n=left), cls="fs-days" + (" is-soon" if left <= 3 else ""))),
                    Td(
                        Div(
                            Form(Input(type="hidden", name="trash_name", value=e.trash_name),
                                 Button(u.restore, type="submit", cls="fs-btn"), action="/files/restore", method="post"),
                            A(u.delete_forever, href=f"/files/purge?trash_name={quote(e.trash_name)}",
                              hx_get=f"/files/purge?trash_name={quote(e.trash_name)}", hx_target="#files-dialog", hx_swap="innerHTML",
                              cls="fs-btn is-danger"),
                            cls="fs-row-actions",
                        ),
                    ),
                    cls="fs-row",
                    data_size=str(e.size),
                )
            )
        body = Table(
            Thead(Tr(Th(cfg().columns.name), Th(u.where_label, cls="is-kind"), Th(u.deleted_label, cls="is-date"),
                     Th(u.remaining_label), Th(Span(u.actions_label, cls="sr-only")))),
            Tbody(*rows),
            cls="fs-table",
        )
    else:
        body = P(u.trash_empty, cls="fs-empty")
    content = Section(head, body, cls="fs-content", id="files-content", aria_label=cfg().trash_label)
    # No preview pane: nothing in the bin is opened or selected, only
    # restored or deleted, and the names need the room.
    win = window(TRASH_URL, toolbar("", view="list", sort="name", label=cfg().trash_label, upload=False), content,
                 show_preview=False)
    return _with_dialog(win, dialog) if dialog is not None else win


# ---------------------------------------------------------------------------
# The viewer.


def viewer_bar(entry, *controls):
    u = ui()
    folder = parent_of(entry.path)
    return Div(
        A(icon(Icon.CHEVRON, size=14), folder_label(folder), href=folder_url(folder, selected=entry.name if not is_phone() else None),
          cls="fs-viewer-back", aria_label=u.back_to.format(folder=folder_label(folder))),
        H2(entry.name, cls="fs-viewer-title"),
        *controls,
        cls="fs-viewer-bar",
    )


def pdf_document(src: str, name: str, bar):
    """The pdf.js reader for the PDF at ``src``, under ``bar``.

    Separate from the Files entry because other apps show PDFs they never store
    -- a shop receipt, a bank statement -- and Chromium as Playwright ships it
    has no built-in PDF viewer: a raw PDF in a tab is a blank page and a silent
    download, for an agent and for anyone using the preview window.
    """
    u = ui()
    return Div(
        bar,
        Ol(cls="fs-thumbs", id="files-thumbs", aria_label=u.pages_label),
        Div(
            P(u.loading, cls="fs-viewer-status", id="files-viewer-status"),
            Noscript(P(u.needs_script, " ", A(u.open_new_tab, href=src))),
            cls="fs-pages",
            id="files-pages",
            tabindex="0",
            aria_label=name,
        ),
        Script(src=VIEWER_SCRIPT_URL, type="module"),
        cls="fs-viewer",
        id="files-viewer",
        data_src=src,
        data_error=u.load_error,
    )


def pdf_pager():
    u = ui()
    return Div(
        Button(icon(Icon.CHEVRON, size=14), type="button", cls="fs-icon-btn is-up", id="files-prev-page", aria_label=u.previous_page),
        Output(u.page_of.format(n=1, total="…"), id="files-page-indicator", aria_live="polite",
               data_template=u.page_of),
        Button(icon(Icon.CHEVRON, size=14), type="button", cls="fs-icon-btn is-down", id="files-next-page", aria_label=u.next_page),
        Span(cls="sr-only"),
        Button("−", type="button", cls="fs-icon-btn", id="files-zoom-out", aria_label=u.zoom_out),
        Output("100%", id="files-zoom-level", style="min-width:3.5rem"),
        Button("+", type="button", cls="fs-icon-btn", id="files-zoom-in", aria_label=u.zoom_in),
        cls="fs-pager",
    )


def pdf_viewer(entry):
    u = ui()
    raw = href("/files/raw", entry.path)
    return pdf_document(raw, entry.name, viewer_bar(
        entry,
        pdf_pager(),
        A(u.open_new_tab, href=raw, target="_blank", rel="noopener", cls="fs-btn"),
        A(u.download, href=raw + "?download=1", cls="fs-btn"),
    ))


def image_viewer(entry):
    u = ui()
    raw = href("/files/raw", entry.path)
    return Div(
        viewer_bar(entry, A(u.open_new_tab, href=raw, target="_blank", rel="noopener", cls="fs-btn"),
                   A(u.download, href=raw + "?download=1", cls="fs-btn")),
        Div(Img(src=raw, alt=entry.name, cls="fs-image"), cls="fs-pages", id="files-pages"),
        cls="fs-viewer is-plain",
        id="files-viewer",
    )


def text_viewer(entry):
    u = ui()
    raw = href("/files/raw", entry.path)
    text = files_store.read(entry.path).decode("utf-8", errors="replace")
    language = _language_for(entry.name)
    in_code = entry.path.split("/")[0] == files_store.CODE
    if in_code:
        code_action = A(u.open_in_editor, href=href("/codeeditor", entry.path.split("/", 1)[1]), cls="fs-btn is-primary")
    else:
        code_action = Form(
            Input(type="hidden", name="path", value=entry.path),
            Input(type="hidden", name="folder", value=files_store.CODE),
            Input(type="hidden", name="then", value="view"),
            Button(u.move_to_code, type="submit", cls="fs-btn"),
            action="/files/move",
            method="post",
        )
    return Div(
        viewer_bar(entry, code_action, A(u.download, href=raw + "?download=1", cls="fs-btn")),
        Div(
            Pre(Code(text, id="files-text-code"), cls="fs-text", data_language=language),
            cls="fs-pages",
            id="files-pages",
        ),
        Script(src=HIGHLIGHT_SCRIPT_URL),
        Script("""
(function () {
    var code = document.getElementById('files-text-code');
    var lang = code && code.parentElement.getAttribute('data-language');
    if (code && lang && window.OpenAppsHighlight) {
        code.innerHTML = window.OpenAppsHighlight.highlight(code.textContent, lang);
    }
})();
"""),
        cls="fs-viewer is-plain",
        id="files-viewer",
    )


def details_viewer(entry):
    u = ui()
    raw = href("/files/raw", entry.path)
    return Div(
        viewer_bar(entry, A(u.download, href=raw + "?download=1", cls="fs-btn is-primary")),
        Div(
            Div(
                thumb(entry, large=True),
                Dl(Dt(cfg().columns.kind), Dd(kind_label(entry.kind)), Dt(cfg().columns.size), Dd(format_size(entry.size)),
                   Dt(cfg().columns.date), Dd(format_date(modified_on(entry.path))), cls="fs-facts"),
                P(u.no_preview, cls="fs-preview-kind"),
                cls="fs-details",
            ),
            cls="fs-pages",
            id="files-pages",
        ),
        cls="fs-viewer is-plain",
        id="files-viewer",
    )


_LANGUAGE_BY_EXTENSION = {
    ".py": "python", ".js": "javascript", ".css": "css", ".yaml": "yaml", ".yml": "yaml",
    ".md": "markdown", ".sh": "shell", ".json": "json", ".html": "html", ".htm": "html",
}


def _language_for(name: str) -> str:
    """The highlighter's language for a file, by extension; plain text stays
    plain (the highlighter escapes unknown languages, never errors)."""
    return _LANGUAGE_BY_EXTENSION.get(PurePosixPath(name).suffix.lower(), "text")


# ---------------------------------------------------------------------------
# Routes. The specific ones first: `/files/{folder:path}` catches the rest.


def _view(view: str) -> str:
    return view if view in ("list", "grid") else default_view()


def _sort(sort: str) -> str:
    return sort if sort in ("name", "date", "size") else "name"


@rt("/files")
def files_index(view: str = "", sort: str = "", selected: str = ""):
    return page(browser("", view=_view(view), sort=_sort(sort), selected=selected))


@rt(TRASH_URL)
def files_trash():
    return page(trash_window())


@rt("/files/search")
def files_search(q: str = ""):
    u = ui()
    query = q.strip().casefold()
    found = sorted((e for e in walk("") if query and query in e.name.casefold()), key=lambda e: e.path.casefold())
    label = u.search_results.format(q=q.strip()) if query else u.search_label
    if found:
        body = list_view(found, "", "", show_where=True)
    else:
        body = P(u.no_results.format(q=q.strip()) if query else u.search_prompt, cls="fs-empty")
    content = Section(body, cls="fs-content", id="files-content", aria_label=label)
    return page(window("", toolbar("", view="list", sort="name", q=q, label=label, upload=False), content, show_preview=False))


def is_same_origin_path(src: str) -> bool:
    """Whether a browser resolves ``src`` to a path on this origin.

    Read the way a browser reads it, not as written: the URL parser drops
    tabs and newlines and treats ``\\`` as ``/``, so ``/\\evil.com/x.pdf`` and
    ``/<tab>/evil.com`` are both ``//evil.com`` -- another host.
    """
    seen = src.replace("\\", "/").translate({ord(c): None for c in "\t\n\r"})
    parts = urlsplit(seen)
    return seen.startswith("/") and not seen.startswith("//") and not (parts.scheme or parts.netloc)


@rt("/files/pdf")
def files_pdf(src: str = "", name: str = ""):
    """A PDF another app serves, in the Files reader (see ``pdf_document``).
    Same-origin paths only: this page fetches ``src`` with pdf.js, and an
    absolute or protocol-relative URL would turn it into a reader for anything."""
    if not is_same_origin_path(src):
        return not_found()
    name = name or PurePosixPath(src).name
    bar = Div(
        H2(name, cls="fs-viewer-title"),
        pdf_pager(),
        A(ui().download, href=src, download=name, cls="fs-btn"),
        cls="fs-viewer-bar",
    )
    return page(pdf_document(src, name, bar))


@rt("/files/view/{path:path}")
def files_view(path: str):
    entry = get_entry(path)
    if entry is None or entry.is_folder:
        return not_found()
    if is_pdf(entry.kind):
        return page(pdf_viewer(entry))
    if is_image(entry.kind):
        return page(image_viewer(entry))
    if is_text(entry.kind):
        return page(text_viewer(entry))
    return page(details_viewer(entry))


# Types a browser would run as a page or script. Every app shares one origin,
# so an uploaded or agent-written HTML file opened from Files could script the
# bank or the messenger and corrupt the state an episode is scored on. They are
# served as plain text instead.
_ACTIVE_KINDS = {
    "text/html", "application/xhtml+xml", "text/javascript", "application/javascript",
    "text/css", "application/xml", "text/xml",
}


@rt("/files/raw/{path:path}")
def files_raw(path: str, download: int = 0):
    """The file's bytes: inline, so a browser with a PDF viewer shows it (Open
    in new tab); or as an attachment (Download).

    Active types go out as ``text/plain``; everything gets ``nosniff``; and
    everything but PDFs is sandboxed, so an SVG opened on its own can't run
    its scripts. PDFs are exempt because Chrome won't show one in a
    sandboxed document.
    """
    entry = get_entry(path)
    if entry is None or entry.is_folder:
        return Response("Not found", status_code=404)
    disposition = "attachment" if download else "inline"
    kind = "text/plain; charset=utf-8" if entry.kind in _ACTIVE_KINDS else entry.kind
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(entry.name)}",
        "X-Content-Type-Options": "nosniff",
    }
    if not is_pdf(entry.kind):
        headers["Content-Security-Policy"] = "sandbox"
    return Response(files_store.read(entry.path), media_type=kind, headers=headers)


@app.get("/files/info")
def files_info_dialog(req, path: str = ""):
    entry = get_entry(path)
    if entry is None:
        return not_found()
    return show_dialog(req, info_dialog(entry), folder=parent_of(path))


@app.get("/files/rename")
def files_rename_dialog(req, path: str = ""):
    entry = get_entry(path)
    if entry is None:
        return not_found()
    return show_dialog(req, rename_dialog(entry), folder=parent_of(path))


@app.post("/files/rename")
def files_rename(req, path: str = "", new_name: str = ""):
    entry = get_entry(path)
    if entry is None:
        return not_found()
    new_name = new_name.strip()
    try:
        moved = files_store.rename(path, new_name)
    except FileExistsError:
        error = ui().exists_error.format(name=new_name, folder=folder_label(parent_of(path)))
        return show_dialog(req, rename_dialog(entry, new_name, error), folder=parent_of(path))
    except ValueError:
        return show_dialog(req, rename_dialog(entry, new_name, ui().invalid_name_error), folder=parent_of(path))
    return go(req, folder_url(parent_of(moved), selected=PurePosixPath(moved).name))


@app.get("/files/move")
def files_move_dialog(req, path: str = ""):
    entry = get_entry(path)
    if entry is None:
        return not_found()
    return show_dialog(req, move_dialog(entry), folder=parent_of(path))


@app.post("/files/move")
def files_move(req, path: str = "", folder: str = "", then: str = ""):
    entry = get_entry(path)
    if entry is None:
        return not_found()
    try:
        moved = files_store.move(path, folder)
    except (ValueError, FileNotFoundError, NotADirectoryError):
        return show_dialog(req, move_dialog(entry, ui().move_error), folder=parent_of(path))
    if then == "view":
        return go(req, href("/files/view", moved))
    return go(req, folder_url(parent_of(moved), selected=PurePosixPath(moved).name))


@app.post("/files/duplicate")
def files_duplicate(req, path: str = ""):
    entry = get_entry(path)
    if entry is None or path == files_store.CODE:
        return not_found()
    copy = files_store.duplicate(path)
    _pin_mtime(copy, str(cfg().today))
    return go(req, folder_url(parent_of(copy), selected=PurePosixPath(copy).name))


@app.post("/files/delete")
def files_delete(req, path: str = "", back: str = ""):
    entry = get_entry(path)
    if entry is None:
        return not_found()
    try:
        files_store.delete(path)
    except ValueError:
        return not_found()
    return go(req, folder_url(parent_of(path)))


@app.post("/files/restore")
def files_restore(req, trash_name: str = ""):
    if trash_entry(trash_name) is None:
        return not_found()
    restored = files_store.restore(trash_name)
    return go(req, folder_url(parent_of(restored), selected=PurePosixPath(restored).name))


@app.get("/files/purge")
def files_purge_dialog(req, trash_name: str = ""):
    entry = trash_entry(trash_name)
    if entry is None:
        return not_found()
    return show_dialog(req, purge_dialog(entry), trash=True)


@app.post("/files/purge")
def files_purge(req, trash_name: str = "", confirm: str = ""):
    """Permanent, so only with ``confirm=1``: without it the answer is the
    confirm dialog and nothing is deleted."""
    entry = trash_entry(trash_name)
    if entry is None:
        return not_found()
    if confirm != "1":
        return show_dialog(req, purge_dialog(entry), trash=True)
    files_store.purge(trash_name)
    return go(req, TRASH_URL)


@app.get("/files/empty-trash")
def files_empty_dialog(req):
    return show_dialog(req, empty_dialog(len(files_store.list_trash())), trash=True)


@app.post("/files/empty-trash")
def files_empty(req, confirm: str = ""):
    if confirm != "1":
        return show_dialog(req, empty_dialog(len(files_store.list_trash())), trash=True)
    files_store.empty_trash()
    return go(req, TRASH_URL)


@app.post("/files/upload")
async def files_upload(req):
    form = await req.form()
    folder = str(form.get("folder") or "Downloads")
    upload = form.get("file")
    if not folder_exists(folder) or folder.split("/")[0] == files_store.CODE:
        folder = "Downloads"
    if upload is None or not getattr(upload, "filename", ""):
        return go(req, folder_url(folder))
    name = PurePosixPath(str(upload.filename).replace("\\", "/")).name
    try:
        stored = files_store.save(folder, name, await upload.read())
    except ValueError:
        return go(req, folder_url(folder))
    _pin_mtime(stored, str(cfg().today))
    return go(req, folder_url(folder, selected=PurePosixPath(stored).name))


@rt("/files/{folder:path}")
def files_folder(folder: str, view: str = "", sort: str = "", selected: str = ""):
    if not folder_exists(folder):
        return not_found()
    return page(browser(folder, view=_view(view), sort=_sort(sort), selected=selected))


@app.get("/files_all")
def get_all():
    """Used for rewards."""
    return Response(json.dumps(files_store.snapshot()), headers={"Content-Type": "application/json"})


def get_files_routes():
    return app.routes
