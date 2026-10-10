"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fasthtml.common import *
import os
import shutil
from typing import Dict
import json
from starlette.responses import Response
from open_apps.apps.start_page.helper import create_logo_header
from open_apps.frontend import local_hdrs
from open_apps.icons import Icon, icon
from open_apps.theme import _as_plain, load_theme, resolve_theme, theme_asset, theme_style

# Static, theme-agnostic component styles. Colors, fonts, radii and spacing
# are design tokens from the shared theme (`config/apps/theme/`), emitted
# per-request by `codeeditor_theme()`.
#
# The chrome is modelled on VS Code / github.dev: an explorer with a section
# header, compact rows and indentation guides; an editor group with a tab
# strip and a header row; a status bar along the bottom of the window.
#
# Every rule is scoped under `.codeeditor-app` and names its own classes
# rather than leaning on Tailwind utilities. Tailwind and daisyUI arrive from
# CDNs that an eval node cannot reach, and Pico (vendored, always present)
# paints every <button>, <select> and <h2> as a full-size form control, so
# these rules set each property they care about explicitly. Two-class
# selectors keep them above Pico's `select:not([multiple],[size])` and
# Tailwind preflight's `[type=button]`. `\f...` escapes are Font Awesome 5
# code points, hence the raw string.
_COMPONENT_STYLES = Style(
    r"""
    /* `:root` as well as `body`: Pico and daisyUI both paint the root from
       their own palettes (daisyUI's follows the OS dark-mode setting), and
       this sheet loads after both. */
    :root, body {
        background-color: var(--color-bg);
        color: var(--color-fg);
        font-family: var(--font-family);
    }
    body {
        padding: calc(var(--space) * 2);
    }

    /* --- Window ---------------------------------------------------------- */
    .codeeditor-app {
        --ce-row: calc(var(--space) * 2.75);
        --ce-chevron: calc(var(--space) * 2);
        --ce-gap: calc(var(--space) * 0.5);
        --ce-hover: color-mix(in srgb, var(--color-fg) 7%, transparent);
        --ce-selected: color-mix(in srgb, var(--color-primary) 16%, transparent);
        display: flex;
        flex-direction: column;
        /* The `--oa-*` terms take off the window chrome's title bar and dock
           when it is on (open_apps.ui.chrome) and are 0 when it is off, so
           the editor fits the window either way. */
        height: calc(100vh - 6rem - var(--oa-titlebar-h, 0px) - var(--oa-dock-space, 0px));
        min-height: 24rem;
        overflow: hidden;
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        background-color: var(--color-bg);
        color: var(--color-fg);
        font-family: var(--font-family);
        font-size: var(--font-size-sm);
        line-height: 1.4;
    }
    .codeeditor-app .codeeditor-page {
        display: flex;
        flex: 1;
        min-height: 0;
    }
    .codeeditor-app :focus-visible {
        outline: 2px solid var(--color-primary);
        outline-offset: -2px;
    }
    /* `!important`: a utility the rename script toggles on elements whose
       own rule sets a display (Tailwind, which used to supply it, is a CDN
       an eval node cannot reach). */
    .codeeditor-app .hidden {
        display: none !important;
    }
    .codeeditor-app .sr-only {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        white-space: nowrap;
    }

    /* --- Buttons and fields ---------------------------------------------- */
    .codeeditor-app .ce-btn {
        display: inline-flex;
        align-items: center;
        gap: var(--ce-gap);
        width: auto;
        margin: 0;
        padding: calc(var(--space) * 0.25) var(--space);
        border: 1px solid transparent;
        border-radius: calc(var(--radius) * 0.5);
        background-color: transparent;
        background-image: none;
        box-shadow: none;
        color: var(--color-fg);
        font-family: var(--font-family);
        font-size: var(--font-size-sm);
        font-weight: 500;
        line-height: 1.4;
        text-decoration: none;
        white-space: nowrap;
        cursor: pointer;
    }
    .codeeditor-app .ce-btn:hover {
        background-color: var(--ce-hover);
    }
    .codeeditor-app .ce-btn i {
        color: var(--color-muted);
    }
    .codeeditor-app .ce-btn-primary {
        background-color: var(--color-primary);
        border-color: var(--color-primary);
        color: var(--color-on-primary);
    }
    .codeeditor-app .ce-btn-primary i {
        color: var(--color-on-primary);
    }
    /* Mixed toward the foreground rather than `--color-primary-hover`: that
       token is a darker shade in every light theme but also in `dark`, where
       it puts black `on-primary` text on deep purple. */
    .codeeditor-app .ce-btn-primary:hover {
        background-color: color-mix(in srgb, var(--color-primary) 85%, var(--color-fg));
        border-color: color-mix(in srgb, var(--color-primary) 85%, var(--color-fg));
    }
    .codeeditor-app .ce-btn-danger,
    .codeeditor-app .ce-btn-danger i {
        color: var(--color-danger);
    }
    .codeeditor-app .ce-btn-danger {
        border-color: color-mix(in srgb, var(--color-danger) 45%, transparent);
    }
    .codeeditor-app .ce-btn-danger:hover {
        background-color: color-mix(in srgb, var(--color-danger) 12%, transparent);
    }
    .codeeditor-app .ce-field {
        display: flex;
        align-items: center;
        gap: var(--ce-gap);
    }
    .codeeditor-app .ce-field label {
        display: inline;
        margin: 0;
        color: var(--color-muted);
        font-size: var(--font-size-sm);
    }
    .codeeditor-app .ce-select,
    .codeeditor-app .ce-input {
        width: auto;
        height: auto;
        margin: 0;
        padding: calc(var(--space) * 0.25) var(--space);
        border: 1px solid var(--color-border);
        border-radius: calc(var(--radius) * 0.5);
        background-color: var(--color-bg);
        box-shadow: none;
        color: var(--color-fg);
        font-family: var(--font-family);
        font-size: var(--font-size-sm);
        line-height: 1.4;
    }
    /* Pico draws the select chevron as a background image; keep it, just
       make room for it at this smaller size. */
    .codeeditor-app .ce-select {
        padding-right: calc(var(--space) * 3.5);
        background-position: center right calc(var(--space) * 0.75);
        background-size: calc(var(--space) * 1.75) auto;
    }
    .codeeditor-app .ce-input {
        width: 100%;
    }

    /* --- Explorer -------------------------------------------------------- */
    /* Both panes sit on the page background by default and read as one slab;
       the explorer gets its own surface so the layout reads as "editor". */
    .codeeditor-app .sidebar {
        --sidebar-bg-color: var(--color-surface);
    }
    .codeeditor-app .codeeditor-sidebar {
        display: flex;
        flex-direction: column;
        flex: 0 0 16rem;
        min-width: 0;
        overflow-y: auto;
        background-color: var(--sidebar-bg-color);
        border-right: 1px solid var(--color-border);
    }
    .codeeditor-app .ce-pane-title {
        margin: 0;
        padding: var(--space) calc(var(--space) * 1.5) calc(var(--space) * 0.5);
        color: var(--color-muted);
        font-family: var(--font-family);
        font-size: calc(var(--font-size-sm) * 0.8);
        font-weight: 600;
        letter-spacing: 0.06em;
        line-height: 1.4;
        text-transform: uppercase;
    }
    .codeeditor-app .ce-explorer-actions {
        display: flex;
        flex-wrap: wrap;
        gap: calc(var(--space) * 0.25);
        padding: 0 var(--space) calc(var(--space) * 0.75);
    }
    .codeeditor-app .ce-explorer-actions .ce-btn {
        padding: calc(var(--space) * 0.25) calc(var(--space) * 0.5);
        font-weight: 400;
    }
    .codeeditor-app .codeeditor-tree {
        padding-bottom: var(--space);
        font-size: var(--font-size-sm);
    }
    .codeeditor-app .tree-row {
        display: flex;
        align-items: center;
        gap: var(--ce-gap);
        min-height: var(--ce-row);
        padding: 0 var(--space);
        color: var(--color-fg);
        white-space: nowrap;
        cursor: pointer;
    }
    .codeeditor-app .sidebar .file-row:hover,
    .codeeditor-app .sidebar .folder-row:hover {
        background-color: var(--ce-hover);
    }
    .codeeditor-app .tree-row.is-selected {
        background-color: var(--ce-selected);
    }
    /* Files have no chevron; indent them by its width so their icons line up
       with the folder icons above, as in VS Code. */
    .codeeditor-app .ce-file {
        padding-left: calc(var(--space) + var(--ce-chevron) + var(--ce-gap));
    }
    .codeeditor-app .ce-row-link {
        display: flex;
        flex: 1;
        align-items: center;
        gap: var(--ce-gap);
        min-width: 0;
        min-height: var(--ce-row);
        color: inherit;
        text-decoration: none;
    }
    .codeeditor-app .ce-row-link:hover {
        color: inherit;
        text-decoration: none;
    }
    /* The folder name is a <button> (its handler lives on the row); strip
       Pico's filled-control look so it reads as a tree label. */
    .codeeditor-app .tree-row button {
        width: auto;
        margin: 0;
        padding: 0;
        border: 0;
        border-radius: 0;
        background: none;
        box-shadow: none;
        color: inherit;
        font: inherit;
        line-height: inherit;
        text-align: left;
        cursor: pointer;
    }
    /* One chevron for both states, rotated a quarter turn when open rather
       than swapping one glyph for another. */
    .codeeditor-app .tree-row .folder-icon {
        display: inline-flex;
        flex: none;
        align-items: center;
        justify-content: center;
        width: var(--ce-chevron);
        color: var(--color-muted);
        transition: transform 0.12s ease;
    }
    .codeeditor-app .folder-row.is-expanded .folder-icon {
        transform: rotate(90deg);
    }
    @media (prefers-reduced-motion: reduce) {
        .codeeditor-app .tree-row .folder-icon {
            transition: none;
        }
    }
    .codeeditor-app .tree-row .folder-name {
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .codeeditor-app .ce-icon {
        flex: none;
        width: calc(var(--space) * 2);
        color: var(--color-muted);
        text-align: center;
    }
    .codeeditor-app .ce-icon-code {
        color: var(--color-primary);
    }
    /* Explorer glyphs are inline SVG (`_row_icon`) drawn in currentColor. */
    .codeeditor-app .row-icon {
        display: inline-flex;
        flex: none;
        align-items: center;
        justify-content: center;
        width: calc(var(--space) * 2);
        color: var(--color-muted);
    }
    .codeeditor-app .folder-row .row-icon {
        color: var(--color-accent);
    }
    /* Both folder glyphs are on the row; the open one shows while the folder
       is expanded (`is-open`, set alongside the inline display). */
    .codeeditor-app .row-icon .ce-folder-open,
    .codeeditor-app .folder-container.is-open > .tree-row .ce-folder-closed {
        display: none;
    }
    .codeeditor-app .folder-container.is-open > .tree-row .ce-folder-open {
        display: inline;
    }
    /* Indentation guide: a hairline under each open folder's chevron. */
    .codeeditor-app .folder-content {
        margin-left: calc(var(--space) + var(--ce-chevron) / 2);
        border-left: 1px solid color-mix(in srgb, var(--color-border) 70%, transparent);
    }
    /* --- Editor group ---------------------------------------------------- */
    .codeeditor-app .ce-editor {
        display: flex;
        flex: 1;
        flex-direction: column;
        min-width: 0;
        min-height: 0;
        overflow: auto;
        background-color: var(--color-bg);
    }

    .codeeditor-app .ce-editor-header {
        display: flex;
        flex: none;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: var(--space);
        min-height: calc(var(--space) * 4.5);
        padding: calc(var(--space) * 0.5) calc(var(--space) * 1.5);
        border-bottom: 1px solid var(--color-border);
    }
    .codeeditor-app .ce-editor-title {
        min-width: 0;
        margin: 0;
        color: var(--color-fg);
        font-family: var(--font-family);
        font-size: var(--font-size-sm);
        font-weight: 600;
        line-height: 1.4;
    }
    /* The file name carries `role="button"` (double-click renames it), which
       Pico paints as a filled primary button; it should read as a label. */
    .codeeditor-app .ce-title-text {
        display: flex;
        align-items: baseline;
        gap: var(--space);
        width: auto;
        margin: 0;
        padding: 0;
        border: 0;
        border-radius: 0;
        background: none;
        box-shadow: none;
        color: inherit;
        font: inherit;
        text-align: left;
        cursor: text;
    }
    .codeeditor-app .ce-rename-hint {
        color: var(--color-muted);
        font-weight: 400;
        opacity: 0;
    }
    .codeeditor-app .ce-editor-title:hover .ce-rename-hint,
    .codeeditor-app .ce-title-text:focus-visible .ce-rename-hint {
        opacity: 1;
    }
    .codeeditor-app .ce-editor-actions,
    .codeeditor-app .ce-selects {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: var(--space);
    }
    .codeeditor-app .ce-editor-actions .ce-selects {
        gap: calc(var(--space) * 1.5);
    }

    /* Tab strip. The strip's bottom rule is an inset shadow so the active
       tab's own background can cover it and run straight into the code. */
    .codeeditor-app .ce-tabs {
        display: flex;
        flex: none;
        align-items: stretch;
        min-height: calc(var(--space) * 4.5);
        background-color: var(--color-surface);
        box-shadow: inset 0 -1px 0 var(--color-border);
    }
    .codeeditor-app .ce-tab-list {
        display: flex;
        flex: 1;
        min-width: 0;
        overflow-x: auto;
    }
    .codeeditor-app .editor-tab {
        display: flex;
        flex: none;
        align-items: center;
        gap: var(--ce-gap);
        padding: 0 calc(var(--space) * 0.75) 0 calc(var(--space) * 1.25);
        border-right: 1px solid var(--color-border);
        color: var(--color-muted);
        white-space: nowrap;
        cursor: pointer;
    }
    .codeeditor-app .editor-tab:hover {
        color: var(--color-fg);
    }
    .codeeditor-app .editor-tab.is-active {
        background-color: var(--color-bg);
        box-shadow: inset 0 2px 0 var(--color-primary);
        color: var(--color-fg);
    }
    .codeeditor-app .editor-tab-close {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: calc(var(--space) * 2.5);
        height: calc(var(--space) * 2.5);
        margin: 0;
        padding: 0;
        border: 0;
        border-radius: calc(var(--radius) * 0.5);
        background: none;
        box-shadow: none;
        color: inherit;
        font-family: var(--font-family);
        font-size: var(--font-size-base);
        line-height: 1;
        cursor: pointer;
    }
    .codeeditor-app .editor-tab-close:hover {
        background-color: var(--ce-hover);
        color: var(--color-fg);
    }
    .codeeditor-app .ce-tab-actions {
        display: flex;
        flex: none;
        align-items: center;
        gap: calc(var(--space) * 0.75);
        padding: 0 calc(var(--space) * 1.5);
    }

    /* The code itself. A plain <textarea> unless `highlight` loads
       CodeMirror, which then replaces it with `.CodeMirror`. */
    .codeeditor-app .ce-editor-body {
        display: flex;
        flex: 1;
        flex-direction: column;
        min-height: 0;
    }
    .codeeditor-app .ce-code {
        flex: 1;
        width: 100%;
        height: auto;
        min-height: 0;
        margin: 0;
        padding: var(--space) calc(var(--space) * 2);
        border: 0;
        border-radius: 0;
        background-color: var(--color-bg);
        box-shadow: none;
        color: var(--color-fg);
        font-family: var(--font-mono);
        font-size: var(--font-size-sm);
        line-height: 1.6;
        resize: none;
        tab-size: 4;
    }
    .codeeditor-app .ce-code:focus {
        outline: none;
        box-shadow: none;
    }
    /* Nothing open: the read-only pane reads as VS Code's empty editor group,
       its message centred and quiet rather than sitting in the corner like
       a one-line document. */
    .codeeditor-app .ce-code:disabled {
        padding-top: 28vh;
        color: var(--color-muted);
        font-family: var(--font-family);
        text-align: center;
        cursor: default;
        opacity: 1;
    }
    .codeeditor-app .ce-editor-body .CodeMirror {
        flex: 1;
        font-family: var(--font-mono);
        font-size: var(--font-size-sm);
    }

    /* `default` only: the ways out as full buttons under the code. */
    .codeeditor-app .ce-editor-footer {
        display: flex;
        flex: none;
        flex-wrap: wrap;
        gap: calc(var(--space) * 1.5);
        padding: calc(var(--space) * 1.5);
        border-top: 1px solid var(--color-border);
    }
    .codeeditor-app .ce-btn-nav {
        padding: var(--space) calc(var(--space) * 2);
        border-color: var(--color-border);
        border-radius: var(--radius);
        background-color: var(--color-surface);
        color: var(--color-fg);
        font-size: var(--font-size-base);
        font-weight: 600;
    }
    .codeeditor-app .ce-btn-nav:hover {
        background-color: color-mix(in srgb, var(--color-fg) 10%, var(--color-surface));
        color: var(--color-fg);
        text-decoration: none;
    }

    /* --- Status bar ------------------------------------------------------ */
    .codeeditor-app .ce-statusbar {
        display: flex;
        flex: none;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        min-height: calc(var(--space) * 3);
        background-color: var(--color-surface);
        border-top: 1px solid var(--color-border);
        color: var(--color-muted);
        font-size: calc(var(--font-size-sm) * 0.9);
    }
    .codeeditor-app .ce-status-group {
        display: flex;
        align-items: center;
        align-self: stretch;
    }
    .codeeditor-app .ce-status-item {
        display: inline-flex;
        align-items: center;
        align-self: stretch;
        gap: var(--ce-gap);
        padding: 0 var(--space);
        color: inherit;
        text-decoration: none;
        white-space: nowrap;
    }
    .codeeditor-app a.ce-status-item:hover {
        background-color: var(--ce-hover);
        color: var(--color-fg);
        text-decoration: none;
    }
    .codeeditor-app .ce-status-item .ce-icon {
        width: auto;
        color: inherit;
    }
    /* The pickers, when they live here, shrink to status-bar items: no box
       until hovered. */
    .codeeditor-app .ce-statusbar .ce-selects {
        flex-wrap: nowrap;
        gap: 0;
        padding: 0 calc(var(--space) * 0.5);
    }
    .codeeditor-app .ce-statusbar .ce-field {
        gap: calc(var(--space) * 0.25);
        padding: 0 calc(var(--space) * 0.5);
    }
    .codeeditor-app .ce-statusbar .ce-field label,
    .codeeditor-app .ce-statusbar .ce-select {
        color: inherit;
        font-size: inherit;
    }
    .codeeditor-app .ce-statusbar .ce-select {
        padding-top: 0;
        padding-bottom: 0;
        border-color: transparent;
        background-color: transparent;
    }
    .codeeditor-app .ce-statusbar .ce-select:hover {
        border-color: var(--color-border);
        background-color: var(--color-bg);
    }
"""
)

