"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The Files app: a browser over the shared file store.

The store (:mod:`open_apps.files_store`) is the state; this module seeds it
and serves it. Seeding renders every document in the content pack to real
bytes (:mod:`.documents`) on each launch and reset, so the store always
starts from the same files with the same checksums -- ``/files_all`` carries
those checksums, and a store that drifted between launches would hand every
task a spurious diff.

The store is configured here, before any other app needs it: the start page
sets apps up in ``AVAILABLE_APPS`` order and registers the shop last, so the
bank and shop find it ready when they write statements and receipts into it.

``/files_all`` is the scoring surface: :func:`files_store.snapshot`, sorted,
without modification times, and without the Code folder (that is the code
editor's workspace, scored through ``/codeeditor_all``).
"""
import json

from fasthtml.common import *

from open_apps import files_store
from open_apps.apps.files_app import documents
from open_apps.apps.start_page.helper import create_logo_header
from open_apps.frontend import local_hdrs
from open_apps.theme import theme_style

app, rt = fast_app(default_hdrs=False, hdrs=local_hdrs())

logo_title_container = None


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
    seed(_plain(config.files.get("documents")) or {}, _plain(config.files.get("owner")) or {})
    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.files,
        base_url="/files",
        current_file_path=__file__,
    )


def seed(specs: dict, owner: dict) -> None:
    """Render and store every document, then move the pre-deleted ones to
    Recently Deleted with their own ``deleted_on``.

    In key order, which is deterministic (the pack's order, then each pack's
    additions), though nothing depends on it: no two specs share a path, and
    a duplicate fails the launch rather than seeding a silent " (2)".
    """
    seen: set[str] = set()
    for key, spec in specs.items():
        if not spec:
            continue  # a pack can drop a default document with `key: null`
        path = f"{spec['folder']}/{spec['name']}"
        if path in seen:
            raise ValueError(f"files seed: {key!r} repeats {path!r}")
        seen.add(path)
        stored = files_store.save(spec["folder"], spec["name"], documents.render(spec, owner))
        if spec.get("deleted_on"):
            files_store.delete(stored, deleted_on=str(spec["deleted_on"]))


def cfg():
    return app.config.files


# ---------------------------------------------------------------------------
# Pages. A minimal listing for now; the browser, viewers and Recently Deleted
# are built on top of these routes.


def page(*content):
    return Div(
        theme_style(app.config, "files"),
        logo_title_container,
        *content,
        cls="files-root",
    )


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


@rt("/files")
def files_index():
    c = cfg()
    labels = c.folder_labels
    sections = []
    for folder in files_store.DEFAULT_FOLDERS:
        entries = [e for e in files_store.list_folder(folder) if not e.is_folder]
        rows = [
            Tr(Td(e.name), Td(e.kind), Td(_size(e.size)))
            for e in entries
        ]
        sections.append(
            Section(
                H2(labels.get(folder, folder)),
                Table(
                    Thead(Tr(Th(c.columns.name), Th(c.columns.kind), Th(c.columns.size))),
                    Tbody(*rows),
                )
                if rows
                else P(c.empty_folder_label),
                id=f"files-folder-{folder.lower()}",
            )
        )
    trash = files_store.list_trash()
    sections.append(
        Section(
            H2(c.trash_label),
            P(c.retention_note),
            Ul(
                *[
                    Li(e.name, " · ", c.days_left_label.format(n=files_store.days_left(e)))
                    for e in trash
                ]
            ),
            id="files-trash",
        )
    )
    return page(*sections)


@app.get("/files_all")
def get_all():
    """Used for rewards."""
    return Response(json.dumps(files_store.snapshot()), headers={"Content-Type": "application/json"})


def get_files_routes():
    return app.routes
