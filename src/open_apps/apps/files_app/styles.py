"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The Files app's stylesheet, kept out of ``main.py`` so the handlers read on
their own. Imported once; nothing here depends on the request.

Shared tokens only (resolved per request by ``theme_style()``), scoped to
``.fs-root`` like every app's sheet.
"""
from fasthtml.common import Style

styles = Style("""
    html, body { background-color: var(--color-bg); }
    .fs-root {
        --pico-background-color: var(--color-bg);
        --pico-color: var(--color-fg);
        --pico-muted-color: var(--color-muted);
        --pico-muted-border-color: var(--color-border);
        --pico-primary: var(--color-primary);
        --pico-primary-background: var(--color-primary);
        --pico-primary-border: var(--color-primary);
        --pico-primary-hover-background: var(--color-primary-hover);
        --pico-primary-inverse: var(--color-on-primary);
        --pico-card-background-color: var(--color-bg);
        --pico-card-sectioning-background-color: var(--color-surface);
        --pico-card-border-color: var(--color-border);
        --pico-form-element-background-color: var(--color-bg);
        --pico-form-element-border-color: var(--color-border);
        --pico-form-element-active-border-color: var(--color-primary);
        --pico-form-element-color: var(--color-fg);
        --pico-form-element-placeholder-color: var(--color-muted);
        --pico-form-element-focus-color: var(--color-primary);
        --pico-h1-color: var(--color-fg);
        --pico-h2-color: var(--color-fg);
        --pico-h3-color: var(--color-fg);
        font-family: var(--font-family);
        color: var(--color-fg);
        max-width: 1280px;
        margin: 0 auto;
        padding: calc(var(--space) * 2) calc(var(--space) * 3)
                 calc(var(--space) * 2 + var(--oa-dock-space, 0px));
    }
    .fs-root a { text-decoration: none; color: inherit; }
    /* Pico gives canvases `overflow: visible`, which Chromium warns about on
       every page drawn; nothing here paints outside its box. */
    .fs-root canvas { overflow: clip; }
    .fs-root .sr-only {
        position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
        overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
    }
    .fs-top { display: flex; align-items: center; gap: var(--space); }
    .fs-top > div, .fs-top > a:first-child { margin-bottom: 0 !important; }
    .fs-top h1 { color: var(--color-fg); }

    /* Buttons. Links styled as buttons stay links in the tree. */
    .fs-btn {
        display: inline-flex; align-items: center; gap: calc(var(--space) * 0.5);
        width: auto; margin: 0;
        padding: calc(var(--space) * 0.5) calc(var(--space) * 1.25);
        border: 1px solid var(--color-border); border-radius: var(--radius);
        background: var(--color-bg); color: var(--color-fg);
        font: inherit; font-size: var(--font-size-sm); font-weight: 500; line-height: 1.25;
        white-space: nowrap; cursor: pointer;
    }
    .fs-btn:hover { border-color: var(--color-muted); }
    .fs-btn.is-primary { background: var(--color-primary); border-color: var(--color-primary); color: var(--color-on-primary); }
    .fs-btn.is-primary:hover { background: var(--color-primary-hover); border-color: var(--color-primary-hover); }
    .fs-btn.is-danger { color: var(--color-danger); }
    .fs-btn.is-danger[type="submit"] { background: var(--color-danger); border-color: var(--color-danger); color: var(--color-btn-fg); }
    .fs-btn[aria-disabled="true"] { opacity: 0.5; pointer-events: none; }
    .fs-btn:focus { box-shadow: none; }
    .fs-root :is(a, button, summary, input):focus-visible {
        outline: 2px solid var(--color-accent); outline-offset: 2px;
    }
    .fs-root form { margin: 0; }

    /* The window: sidebar | toolbar over content | preview. */
    .fs-window {
        display: grid;
        grid-template-columns: 12.5rem minmax(0, 1fr) 17.5rem;
        grid-template-rows: auto minmax(0, 1fr);
        grid-template-areas: "side bar bar" "side main preview";
        margin-top: calc(var(--space) * 1.5);
        border: 1px solid var(--color-border);
        border-radius: calc(var(--radius) * 1.5);
        overflow: hidden;
        height: calc(100vh - 7.5rem - var(--oa-titlebar-h, 0px) - var(--oa-dock-space, 0px));
        min-height: 28rem;
        background: var(--color-bg);
    }
    .fs-window.no-preview { grid-template-columns: 12.5rem minmax(0, 1fr); grid-template-areas: "side bar" "side main"; }

    .fs-sidebar {
        grid-area: side; overflow-y: auto;
        padding: calc(var(--space) * 1.25) var(--space);
        background: var(--color-surface);
        border-right: 1px solid var(--color-border);
    }
    /* Pico lays every <nav> out as a horizontal bar; this one is a column. */
    .fs-root .fs-sidebar { display: block; padding: calc(var(--space) * 1.25) var(--space); }
    .fs-root .fs-sidebar > ul { display: flex; flex-direction: column; align-items: stretch; gap: 1px; margin: 0; padding: 0; list-style: none; }
    .fs-root .fs-sidebar li { display: block; margin: 0; padding: 0; list-style: none; }
    .fs-side-link {
        display: flex; align-items: center; gap: calc(var(--space) * 0.75);
        padding: calc(var(--space) * 0.45) calc(var(--space) * 0.75);
        border-radius: var(--radius);
        color: var(--color-fg); font-size: var(--font-size-sm);
    }
    .fs-side-link:hover { background: color-mix(in srgb, var(--color-fg) 6%, transparent); }
    .fs-side-link[aria-current="page"] { background: color-mix(in srgb, var(--color-primary) 16%, transparent); font-weight: 600; }
    .fs-side-link .fs-glyph { color: var(--color-primary); flex: none; }
    .fs-side-count { margin-left: auto; color: var(--color-muted); font-variant-numeric: tabular-nums; font-weight: 400; }
    .fs-side-rule { height: 1px; margin: var(--space) calc(var(--space) * 0.75); background: var(--color-border); }

    .fs-toolbar {
        grid-area: bar;
        display: flex; align-items: center; gap: var(--space); flex-wrap: wrap;
        padding: calc(var(--space) * 0.9) calc(var(--space) * 1.5);
        border-bottom: 1px solid var(--color-border);
    }
    .fs-root .fs-crumbs { display: flex; align-items: center; min-width: 0; margin-right: auto; }
    .fs-root .fs-crumbs ol { display: flex; align-items: center; gap: calc(var(--space) * 0.4); margin: 0; padding: 0; list-style: none; }
    .fs-root .fs-crumbs li { list-style: none; margin: 0; padding: 0; display: flex; align-items: center; gap: calc(var(--space) * 0.4); }
    .fs-crumbs a { color: var(--color-muted); }
    .fs-crumbs a:hover { color: var(--color-fg); }
    .fs-crumbs [aria-current="page"] { color: var(--color-fg); font-weight: 600; font-size: 1.05rem; }
    .fs-crumb-sep { color: var(--color-muted); }
    /* Pico stretches [role=search] and [role=group] into full-width input
       groups; in a toolbar they size to their content. */
    .fs-root .fs-search { display: flex; width: auto; margin: 0; box-shadow: none; }
    /* Pico rounds search inputs into a pill; in the toolbar it matches the
       segment and buttons beside it. */
    .fs-root .fs-search input[type="search"] {
        width: 13rem; height: auto; margin: 0;
        border-radius: var(--radius);
        padding-block: calc(var(--space) * 0.45);
        font-size: var(--font-size-sm);
    }
    .fs-root .fs-segment {
        display: inline-flex; width: auto; margin: 0; box-shadow: none;
        border: 1px solid var(--color-border); border-radius: var(--radius); overflow: hidden;
    }
    .fs-segment a {
        display: inline-flex; align-items: center; padding: calc(var(--space) * 0.4) calc(var(--space) * 0.8);
        font-size: var(--font-size-sm); color: var(--color-muted);
    }
    .fs-segment a + a { border-left: 1px solid var(--color-border); }
    .fs-segment a[aria-pressed="true"] { background: var(--color-surface); color: var(--color-fg); font-weight: 600; }
    .fs-upload input[type="file"] { position: absolute; width: 1px; height: 1px; opacity: 0; }
    .fs-upload label { margin: 0; }
    .fs-upload:focus-within label { outline: 2px solid var(--color-accent); outline-offset: 2px; }

    /* Menus: a <details> per item -- the row's "..." button, and what a
       right-click opens at the pointer. */
    .fs-menu { position: relative; margin: 0; }
    .fs-root details.fs-sort > summary.fs-btn {
        width: auto; height: auto; border: 1px solid var(--color-border); color: var(--color-fg);
        padding: calc(var(--space) * 0.4) calc(var(--space) * 1);
    }
    .fs-root .fs-sort > summary.fs-btn::after { display: none; }
    .fs-root .fs-sort .fs-menu-list { right: auto; left: 0; }
    .fs-root .fs-sort .fs-menu-list a[aria-current="true"] { font-weight: 600; }
    .fs-menu > summary {
        list-style: none; display: inline-flex; align-items: center; justify-content: center;
        width: 1.9rem; height: 1.9rem; margin: 0; padding: 0;
        border-radius: var(--radius); color: var(--color-muted); cursor: pointer;
    }
    .fs-menu > summary::-webkit-details-marker { display: none; }
    /* Pico spaces an open <details> summary from its content; a menu's content
       floats, so that margin only pushed the row taller while the menu was open. */
    .fs-root details.fs-menu[open] > summary { margin-bottom: 0; }
    .fs-menu > summary::after { display: none; }
    .fs-menu > summary:hover, .fs-menu[open] > summary { background: color-mix(in srgb, var(--color-fg) 8%, transparent); color: var(--color-fg); }
    .fs-menu-list {
        position: absolute; right: 0; top: calc(100% + 2px); z-index: 20;
        min-width: 11rem; margin: 0; padding: calc(var(--space) * 0.4);
        list-style: none;
        background: var(--color-bg);
        border: 1px solid var(--color-border); border-radius: var(--radius);
        box-shadow: 0 8px 24px color-mix(in srgb, var(--color-fg) 18%, transparent);
    }
    .fs-menu.at-pointer .fs-menu-list { position: fixed; right: auto; top: var(--menu-y); left: var(--menu-x); }
    .fs-sidebar .fs-menu > summary { display: none; }
    /* Pico lays every list inside a <nav> out as a row; a menu is a column. */
    .fs-root .fs-sidebar .fs-menu-list { display: block; }
    .fs-root .fs-sidebar .fs-menu-list li { display: block; padding: 0; }
    .fs-menu-list li { list-style: none; margin: 0; padding: 0; }
    .fs-menu-list :is(a, button) {
        display: block; width: 100%; margin: 0; text-align: left;
        padding: calc(var(--space) * 0.45) calc(var(--space) * 0.9);
        border: 0; border-radius: calc(var(--radius) * 0.6);
        background: none; color: var(--color-fg);
        font: inherit; font-size: var(--font-size-sm); line-height: 1.3; cursor: pointer;
    }
    .fs-menu-list :is(a, button):hover { background: var(--color-primary); color: var(--color-on-primary); }
    .fs-menu-list .is-danger { color: var(--color-danger); }
    .fs-menu-list .fs-menu-rule { height: 1px; margin: calc(var(--space) * 0.35) 0; background: var(--color-border); }

    /* Content pane. */
    .fs-content { grid-area: main; overflow: auto; min-height: 0; }
    .fs-table { width: 100%; margin: 0; border-collapse: collapse; font-size: var(--font-size-sm); table-layout: fixed; }
    /* Name takes what the fixed columns leave; the dividers move the rest. */
    .fs-table col.is-col-where { width: var(--w-where, 7em); }
    .fs-table col.is-col-date { width: var(--w-date, 7.5em); }
    .fs-table col.is-col-size { width: var(--w-size, 4.5em); }
    .fs-table col.is-col-kind { width: var(--w-kind, 7.5em); }
    .fs-table col.is-col-menu { width: 2.75rem; }
    /* Inside its own header: each sticky header is a stacking context, so a
       divider hanging past the edge would sit under the next header. */
    .fs-col-resize {
        position: absolute; top: 0; right: 0; z-index: 2;
        width: 9px; height: 100%; cursor: col-resize; touch-action: none;
    }
    .fs-col-resize::after {
        content: ""; position: absolute; top: 25%; bottom: 25%; right: 0; width: 1px;
        background: var(--color-border);
    }
    .fs-col-resize:hover::after, .fs-col-resize:focus-visible::after, .fs-col-resize.is-dragging::after {
        top: 0; bottom: 0; width: 2px; background: var(--color-primary);
    }
    .fs-col-resize:focus-visible { outline: none; }
    .fs-table.is-resizing { cursor: col-resize; user-select: none; }
    .fs-table th {
        white-space: nowrap;
        position: sticky; top: 0; z-index: 1;
        padding: calc(var(--space) * 0.5) calc(var(--space) * 1.5);
        background: var(--color-bg); color: var(--color-muted);
        font-weight: 500; text-align: left;
        border-bottom: 1px solid var(--color-border);
    }
    .fs-table td {
        padding: 0 calc(var(--space) * 1.5);
        background: transparent; color: var(--color-fg);
        border-bottom: 1px solid color-mix(in srgb, var(--color-border) 60%, transparent);
        white-space: nowrap;
    }
    /* Every cell truncates at its column's width; the full value is a resize away. */
    .fs-table td { overflow: hidden; text-overflow: ellipsis; }
    /* Except the menu cell: its dropdown hangs below the row and would be clipped away. */
    .fs-table td.is-menu { overflow: visible; }
    /* The facts are short and need little air; what they don't use goes to Name. */
    .fs-table th:not(:first-child), .fs-table td:not(:first-child) { padding-inline: calc(var(--space) * 0.75); }
    .fs-name > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .fs-table .is-num { text-align: right; font-variant-numeric: tabular-nums; }
    .fs-table .is-muted { color: var(--color-muted); }
    .fs-table .is-menu { width: 2.5rem; padding-right: var(--space); }
    .fs-row:hover td { background: color-mix(in srgb, var(--color-fg) 4%, transparent); }
    .fs-row[aria-selected="true"] td { background: color-mix(in srgb, var(--color-primary) 14%, transparent); }
    .fs-name {
        display: flex; align-items: center; gap: calc(var(--space) * 0.9);
        padding: calc(var(--space) * 0.6) 0; font-weight: 500;
        overflow: hidden; text-overflow: ellipsis;
    }
    .fs-name .fs-glyph { flex: none; color: var(--color-muted); }
    .fs-row.is-folder .fs-glyph { color: var(--color-primary); }
    .fs-empty { padding: calc(var(--space) * 4) calc(var(--space) * 2); text-align: center; color: var(--color-muted); }

    .fs-grid {
        display: grid; grid-template-columns: repeat(auto-fill, minmax(8rem, 1fr));
        gap: calc(var(--space) * 2); padding: calc(var(--space) * 2);
        margin: 0; list-style: none;
    }
    .fs-tile { position: relative; list-style: none; margin: 0; }
    .fs-tile > a {
        display: flex; flex-direction: column; align-items: center; gap: var(--space);
        padding: var(--space); border-radius: var(--radius); text-align: center;
    }
    .fs-tile > a:hover { background: color-mix(in srgb, var(--color-fg) 5%, transparent); }
    .fs-tile[aria-selected="true"] > a { background: color-mix(in srgb, var(--color-primary) 14%, transparent); }
    .fs-tile-name { font-size: var(--font-size-sm); font-weight: 500; line-height: 1.3; overflow-wrap: anywhere; }
    .fs-tile-meta { font-size: var(--font-size-sm); color: var(--color-muted); }
    .fs-tile .fs-menu { position: absolute; top: calc(var(--space) * 0.5); right: calc(var(--space) * 0.5); }
    .fs-tile .fs-menu > summary { background: var(--color-bg); }
    .fs-tile:not(:hover):not(:focus-within) .fs-menu:not([open]) > summary { opacity: 0; }

    /* The thumbnail: a sheet of paper on the surface, the one thing allowed
       a shadow. Page images are drawn by files_viewer.js. */
    .fs-thumb {
        display: flex; align-items: center; justify-content: center;
        width: 6.5rem; height: 8.4rem; flex: none;
        color: var(--color-muted);
    }
    .fs-thumb canvas, .fs-thumb img {
        display: block; max-width: 100%; max-height: 100%;
        background: var(--color-bg);
        box-shadow: 0 1px 2px color-mix(in srgb, var(--color-fg) 22%, transparent),
                    0 4px 14px color-mix(in srgb, var(--color-fg) 10%, transparent);
    }
    .fs-thumb.is-large { width: 100%; height: 10rem; }

    /* Preview pane. */
    .fs-preview {
        grid-area: preview; overflow-y: auto;
        display: flex; flex-direction: column; gap: calc(var(--space) * 1.2);
        padding: calc(var(--space) * 2);
        background: var(--color-surface);
        border-left: 1px solid var(--color-border);
    }
    .fs-preview-name { margin: 0; font-size: 1.05rem; font-weight: 600; line-height: 1.35; overflow-wrap: anywhere; }
    .fs-preview-kind { margin: 0.15rem 0 0; color: var(--color-muted); font-size: var(--font-size-sm); }
    .fs-facts { display: grid; grid-template-columns: auto 1fr; gap: calc(var(--space) * 0.5) calc(var(--space) * 1.5); margin: 0; font-size: var(--font-size-sm); }
    .fs-facts dt { color: var(--color-muted); font-weight: 400; }
    .fs-facts dd { margin: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
    .fs-actions { display: grid; grid-template-columns: 1fr 1fr; gap: calc(var(--space) * 0.6); }
    .fs-actions > :first-child { grid-column: 1 / -1; justify-content: center; }
    .fs-actions .fs-btn { width: 100%; justify-content: center; }
    .fs-actions form { display: contents; }
    .fs-preview-empty { margin: auto 0; text-align: center; color: var(--color-muted); font-size: var(--font-size-sm); }

    /* Recently Deleted. */
    .fs-trash-head {
        display: flex; align-items: center; justify-content: space-between; gap: var(--space);
        padding: calc(var(--space) * 1) calc(var(--space) * 1.5);
        background: var(--color-surface);
        border-bottom: 1px solid var(--color-border);
        font-size: var(--font-size-sm); color: var(--color-muted);
    }
    .fs-days { font-variant-numeric: tabular-nums; }
    .fs-days.is-soon { color: var(--color-danger); font-weight: 600; }
    .fs-row-actions { display: flex; justify-content: flex-end; gap: calc(var(--space) * 0.5); }

    /* Dialogs. */
    .fs-dialog article { width: min(28rem, 92vw); }
    .fs-dialog footer { display: flex; justify-content: flex-end; gap: var(--space); }
    .fs-root .fs-dialog footer .fs-btn { width: auto; margin: 0; }
    .fs-root .fs-dialog h3 { font-size: 1.15rem; line-height: 1.35; overflow-wrap: anywhere; }
    .fs-dialog label { font-size: var(--font-size-sm); color: var(--color-muted); }
    .fs-info { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: calc(var(--space) * 0.5) calc(var(--space) * 1.5); margin: 0 0 calc(var(--space) * 1.5); font-size: var(--font-size-sm); }
    .fs-info dt { color: var(--color-muted); font-weight: 400; }
    .fs-info dd { margin: 0; overflow-wrap: anywhere; }
    .fs-dialog input[type="text"] { margin-top: 0.25rem; }
    .fs-dialog ul { list-style: none; margin: 0; padding: 0; }
    .fs-dialog li { list-style: none; }
    .fs-dialog .fs-choice { display: flex; align-items: center; gap: var(--space); margin: 0.25rem 0; color: var(--color-fg); }
    .fs-error { margin: var(--space) 0 0; color: var(--color-danger); font-size: var(--font-size-sm); }

    /* Viewer: a document on a quiet well, with its page rail. */
    .fs-viewer {
        display: grid;
        grid-template-columns: 9.5rem minmax(0, 1fr);
        grid-template-rows: auto minmax(0, 1fr);
        grid-template-areas: "bar bar" "thumbs pages";
        margin-top: calc(var(--space) * 1.5);
        border: 1px solid var(--color-border);
        border-radius: calc(var(--radius) * 1.5);
        overflow: hidden;
        /* Down to the bottom of the browser, behind the dock, the way Preview
           fills the screen: stopping above the dock cut a letter page off at
           about two thirds on a landscape laptop. --fs-viewer-top is measured
           by BROWSER_SCRIPT; .fs-pages pads its end by the dock's height so
           the last page still scrolls clear of it. */
        height: calc(100vh - var(--fs-viewer-top, 7.5rem));
        border-bottom-left-radius: 0; border-bottom-right-radius: 0;
        min-height: 28rem;
        background: var(--color-surface);
    }
    /* The viewer fills the window, so the page around it must not scroll too:
       one scrollbar, inside the document, like Preview. */
    html:has(.fs-root:not(.is-phone) .fs-viewer) { overflow: hidden; }
    .fs-viewer.is-plain { grid-template-columns: minmax(0, 1fr); grid-template-areas: "bar" "pages"; }
    .fs-viewer-bar {
        grid-area: bar;
        display: flex; align-items: center; gap: var(--space); flex-wrap: wrap;
        padding: calc(var(--space) * 0.9) calc(var(--space) * 1.5);
        background: var(--color-bg);
        border-bottom: 1px solid var(--color-border);
    }
    .fs-viewer-bar form { display: inline-flex; }
    .fs-viewer-title { margin: 0 auto 0 0; min-width: 0; font-size: 1rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .fs-viewer-back { display: inline-flex; align-items: center; gap: 0.2rem; color: var(--color-accent) !important; font-weight: 500; font-size: var(--font-size-sm); }
    .fs-viewer-back svg { transform: rotate(180deg); }
    .fs-pager { display: inline-flex; align-items: center; gap: calc(var(--space) * 0.5); font-size: var(--font-size-sm); }
    .fs-pager output { min-width: 6.5rem; text-align: center; color: var(--color-muted); font-variant-numeric: tabular-nums; }
    .fs-icon-btn {
        display: inline-flex; align-items: center; justify-content: center;
        width: 2rem; height: 2rem; margin: 0; padding: 0;
        border: 1px solid var(--color-border); border-radius: var(--radius);
        background: var(--color-bg); color: var(--color-fg); font: inherit; font-weight: 600; cursor: pointer;
    }
    .fs-icon-btn.is-up svg { transform: rotate(-90deg); }
    .fs-icon-btn.is-down svg { transform: rotate(90deg); }
    .fs-thumbs {
        grid-area: thumbs; overflow-y: auto;
        display: flex; flex-direction: column; align-items: center; gap: calc(var(--space) * 1.5);
        margin: 0; padding: calc(var(--space) * 1.5) var(--space);
        list-style: none;
        border-right: 1px solid var(--color-border);
    }
    .fs-thumbs li { list-style: none; margin: 0; }
    .fs-thumbs button {
        display: flex; flex-direction: column; align-items: center; gap: 0.3rem;
        margin: 0; padding: 0.3rem; border: 2px solid transparent; border-radius: var(--radius);
        background: none; color: var(--color-muted); font: inherit; font-size: var(--font-size-sm); cursor: pointer;
    }
    .fs-thumbs button[aria-current="true"] { border-color: var(--color-primary); color: var(--color-fg); }
    .fs-thumbs canvas { display: block; width: 6.5rem; background: var(--color-bg); box-shadow: 0 1px 3px color-mix(in srgb, var(--color-fg) 25%, transparent); }
    .fs-pages {
        grid-area: pages; overflow: auto;
        display: flex; flex-direction: column; align-items: center; gap: calc(var(--space) * 3);
        padding: calc(var(--space) * 3);
        padding-bottom: calc(var(--space) * 3 + var(--oa-dock-space, 0px));
    }
    .fs-page {
        position: relative; flex: none;
        --scale-round-x: 1px; --scale-round-y: 1px;
        background: var(--color-bg);
        box-shadow: 0 1px 3px color-mix(in srgb, var(--color-fg) 22%, transparent),
                    0 10px 30px color-mix(in srgb, var(--color-fg) 12%, transparent);
    }
    .fs-page canvas { display: block; }
    .fs-image { max-width: 100%; max-height: 100%; object-fit: contain; box-shadow: 0 6px 24px color-mix(in srgb, var(--color-fg) 15%, transparent); }
    .fs-text {
        width: min(48rem, 100%); margin: 0; padding: calc(var(--space) * 3);
        background: var(--color-bg); color: var(--color-fg);
        border: 1px solid var(--color-border); border-radius: var(--radius);
        font-family: var(--font-mono); font-size: var(--font-size-sm); line-height: 1.6;
        white-space: pre-wrap; overflow-wrap: anywhere;
    }
    .fs-text code { padding: 0; background: none; color: inherit; font: inherit; white-space: inherit; }
    .fs-text .tok-keyword { color: var(--color-syntax-keyword, var(--color-fg)); }
    .fs-text .tok-control { color: var(--color-syntax-control, var(--color-fg)); }
    .fs-text .tok-string { color: var(--color-syntax-string, var(--color-fg)); }
    .fs-text .tok-number { color: var(--color-syntax-number, var(--color-fg)); }
    .fs-text .tok-comment { color: var(--color-syntax-comment, var(--color-fg)); }
    .fs-text .tok-function { color: var(--color-syntax-function, var(--color-fg)); }
    .fs-text .tok-type { color: var(--color-syntax-type, var(--color-fg)); }
    .fs-text .tok-property { color: var(--color-syntax-property, var(--color-fg)); }
    .fs-text .tok-constant { color: var(--color-syntax-constant, var(--color-fg)); }
    .fs-text .tok-tag { color: var(--color-syntax-tag, var(--color-fg)); }
    .fs-details { width: min(28rem, 100%); margin: auto; padding: calc(var(--space) * 3); background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius); }
    .fs-viewer-status { margin: auto; color: var(--color-muted); }

    /* pdf.js text layer: the page's words, transparent, laid over the canvas
       so they can be selected, found and read by the accessibility tree.
       Mirrors pdf.js's own pdf_viewer.css for the properties TextLayer sets. */
    .fs-page .textLayer {
        position: absolute; inset: 0; overflow: clip; opacity: 1; line-height: 1;
        text-align: initial; text-size-adjust: none; -webkit-text-size-adjust: none;
        forced-color-adjust: none; transform-origin: 0 0; caret-color: CanvasText; z-index: 0;
        --min-font-size: 1;
        --text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
        --min-font-size-inv: calc(1 / var(--min-font-size));
    }
    .fs-page .textLayer :is(span, br) {
        color: transparent; position: absolute; white-space: pre; cursor: text; transform-origin: 0% 0%;
    }
    .fs-page .textLayer > :not(.markedContent), .fs-page .textLayer .markedContent span:not(.markedContent) {
        z-index: 1;
        --font-height: 0;
        font-size: calc(var(--text-scale-factor) * var(--font-height));
        --scale-x: 1; --rotate: 0deg;
        transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
    }
    .fs-page .textLayer .markedContent { display: contents; }
    .fs-page .textLayer ::selection { background: color-mix(in srgb, var(--color-accent) 35%, transparent); }

    /* Phone: one pane, full-width rows. */
    .fs-root.is-phone { padding: var(--space) var(--space) calc(var(--space) + var(--oa-dock-space, 0px)); }
    .fs-root.is-phone .fs-window { grid-template-columns: minmax(0, 1fr); grid-template-areas: "bar" "main"; height: auto; min-height: 0; border-radius: var(--radius); }
    .fs-root.is-phone .fs-search input[type="search"] { width: 100%; }
    .fs-root.is-phone .fs-search { flex: 1 1 100%; order: 3; }
    .fs-root.is-phone .fs-table .is-kind, .fs-root.is-phone .fs-table .is-date { display: none; }
    /* One pane, no dividers: the phone keeps the automatic layout. */
    .fs-root.is-phone .fs-table { table-layout: auto; }
    .fs-root.is-phone .fs-table col { width: auto; }
    .fs-root.is-phone .fs-col-resize { display: none; }
    .fs-root.is-phone .fs-viewer { grid-template-columns: minmax(0, 1fr); grid-template-areas: "bar" "pages"; height: auto; min-height: 70vh; }
    .fs-root.is-phone .fs-thumbs { display: none; }
    .fs-root.is-phone .fs-pages { padding: var(--space); }
    @media (prefers-reduced-motion: no-preference) {
        .fs-page canvas, .fs-thumb canvas { animation: fs-paper 160ms ease-out; }
        @keyframes fs-paper { from { opacity: 0; } to { opacity: 1; } }
    }
""")