# Dialogs (New Folder, errors) are built by inline scripts with daisyUI's
# `modal` markup and appended to <body>, outside `.codeeditor-app`. daisyUI
# comes from a CDN, so on an offline eval node they would land unstyled at
# the foot of the page; these rules give them an overlay and token colours
# either way, and agree with daisyUI where it does load.
_DIALOG_STYLES = Style(
    """
    .modal.modal-open {
        position: fixed;
        inset: 0;
        z-index: 999;
        display: grid;
        place-items: center;
        background-color: color-mix(in srgb, var(--color-fg) 35%, transparent);
        opacity: 1;
        visibility: visible;
        pointer-events: auto;
    }
    .modal-open .modal-box {
        width: min(28rem, calc(100vw - var(--space) * 4));
        padding: calc(var(--space) * 2.5);
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        background-color: var(--color-bg);
        color: var(--color-fg);
        font-family: var(--font-family);
        font-size: var(--font-size-sm);
    }
    .modal-open .modal-box h3 {
        margin: 0 0 var(--space);
        color: var(--color-fg);
        font-size: var(--font-size-base);
        font-weight: 600;
    }
    .modal-open .modal-box .text-error {
        color: var(--color-danger);
    }
    .modal-open .modal-box input {
        width: 100%;
        margin: 0;
        border: 1px solid var(--color-border);
        background-color: var(--color-bg);
        color: var(--color-fg);
        font-size: var(--font-size-sm);
    }
    .modal-open .modal-action {
        display: flex;
        justify-content: flex-end;
        gap: var(--space);
        margin-top: calc(var(--space) * 2);
    }
    .modal-open .modal-action .btn {
        width: auto;
        margin: 0;
        padding: calc(var(--space) * 0.5) calc(var(--space) * 1.5);
        border: 1px solid var(--color-border);
        border-radius: calc(var(--radius) * 0.5);
        background-color: transparent;
        color: var(--color-fg);
        font-family: var(--font-family);
        font-size: var(--font-size-sm);
    }
    .modal-open .modal-action .btn-primary {
        border-color: var(--color-primary);
        background-color: var(--color-primary);
        color: var(--color-on-primary);
    }
"""
)

# Font Awesome 5, vendored under apps/assets (no CDN) -- explorer, tab and
# status-bar icons. Every icon is `aria-hidden`, so the accessibility tree an
# agent reads is unchanged by it.
# Local fallbacks for the Tailwind utilities the markup still carries (the
# body's `p-4`, the folder rows' `flex`, ...). Tailwind is a CDN fetch; with
# it unreachable these are what render, using Tailwind's own values, so the
# page is the same with or without egress. Layout only, no colours.
_UTILITY_FALLBACKS = Style(r"""
    .flex { display: flex; }
    .items-center { align-items: center; }
    .justify-between { justify-content: space-between; }
    .justify-center { justify-content: center; }
    .w-1\/6 { width: 16.666667%; }
    .w-5\/6 { width: 83.333333%; }
    .p-4 { padding: 1rem; }
    .pl-2 { padding-left: 0.5rem; }
    .pl-4 { padding-left: 1rem; }
    .py-1 { padding-top: 0.25rem; padding-bottom: 0.25rem; }
    .ml-2 { margin-left: 0.5rem; }
    .mt-4 { margin-top: 1rem; }
    .rounded-lg { border-radius: 0.5rem; }
    .overflow-y-auto { overflow-y: auto; }
    .cursor-pointer { cursor: pointer; }
""")

_ICON_STYLESHEET = Link(rel="stylesheet", href="/assets/css/fontawesome-all.min.css")

def _as_dict(node):
    """Coerce an OmegaConf node (or None) to a plain dict."""
    return {k: v for k, v in node.items()} if node is not None else {}


# Global variables
_base_hdrs_no_highlight = (
    # Pico + htmx from apps/assets/vendor, not jsdelivr (see frontend.py).
    *local_hdrs(),
    Script(src="https://cdn.tailwindcss.com"),
    Link(
        rel="stylesheet",
        href="https://cdn.jsdelivr.net/npm/daisyui@4.11.1/dist/full.min.css",
    ),
    Script("""
        function getStorageKey(folderPath) {
            return `folder_state_${folderPath}`;
        }
    """),
)
current_dir = None
list_of_modes, list_of_themes = [], []
# CodeMirror syntax stylesheets, distinct from `list_of_themes` (the shared
# design themes the in-editor selector offers).
list_of_editor_themes = []
_base_hdrs = _base_hdrs_no_highlight
opened_files = {}
logo_title_container = None

# Initialize app with default headers
app = FastHTML(hdrs=[*local_hdrs(), *_base_hdrs], cls="p-4", default_hdrs=False)

import yaml
import os

def create_file_system(base_path, file_system):
    """
    Creates a file system based on the provided dictionary structure.

    Args:
        base_path (str): The root directory where the file system will be created.
        file_system (dict): A dictionary representing the file system.
    """
    if file_system is None:
        return
    for item in file_system:
        name = item['name']
        full_path = os.path.join(base_path, name)
        if item['type'] == 'folder':
            os.makedirs(full_path, exist_ok=True)
            create_file_system(full_path, item['content'])  # Recursive call for subfolders
        elif item['type'] == 'file':
            with open(full_path, 'w') as f:
                f.write(item['content'])
        else:
            print(f"Invalid type: {item['type']}")


def update_db_from_hydra(config):
    file_system = config.code_editor.filesystem
    create_file_system(current_dir, file_system)

def set_environment(config):
    """Set environment variables for the code editor app"""
    # Create styles with environment variables
    global app, _base_hdrs, list_of_modes, list_of_themes, list_of_editor_themes, current_dir, logo_title_container
    if getattr(config.code_editor, 'no_css', False):
        app.hdrs = ()
        app.config = config
        current_dir = config.code_editor.database_path + '/'
        logo_title_container = create_logo_header(
            app_config=config.start_page.apps.codeeditor,
            base_url="/codeeditor",
            current_file_path=__file__
        )
        return
    list_of_modes = config.code_editor.list_of_modes
    list_of_themes = config.code_editor.list_of_themes
    list_of_editor_themes = config.code_editor.list_of_editor_themes
    current_dir = config.code_editor.database_path + '/'
    if os.path.exists(current_dir):
        # alert the user
        print("- Code editor folder already exists. This is undesired!!! Please double check.")
        print("######## ########")
        return
    os.makedirs(current_dir, exist_ok=True)
    update_db_from_hydra(config)
    print(f"- Code editor filesystem created under {current_dir}")
    _base_hdrs_with_highlight = (
        # Pico + htmx from apps/assets/vendor, not jsdelivr (see frontend.py).
        *local_hdrs(),
        Script(src="https://cdn.tailwindcss.com"),
        Link(rel="stylesheet", href="https://cdn.jsdelivr.net/npm/daisyui@4.11.1/dist/full.min.css"),
        Link(rel="stylesheet", href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/codemirror.min.css"),
    )
    # Every stylesheet the page could ask for, loaded up front: a shared-theme
    # swap selects one by tone at request time. Include the tone-mapped names
    # even if `list_of_editor_themes` was trimmed, otherwise `apps/theme=dark`
    # asks CodeMirror for a stylesheet that is not on the page and the code
    # pane silently renders unstyled. Design-theme names are deliberately not
    # in here -- they have no CodeMirror stylesheet to fetch.
    tone_themes = list(_as_plain(getattr(config.code_editor, "editor_theme_by_tone", None) or {}).values())
    for theme_name in dict.fromkeys([*list_of_editor_themes, *tone_themes, config.code_editor.editor_theme]):
        _base_hdrs_with_highlight += (
            Link(rel="stylesheet", href=f"https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/theme/{theme_name}.min.css"),
        )

    _base_hdrs_with_highlight += (
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/codemirror.min.js"),
    )
    for mode in list_of_modes:
        _base_hdrs_with_highlight += (
            Script(src=f"https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/mode/{mode}/{mode}.min.js"),
        )
    _base_hdrs_with_highlight += (Script("""
        function getStorageKey(folderPath) {
            return `folder_state_${folderPath}`;
        }
    """),)
    _base_hdrs = _base_hdrs_with_highlight if config.code_editor.highlight else _base_hdrs_no_highlight

    # Drop every third-party stylesheet/script. The inline blocks carry the
    # design tokens, the component styles and the utility fallbacks, and Pico,
    # htmx and the icon font are served locally, so the page renders the same
    # with no outbound network.
    #
    # Worth knowing which way the risk runs: on a host that already has no
    # egress those CDN tags fail silently and the inline CSS is what renders
    # anyway, so setting this makes the result deterministic rather than
    # network-dependent. On a host *with* egress it is a visible change,
    # because Tailwind and DaisyUI stop contributing.
    if getattr(config.code_editor, 'no_egress', False):
        _base_hdrs = (
            *local_hdrs(),
            Script("""
                function getStorageKey(folderPath) {
                    return `folder_state_${folderPath}`;
                }
            """),
        )

    app.config = config
    # Update app headers by extending existing ones
    app.hdrs = (
        *_base_hdrs, _ICON_STYLESHEET, _UTILITY_FALLBACKS, _COMPONENT_STYLES,
        _DIALOG_STYLES, theme_switcher_script(config),
    )

    if config.code_editor.sort_feature:
        list_of_modes = sorted(list_of_modes)
        list_of_themes = sorted(list_of_themes)

    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.codeeditor,
        base_url="/codeeditor",
        current_file_path=__file__
    )

def codeeditor_theme():
    """The active theme's `:root` token block.

    Rendered into the page body (not `app.hdrs`) so live `reconfigure` theme
    swaps take effect without rebuilding the headers.
    """
    return theme_style(app.config, "code_editor")


def current_design_theme() -> str:
    """Name of the design theme the page is currently rendering.

    Resolved from the live config rather than tracked separately, so the
    selector always agrees with the token block `codeeditor_theme()` emits --
    including after `/codeeditor/update_config` pins a new one, and with a
    null `apps.code_editor.theme` inheriting the global selection.
    """
    return resolve_theme(app.config, "code_editor").get("name", "default")


def current_editor_theme():
    """CodeMirror's syntax-highlighting stylesheet name.

    Not the shared design-token theme -- CodeMirror ships a whole stylesheet
    per theme, which no CSS variable can substitute for. The shared theme
    therefore picks one indirectly via its `tone` asset, so `apps/theme=dark`
    darkens the editor pane and not just the chrome around it.
    """
    cfg = app.config.code_editor
    tone = theme_asset(app.config, "code_editor", "tone", "light")
    by_tone = _as_plain(getattr(cfg, "editor_theme_by_tone", None) or {})
    return by_tone.get(tone, cfg.editor_theme)


def theme_switcher_script(config) -> Script:
    """Embed every selectable theme's tokens so the selector can swap live.

    The tokens for all of ``list_of_themes`` are inlined as JSON, so changing
    theme is a set of ``style.setProperty`` calls on :root -- no page reload,
    no server round-trip, and nothing fetched from a CDN. The server is still
    told (``/codeeditor/update_config``), so the choice survives navigation and
    shows up in the config an eval records, but the repaint does not wait on it.

    Every token any theme defines is cleared first: themes do not all define
    the same keys, and a key only the previous theme set would otherwise stay
    on :root and leak into the new one.
    """
    names = list(getattr(config.code_editor, "list_of_themes", []) or [])
    palettes = {name: _as_plain(load_theme(name).get("tokens", {})) for name in names}
    return Script(f"""
        window.OPENAPPS_THEMES = {json.dumps(palettes)};
        window.applyTheme = function(name) {{
            var tokens = window.OPENAPPS_THEMES[name];
            if (!tokens) {{ return false; }}
            var root = document.documentElement;
            var allKeys = {{}};
            Object.keys(window.OPENAPPS_THEMES).forEach(function(theme) {{
                Object.keys(window.OPENAPPS_THEMES[theme]).forEach(function(k) {{
                    allKeys[k] = true;
                }});
            }});
            Object.keys(allKeys).forEach(function(k) {{
                root.style.removeProperty('--' + k);
            }});
            Object.keys(tokens).forEach(function(k) {{
                root.style.setProperty('--' + k, tokens[k]);
            }});
            return true;
        }};
    """)


def current_layout():
    """The active structure variant from `config/apps/code_editor/layout/`.

    Consumed by :func:`editor_page` (where the explorer sits) and
    :func:`_selects_in_status_bar` (where the Language / Theme pickers sit).
    Nothing else varies, so routes, ids and the `/codeeditor_all` tree are
    identical across layouts.
    """
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return getattr(config.code_editor, "layout", "default")


# Layout rules live here rather than in `_COMPONENT_STYLES` so that the
# structural variants stay readable as a group. `default` needs none: the
# base styles are the VS Code arrangement, explorer left of the editor.
_LAYOUT_STYLES = Style("""
    /* sidebar_right -- the explorer comes after the editor in the DOM (see
       `editor_page`), so only its dividing border has to swap sides. */
    .codeeditor-app.layout-sidebar_right .codeeditor-sidebar {
        border-right: 0;
        border-left: 1px solid var(--color-border);
    }

    /* top_tree -- no side column: the explorer becomes a single-row file bar
       above the editor, in the manner of an editor's tab strip. The section
       title and New File / New Folder lead, then every tree entry in one
       scrolling row. */
    .codeeditor-app.layout-top_tree .codeeditor-page {
        flex-direction: column;
    }
    .codeeditor-app.layout-top_tree .codeeditor-sidebar {
        flex: none;
        flex-direction: row;
        align-items: center;
        gap: var(--space);
        overflow-x: auto;
        overflow-y: hidden;
        padding: calc(var(--space) * 0.5) var(--space);
        border-right: 0;
        border-bottom: 1px solid var(--color-border);
    }
    .codeeditor-app.layout-top_tree .ce-pane-title {
        flex: none;
        padding: 0 calc(var(--space) * 0.5);
    }
    .codeeditor-app.layout-top_tree .ce-explorer-actions {
        flex: none;
        flex-wrap: nowrap;
        padding: 0 var(--space) 0 0;
        border-right: 1px solid var(--color-border);
    }
    .codeeditor-app.layout-top_tree .codeeditor-tree {
        display: flex;
        align-items: center;
        gap: calc(var(--space) * 0.25);
        padding: 0;
    }

    /* An expanded folder's children join the row right after it rather than
       opening a block underneath, which would make the bar grow downwards
       over the editor. `display: contents` keeps the DOM -- and so the
       accessibility tree -- exactly as the other layouts have it. Collapsed
       folders keep their inline `display: none`; only an open folder's
       content (`is-open`, set by the folder toggle alongside the inline
       display) is matched, and `!important` beats that inline value. */
    .codeeditor-app.layout-top_tree .folder-container,
    .codeeditor-app.layout-top_tree .folder-container.is-open > .folder-content {
        display: contents !important;
    }
    .codeeditor-app.layout-top_tree .tree-row {
        flex: none;
        padding: 0 var(--space);
        border-radius: calc(var(--radius) * 0.5);
    }

    /* Entries inside an expanded folder: a leading rule marks them as
       nested, the one hierarchy cue a flat row can still give. */
    .codeeditor-app.layout-top_tree .folder-content .tree-row {
        border-left: 2px solid var(--color-border);
        border-radius: 0;
    }
""")


def editor_page(side_bar, main_screen, status_bar=None):
    """Assemble the explorer, the editor group and the status bar per layout.

    The three page handlers (`/codeeditor/`, the folder view and the file
    view) all compose their page through here, so a new variant is added in
    one place instead of three.

    * `default`       -- explorer left of the editor
    * `sidebar_right` -- explorer right of the editor (real DOM reorder, so
      the accessibility tree matches what is rendered)
    * `top_tree`      -- explorer as a horizontal file bar above the editor

    The status bar spans the whole window in every layout, as in VS Code.
    """
    layout = current_layout()
    if layout == "sidebar_right":
        panes = (main_screen, side_bar)
    else:
        panes = (side_bar, main_screen)
    return Div(cls=f"codeeditor-app layout-{layout}")(
        _LAYOUT_STYLES,
        Div(cls=f"codeeditor-page layout-{layout}")(*panes),
        *([status_bar] if status_bar is not None else []),
    )


def _icon(cls: str) -> I:
    """A decorative Font Awesome glyph, hidden from the accessibility tree."""
    return I(cls=f"ce-icon {cls}", **{"aria-hidden": "true"})


# Extensions that get the code-file glyph in the explorer and the tab strip;
# anything else gets a plain document. One glyph per kind rather than a logo
# per language: FA5 Free has no marks for most of these.
_CODE_EXTENSIONS = (
    ".c", ".cc", ".cpp", ".css", ".go", ".h", ".html", ".java", ".js",
    ".json", ".py", ".rs", ".sh", ".ts", ".yaml", ".yml",
)


def _file_icon(name: str) -> I:
    if name.lower().endswith(_CODE_EXTENSIONS):
        return _icon("far fa-file-code ce-icon-code")
    return _icon("far fa-file-alt")


def _row_icon(*svgs) -> I:
    """The explorer row's glyph slot: inline SVG from `open_apps.icons`.

    SVG rather than the icon font in the tree: it inherits colour through
    `currentColor`, so a live theme swap recolours it with everything else,
    and it needs no stylesheet to arrive. Decorative, like every icon here.
    """
    return I(*svgs, cls="row-icon", **{"aria-hidden": "true"})


def _file_svg(name: str):
    """One file glyph for every kind -- the icon set has no code-file mark --
    with code files tinted, as the icon-font glyph used to distinguish them."""
    is_code = name.lower().endswith(_CODE_EXTENSIONS)
    return icon(Icon.FILE, size=14, cls="ce-icon-code" if is_code else "")


def _nav_in_status_bar() -> bool:
    """Whether the ways out of the editor are quiet status-bar items.

    Outside `default` they are, as in VS Code: they leave the editor, they
    are not what you came to it for. `default` keeps them as full buttons
    under the editor, where they always were: the navigation tasks
    (`navigate_from_codeeditor_to_todo` and its variants) are run by
    screenshot agents that have to find "Return to List of Apps".
    """
    return current_layout() != "default"


def return_to_index(prominent: bool = False):
    if prominent:
        return A("Code Editor Index Page", href="/codeeditor", cls="ce-btn ce-btn-nav")
    return A(_icon("fas fa-code"), "Code Editor Index Page", href="/codeeditor", cls="ce-status-item")


def return_to_home(prominent: bool = False):
    if prominent:
        return A("Return to List of Apps", href="/", cls="ce-btn ce-btn-nav")
    return A(_icon("fas fa-home"), "Return to List of Apps", href="/", cls="ce-status-item")


def editor_footer() -> list:
    """`default` only: the two navigation buttons, bottom-left of the editor
    pane directly under the code, in their original order."""
    if _nav_in_status_bar():
        return []
    return [Div(cls="ce-editor-footer")(return_to_index(prominent=True), return_to_home(prominent=True))]

def newfile_index(current_path):
    # files_root = os.path.join(current_dir, "files")
    files_root = current_dir
    # Use current_path directly as it now represents either a file or folder path
    target_dir = os.path.join(files_root, current_path if current_path else "")
    if os.path.isfile(target_dir):
        target_dir = os.path.dirname(target_dir)
    i = 1
    while os.path.exists(os.path.join(target_dir, f"Untitled-{i}")):
        i += 1
    return i

def get_file_tree(path: str) -> Dict:
    """Recursively build a file tree structure"""
    # base_path = os.path.join(current_dir, "files")
    base_path = current_dir
    tree = {'type': 'folder', 'name': os.path.basename(path), 'children': []}
    try:
        for item in sorted(os.listdir(path)):
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                tree['children'].append(get_file_tree(item_path))
            else:
                # Remove the 'files/' prefix from the path
                relative_path = os.path.relpath(item_path, base_path)
                tree['children'].append({
                    'type': 'file',
                    'name': item,
                    'path': relative_path,
                    'content': open(item_path).read()
                })
    except OSError:
        pass
    return tree

def create_sidebar(current_path: str = None) -> Div:
    """Create the sidebar with file tree"""
    # files_root = os.path.join(current_dir, "files")
    files_root = current_dir
    file_tree = get_file_tree(files_root)

    def render_tree_item(item, path=''):
        # Only the open item is selected, as in VS Code. (Ancestor folders of
        # the open file used to share the highlight, which lit up a whole
        # branch of the tree.)
        if item['type'] == 'file':
            file_path = item['path']
            is_current = current_path == file_path
            # `file-row` / `is-current` are the semantic hooks the row styles
            # select on; `is-selected` is the highlight itself.
            return Div(
                cls=f"tree-row ce-file file-row{' is-selected is-current' if is_current else ''}"
            )(
                A(
                    _row_icon(_file_svg(item['name'])),
                    item['name'],
                    href=f"/codeeditor/{file_path}",
                    cls="row-link ce-row-link",
                    **({"aria-current": "page"} if is_current else {}),
                )
            )
        else:
            folder_path = os.path.join(path, item['name'])
            is_current = current_path == folder_path
            return Div(cls="folder-container")(
                # Merge span elements into a single clickable div. `flex`
                # stays: the DOMContentLoaded script below finds this row with
                # `container.querySelector('.flex')`. The toggle mirrors its
                # state onto an `is-open` class and `aria-expanded`, which the
                # styles select on instead of the inline display value.
                Div(
                    cls=f"tree-row ce-folder folder-row flex{' is-selected' if is_current else ''}",
                    **{
                        "data-path": folder_path,
                        "onclick": f"""
                            const container = this.closest('.folder-container');
                            const content = container.querySelector('.folder-content');
                            const icon = this.querySelector('.folder-icon');
                            const isVisible = content.style.display === 'block';
                            content.style.display = isVisible ? 'none' : 'block';
                            // One chevron, rotated by CSS -- no glyph swap.
                            this.classList.toggle('is-expanded', !isVisible);
                            icon.setAttribute('aria-expanded', (!isVisible).toString());
                            container.classList.toggle('is-open', !isVisible);

                            const storageKey = getStorageKey('{folder_path}');
                            localStorage.setItem(storageKey, (!isVisible).toString());

                            window.location = '/codeeditor/{folder_path}';
                        """
                    }
                )(
                    # Chevron and folder glyph are not buttons: an icon-only
                    # button is an unnamed node in the accessibility tree for
                    # every folder. The name stays a Button, so the folder keeps
                    # one named, clickable node to target.
                    Span(icon(Icon.CHEVRON, size=12), cls="folder-icon", **{"aria-expanded": "false"}),
                    _row_icon(
                        icon(Icon.FOLDER, size=14, cls="ce-folder-closed"),
                        icon(Icon.FOLDER_OPEN, size=14, cls="ce-folder-open"),
                    ),
                    Button(item['name'], cls="folder-name", onclick=""),
                ),
                Div(
                    cls="folder-content",
                    style="display: none"
                )(
                    *[render_tree_item(child, folder_path) for child in item['children']]
                )
            )

    next_index = newfile_index(current_path)
    # files_root = os.path.join(current_dir, "files")
    files_root = current_dir
    # Handle relative paths for folder creation
    if current_path is None:
        folder_path = ""
    elif os.path.isfile(os.path.join(files_root, current_path)):
        folder_path = os.path.dirname(current_path)
    # it is also possible that current_path is not a folder path nor a file path
    # like, it points to a to-be-saved new file, but the file has not been saved yet
    elif not os.path.exists(os.path.join(files_root, current_path)):
        folder_path = os.path.dirname(current_path)
    else:
        folder_path = current_path
    return Div(
        # `codeeditor-sidebar` distinguishes this panel from the editor pane;
        # the layout rules target it. `sidebar` carries its own surface colour.
        cls="codeeditor-sidebar sidebar main-content",
    )(
        H2("Explorer", cls="ce-pane-title"),
        # New File / New Folder keep their visible labels: the UI questions
        # (tests/ui_questions) ask for "two buttons labeled 'New File' and
        # 'New Folder' at the top of the sidebar".
        Div(cls="ce-explorer-actions")(
            Button(
                _icon("far fa-file"),
                "New File",
                cls="ce-btn",
                onclick=f"""
                    const path = '{folder_path or ""}';
                    // Check if current path is already an unsaved Untitled file
                    if (path.includes('Untitled-') && !path.includes('/')) {{
                        showErrorModal('Please save the current new file first');
                        return;
                    }}
                    const newPath = path ? path + '/Untitled-{next_index}' : 'Untitled-{next_index}';
                    window.location = '/codeeditor/' + newPath;
                """
            ),
            Button(
                _icon("far fa-folder"),
                "New Folder",
                cls="ce-btn",
                onclick=f"""
                    const path = '{folder_path or ""}';
                    // Create a modal dynamically
                    const modal = document.createElement('div');
                    modal.className = 'modal modal-open'; // daisyUI classes to open the modal
                    modal.innerHTML = `
                        <div class="modal-box">
                            <h3 class="font-bold text-lg">Enter Folder Name</h3>
                            <input type="text" id="folderNameInput" class="input input-bordered w-full max-w-xs" placeholder="Folder Name">
                            <div class="modal-action">
                                <button class="btn btn-primary" onclick="createFolder(this.closest('.modal'))">Create</button>
                                <button class="btn" onclick="this.closest('.modal').remove()">Cancel</button>
                            </div>
                        </div>
                    `;
                    document.body.appendChild(modal);

                    // Function to handle folder creation
                    window.createFolder = (modalElement) => {{
                        const folderNameInput = modalElement.querySelector('#folderNameInput');
                        const folderName = folderNameInput.value;

                        if (folderName) {{
                            if (folderName.includes('Untitled-')) {{
                                modalElement.remove();
                                showErrorModal('Cannot create folders with "Untitled-" in the name. This prefix is reserved for new files.');
                                return;
                            }}
                            const newPath = path ? path + '/' + folderName : folderName;
                            fetch('/codeeditor/create_folder/' + newPath, {{
                                method: 'POST'
                            }})
                            .then(r => r.json())
                            .then(data => {{
                                modalElement.remove(); // Close the modal
                                if (data.success) window.location.reload();
                                else {{
                                    showErrorModal('Failed to create folder: ' + data.error);
                                }};
                            }});
                        }} else {{
                            modalElement.remove();
                        }}
                    }};
                """
            )
        ),
        Div(cls="codeeditor-tree")(
            *[render_tree_item(child) for child in file_tree['children']]
        ),
        Script("""
            document.addEventListener('DOMContentLoaded', () => {
                document.querySelectorAll('.folder-container').forEach(container => {
                    const folderHeader = container.querySelector('.flex');
                    const icon = container.querySelector('.folder-icon');
                    const content = container.querySelector('.folder-content');
                    const folderPath = folderHeader.getAttribute('data-path');
                    
                    // Set initial state from localStorage, default to collapsed (false)
                    const storageKey = getStorageKey(folderPath);
                    const isExpanded = localStorage.getItem(storageKey) === 'true';
                    
                    // Always start collapsed unless explicitly set to expanded in localStorage
                    content.style.display = isExpanded ? 'block' : 'none';
                    folderHeader.classList.toggle('is-expanded', isExpanded);
                    icon.setAttribute('aria-expanded', isExpanded.toString());
                    container.classList.toggle('is-open', isExpanded);
                });
            });
            function showErrorModal(message) {
                const modal = document.createElement('div');
                modal.className = 'modal modal-open';
                modal.innerHTML = `
                    <div class="modal-box">
                        <h3 class="font-bold text-lg text-error">Error</h3>
                        <p class="py-4">${message}</p>
                        <div class="modal-action">
                            <button class="btn" onclick="this.closest('.modal').remove()">Close</button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modal);
            }
        """)
    )

# The Language / Theme pickers' change handlers. One copy for all three
# views: the folder and file views used to omit the error report the index
# view had, so a failed update there was silent.
_MODE_ONCHANGE = """
    editor.setOption('mode', this.value);
    fetch('/codeeditor/update_config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            type: 'mode',
            value: this.value
        })
    })
    .then(r => r.json())
    .then(data => {
        if (!data.success) {
            showErrorModal('Failed to update mode: ' + data.error);
        }
    });
"""

_THEME_ONCHANGE = """
    window.applyTheme(this.value);
    editor.setOption('theme', this.value);
    fetch('/codeeditor/update_config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            type: 'theme',
            value: this.value
        })
    })
    .then(r => r.json())
    .then(data => {
        if (!data.success) {
            showErrorModal('Failed to update theme: ' + data.error);
        }
    });
"""


def _selects_in_status_bar() -> bool:
    """Whether the Language / Theme pickers live in the status bar.

    VS Code keeps its language picker in the status bar, and the non-default
    layouts follow it. `default` keeps them in the top-right of the editor
    pane: the UI questions in `tests/ui_questions/ui_questions.json` are
    built from the default screenshot and ask where they sit.
    """
    return current_layout() != "default"


def editor_selects():
    """The Language and Theme pickers -- ids, names, labels and options as
    they always were; only their placement and styling vary."""
    return Div(cls="ce-selects")(
        Div(cls="ce-field")(
            Label("Language: "),
            Select(id="mode-selector", cls="ce-select", onchange=_MODE_ONCHANGE)(
                *[Option(mode, value=mode, selected=(mode == app.config.code_editor.mode)) for mode in list_of_modes]
            ),
        ),
        Div(cls="ce-field")(
            Label("Theme: "),
            Select(id="theme-selector", cls="ce-select", onchange=_THEME_ONCHANGE)(
                *[Option(theme, value=theme, selected=(theme == current_design_theme())) for theme in list_of_themes]
            ),
        ),
    )


def editor_header(title, *actions):
    """The row above the code: what is open on the left, actions and (in
    `default`) the pickers on the right."""
    selects = [] if _selects_in_status_bar() else [editor_selects()]
    return Div(cls="ce-editor-header")(
        title,
        Div(cls="ce-editor-actions")(*actions, *selects),
    )


def status_bar(*meta):
    """The strip along the bottom of the window.

    Outside `default`, navigation out of the editor sits bottom-left, where
    VS Code keeps its remote indicator, and the pickers join the file facts
    bottom-right.
    """
    selects = [editor_selects()] if _selects_in_status_bar() else []
    nav = [return_to_home(), return_to_index()] if _nav_in_status_bar() else []
    return Div(cls="ce-statusbar")(
        Div(cls="ce-status-group")(*nav),
        Div(cls="ce-status-group")(
            *[Span(item, cls="ce-status-item") for item in meta],
            *selects,
        ),
    )


def editor_binding(options_js: str) -> str:
    """JS that binds the page-global ``editor`` used by Save / the selectors.

    With ``code_editor.highlight`` on, ``editor`` is a CodeMirror instance
    wrapping the ``#editor`` textarea. With it off there is no CodeMirror on
    the page (the CDN scripts are only added in the highlight branch of
    ``set_environment``), so bind a small shim over the plain textarea that
    exposes the handful of methods the page calls: ``getValue`` (Save),
    ``setValue``, ``setOption`` (mode/theme selectors), and ``setSize``.

    Without the shim the emitted JS was
    ``var editor = (document.getElementById('editor'), {...});`` -- the comma
    operator, which bound ``editor`` to the *options object*. Every
    ``editor.getValue()`` then threw a TypeError, so the Save button silently
    did nothing: no POST, no reload, no error modal.
    """
    if app.config.code_editor.highlight:
        return (
            "var editor = CodeMirror.fromTextArea(document.getElementById('editor'), "
            f"{options_js});"
        )
    return """
        var editorTextarea = document.getElementById('editor');
        var editor = {
            getValue: function() { return editorTextarea.value; },
            setValue: function(value) { editorTextarea.value = value; },
            getOption: function() { return null; },
            setOption: function(name, value) {
                // Themes are design tokens, applied to :root by
                // window.applyTheme, so this works with no CodeMirror on the
                // page and nothing fetched from a CDN.
                if (name === 'theme' && window.applyTheme) {
                    window.applyTheme(value);
                }
            },
            setSize: function() {},
            refresh: function() {},
            focus: function() { editorTextarea.focus(); }
        };"""


def editor_script(options: str) -> Script:
    """Bind the page's ``editor`` (see :func:`editor_binding`).

    `setSize("100%", "100%")`: the editor body is a flex column that already
    fills the window, so CodeMirror takes its height from there rather than
    from a viewport calculation that ignored the tab strip and status bar.
    """
    highlight = app.config.code_editor.highlight
    return Script(f"""
        {editor_binding(options)}
        {'editor.setSize("100%", "100%");' if highlight else ''}
    """)


_EDIT_KEYS = """
                        extraKeys: {
                            "Tab": function(cm) {
                                if (cm.somethingSelected()) {
                                    cm.indentSelection("add");
                                } else {
                                    cm.replaceSelection("    ", "end", "+input");
                                }
                            },
                            "Shift-Tab": function(cm) {
                                cm.indentSelection("subtract");
                            }
                        }"""


@app.get("/codeeditor/")
def index():
    side_bar = create_sidebar()
    # by default, the main screen should display an empty code editor
    main_screen = Div(cls="ce-editor")(
        # "No file selected" stays the header text: a UI question asks for it.
        editor_header(H2("No file selected", cls="ce-editor-title")),
        Div(cls="ce-editor-body")(
            Textarea(
                app.config.code_editor.welcome_message or "Welcome! Happy coding everyday!",
                id="editor",
                cls="ce-code",
                disabled="disabled"
            ),
            editor_script(f"""{{
                        mode: '{app.config.code_editor.mode}',
                        theme: '{current_editor_theme()}',
                        lineNumbers: true,
                        indentUnit: 4,
                        tabSize: 4,
                        indentWithTabs: false,
                        smartIndent: true,
                        lineWrapping: true,{_EDIT_KEYS}
                    }}"""),
        ),
        *editor_footer(),
    )
    page = editor_page(side_bar, main_screen, status_bar())
    return Div(codeeditor_theme(), logo_title_container, page)


@app.get("/codeeditor/{path:path}")
def get(path: str):
    # Check if the path is a directory
    # full_path = os.path.join(current_dir, "files", path)
    full_path = os.path.join(current_dir, path)
    if os.path.isdir(full_path):
        # If it's a directory, show the folder view
        return get_folder(path)
    else:
        # If it's a file, show the file editor
        return get_file(path)

def get_folder(folder: str):
    """Handle folder view with empty editor"""
    side_bar = create_sidebar(folder)
    delete_folder = Button(
        "Delete Folder",
        cls="ce-btn ce-btn-danger",
        onclick=f"""
            fetch('/codeeditor/delete/{folder}', {{
                method: 'POST'
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    window.location = '/codeeditor/';
                }} else {{
                    showErrorModal('Failed to delete folder: ' + data.error);
                }}
            }});
        """
    )
    main_screen = Div(cls="ce-editor")(
        editor_header(H2(f"Folder: {folder}", cls="ce-editor-title"), delete_folder),
        Div(cls="ce-editor-body")(
            Textarea(
                "Select a file to edit or create a new one.",
                id="editor",
                cls="ce-code",
                disabled="disabled"
            ),
            editor_script(f"""{{
                        mode: '{app.config.code_editor.mode}',
                        theme: '{current_editor_theme()}',
                        lineNumbers: true,
                        readOnly: true
                    }}"""),
        ),
        *editor_footer(),
    )
    page = editor_page(side_bar, main_screen, status_bar())
    return Div(codeeditor_theme(), logo_title_container, page)

def get_file(file: str):
    # read the content of the file and display it in the editor
    try:
        # file_path = os.path.join(current_dir, "files", file)
        file_path = os.path.join(current_dir, file)
        with open(file_path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    # same layout and sidebar as the main screen
    side_bar = create_sidebar(file)
    file_name = file.split('/')[-1]
    save_button = Button(
        "Save",
        cls="ce-btn ce-btn-primary",
        onclick=f"""
            const content = editor.getValue();
            fetch('/codeeditor/save/{file}', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{content: content}})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    window.location.reload();
                }} else {{
                    showErrorModal('Failed to save file: ' + data.error);
                }}
            }});
        """,
    )
    delete_button = Button(
        "Delete",
        cls="ce-btn ce-btn-danger",
        onclick=f"""
            fetch('/codeeditor/delete/{file}', {{method: 'POST'}})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        // Remove the deleted file from openedFiles array
                        openedFiles = openedFiles.filter(f => f !== '{file}');
                        updateOpenedFiles(openedFiles);
                        // Navigate to the index page after deleting
                        window.location = '/codeeditor/';
                    }}
                    else showErrorModal('Failed to delete file: ' + data.error);
                }});
        """,
    )
    # The tab strip. The open file's tab is rendered server-side so the strip
    # is never empty (no-JS observers, the moment before the script runs);
    # `renderTabs()` then replaces it with every tab from localStorage. Save
    # and Delete sit at the strip's right end, where VS Code puts its editor
    # actions.
    tab_bar = Div(cls="ce-tabs")(
        Div(
            id="tab-container",
            cls="ce-tab-list"
        )(
            Div(cls="editor-tab is-active")(
                _file_icon(file_name),
                Span(file_name),
                Button("×", cls="editor-tab-close", aria_label=f"Close {file_name}"),
            ),
            Script("""
                // Use sessionStorage to track if a session is active
                const SESSION_KEY = 'editor_session_active';
                const TABS_KEY = 'opened_files';
                const CODE_EXTENSIONS = """ + json.dumps(list(_CODE_EXTENSIONS)) + """;

                // Check if this is a fresh session
                if (!sessionStorage.getItem(SESSION_KEY)) {
                    // Clear localStorage tabs when starting a new session
                    localStorage.clear();
                    // Mark session as active
                    sessionStorage.setItem(SESSION_KEY, 'true');
                }

                // Store opened files in localStorage
                function updateOpenedFiles(files) {
                    localStorage.setItem(TABS_KEY, JSON.stringify(files));
                }

                // Get opened files from localStorage
                function getOpenedFiles() {
                    const files = localStorage.getItem(TABS_KEY);
                    return files ? JSON.parse(files) : [];
                }

                // Update tab name when file is renamed
                function updateTabOnRename(oldPath, newPath) {
                    let openedFiles = getOpenedFiles();
                    openedFiles = openedFiles.map(file => file === oldPath ? newPath : file);
                    updateOpenedFiles(openedFiles);
                }

                // Initialize opened files
                let openedFiles = getOpenedFiles();
                const currentFile = '""" + file + """';
                
                if (!openedFiles.includes(currentFile)) {
                    openedFiles.push(currentFile);
                    updateOpenedFiles(openedFiles);
                }

                // Render tabs
                function renderTabs() {
                    const container = document.getElementById('tab-container');
                    container.innerHTML = '';
                    
                    openedFiles.forEach(file => {
                        const tab = document.createElement('div');
                        tab.className = file === currentFile ? 'editor-tab is-active' : 'editor-tab';

                        // Same glyph as the explorer row; decorative only.
                        const name = file.split('/').pop();
                        const icon = document.createElement('i');
                        const isCode = CODE_EXTENSIONS.some(ext => name.toLowerCase().endsWith(ext));
                        icon.className = isCode ? 'ce-icon far fa-file-code ce-icon-code' : 'ce-icon far fa-file-alt';
                        icon.setAttribute('aria-hidden', 'true');

                        const fileName = document.createElement('span');
                        fileName.textContent = name;
                        fileName.onclick = () => {
                            if (file !== currentFile) {
                                window.location = '/codeeditor/' + file;
                            }
                        };
                        
                        const closeBtn = document.createElement('button');
                        closeBtn.className = 'editor-tab-close';
                        closeBtn.innerHTML = '×';
                        closeBtn.setAttribute('aria-label', 'Close ' + name);
                        closeBtn.onclick = (e) => {
                            e.stopPropagation();
                            openedFiles = openedFiles.filter(f => f !== file);
                            updateOpenedFiles(openedFiles);
                            
                            if (file === currentFile) {
                                // Navigate to the next available tab or index
                                if (openedFiles.length > 0) {
                                    window.location = '/codeeditor/' + openedFiles[0];
                                } else {
                                    window.location = '/codeeditor/';
                                }
                            } else {
                                renderTabs();
                            }
                        };
                        
                        tab.appendChild(icon);
                        tab.appendChild(fileName);
                        tab.appendChild(closeBtn);
                        container.appendChild(tab);
                    });
                }

                // Initial render
                renderTabs();
            """)
        ),
        Div(cls="ce-tab-actions")(save_button, delete_button),
    )
    title = Div(cls="ce-editor-title group")(
        Div(
            cls="ce-title-text",
            ondblclick="""
                this.nextElementSibling.classList.remove('hidden');
                this.classList.add('hidden');
                const input = this.nextElementSibling.querySelector('input');
                input.focus();
                input.select();
            """,
            role="button",
            tabindex="0",
            **{'aria-label': f"File name: {file}. Double-click to rename."}
        )(
            file,
            Span(cls="ce-rename-hint")("Double-click to rename"),
        ),
        Div(cls="hidden")(
            Input(
                type="text",
                value=file,
                cls="ce-input",
                onblur=f"""
                    const newName = this.value;
                    if (newName !== '{file}') {{
                        // First save the current content
                        const content = document.querySelector('textarea').value;
                        fetch('/codeeditor/save/{file}', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            body: JSON.stringify({{content: content}})
                        }})
                        .then(r => r.json())
                        .then(data => {{
                            if (data.success) {{
                                // After successful save, proceed with rename
                                return fetch('/codeeditor/rename/{file}?new_file=' + encodeURIComponent(newName), {{method: 'POST'}});
                            }} else {{
                                showErrorModal('Failed to save file: ' + data.error);
                            }}
                        }})
                        .then(r => r.json())
                        .then(data => {{
                            if (data.success) {{
                                // Update tab name before navigation
                                updateTabOnRename('{file}', newName);                                            
                                window.location = '/codeeditor/' + newName;
                            }} else {{
                                showErrorModal('Failed to rename: ' + data.error);
                            }}
                        }})
                        .catch(error => showErrorModal(error.message));
                    }}
                    this.parentElement.classList.add('hidden');
                    this.parentElement.previousElementSibling.classList.remove('hidden');
                """,
                onkeydown="if(event.key==='Enter')this.blur();if(event.key==='Escape'){this.value='"
                + file
                + "';this.blur();}",
            ),
        ),
    )
    main_screen = Div(cls="ce-editor")(
        tab_bar,
        editor_header(title),
        Div(cls="ce-editor-body")(
            Textarea(
                content,  # or "" for index() function
                id="editor",
                cls="ce-code",
                role="textbox",
                spellcheck="false",
                wrap="off",
                **{
                    "aria-label": f"Code editor - {file}",
                    "aria-multiline": "true",
                    "aria-describedby": "editor-description",
                    "aria-atomic": "true",
                    "aria-live": "off"
                }
            ),
            Div(
                id="editor-description",
                cls="sr-only"
            )(f"Code editor for editing {file}"),
            editor_script(f"""{{
                        mode: '{app.config.code_editor.mode}',
                        theme: '{current_editor_theme()}',
                        lineNumbers: true,
                        indentUnit: 4,
                        tabSize: 4,
                        indentWithTabs: false,
                        smartIndent: true,
                        lineWrapping: true,
                        screenReaderLabel: 'Code editor',
                        inputStyle: 'contenteditable',
                        role: 'textbox',
                        'aria-multiline': true,
                        'aria-atomic': true,
                        'aria-live': 'off',
                        announceMultiline: true,{_EDIT_KEYS}
                    }}"""),
        ),
        *editor_footer(),
    )
    # Facts, not decoration: CodeMirror is configured with `indentUnit: 4`,
    # and files are read and written in the platform default text encoding,
    # UTF-8 on the hosts this runs on.
    page = editor_page(side_bar, main_screen, status_bar("Spaces: 4", "UTF-8"))
    return Div(codeeditor_theme(), logo_title_container, page)

@app.post("/codeeditor/create_folder/{folder:path}")
def create_folder(folder: str):
    try:
        # Prevent creating folders with "Untitled-" prefix
        folder_name = os.path.basename(folder)
        # folder_path = os.path.join(current_dir, "files", folder)
        folder_path = os.path.join(current_dir, folder)
        # Be cautious! Path traversal attack prevention
        if not os.path.abspath(folder_path).startswith(os.path.abspath(current_dir)):
            return {"success": False, "error": "Invalid folder path."}
        # check whether the name has been occupied by another folder or file
        if os.path.exists(folder_path):
            return {"success": False, "error": "Name already occupied."}

        os.makedirs(folder_path, exist_ok=True)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/codeeditor/save/{file:path}")
def save_file(file: str, content: dict):
    try:
        # file_path = os.path.join(current_dir, "files", file)
        file_path = os.path.join(current_dir, file)
        # Be cautious! Path traversal attack prevention
        if not os.path.abspath(file_path).startswith(os.path.abspath(current_dir)):
            return {"success": False, "error": "Invalid file path."}
        # check if the parent directory exists: if not, create it
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(content["content"])
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/codeeditor/rename/{old_file:path}")
def rename_file(old_file: str, new_file: str):
    try:
        # old_path = os.path.join(current_dir, "files", old_file)
        # new_path = os.path.join(current_dir, "files", new_file)
        # Be cautious! Path traversal attack prevention
        old_path = os.path.join(current_dir, old_file)
        new_path = os.path.join(current_dir, new_file)
        if not os.path.abspath(old_path).startswith(os.path.abspath(current_dir)):
            return {"success": False, "error": "Invalid file path."}
        if not os.path.abspath(new_path).startswith(os.path.abspath(current_dir)):
            return {"success": False, "error": "Invalid file path."}
        if os.path.exists(new_path):
            return {"success": False, "error": "File already exists."}
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        shutil.move(old_path, new_path)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/codeeditor/delete/{file:path}")
def delete_file(file: str):
    try:
        # path = os.path.join(current_dir, "files", file)
        path = os.path.join(current_dir, file)
        # Be cautious! Path traversal attack prevention
        if not os.path.abspath(path).startswith(os.path.abspath(current_dir)):
            return {"success": False, "error": "Invalid file path."}
        if os.path.isdir(path):
            shutil.rmtree(path)
            # make sure the file exists
        elif os.path.exists(path):
            os.remove(path)
        else:
            return {"success": False, "error": "File not found. Are you trying to delete an unsaved file?"}
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/codeeditor/update_config")
async def update_config(request):
    try:
        data = await request.json()
        if data["type"] == "mode":
            app.config.code_editor.mode = data["value"]
        elif data["type"] == "theme":
            # The selector offers *design* themes. Pinning it per-app is what
            # makes the choice survive navigation: every page re-emits the
            # token block from the live config (`codeeditor_theme()`).
            app.config.code_editor.theme = data["value"]
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/codeeditor_all")
def get_all():
    """Used for rewards"""
    # return the file tree of the code editor
    files_root = current_dir
    file_tree = get_file_tree(files_root)
    # convert the file tree to a JSON object
    file_tree_json = json.dumps(file_tree, indent=4)
    # return the file tree as a JSON object
    return Response(content=file_tree_json, headers={"Content-Type": "application/json"})

def get_codeeditor_routes():
    return app.routes

if __name__ == "__main__":
    app.routes = get_codeeditor_routes()
    serve()