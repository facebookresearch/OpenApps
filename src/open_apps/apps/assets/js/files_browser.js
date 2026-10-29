/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * The Files browser's behaviour, loaded on every Files page.
 *
 * Right-click opens an item's own menu at the pointer; Escape and outside
 * clicks close it; double-clicking a row opens the file; list columns resize
 * and remember their widths. All delegated, so rows swapped in later need no
 * rebinding. The PDF reader is files_viewer.js.
 */
(function () {
    // A desktop viewer runs from wherever the header leaves it down to the
    // bottom of the window, behind the dock. Where that is depends on the
    // theme's type, so it is measured rather than guessed in rem.
    function fitViewer() {
        var viewer = document.querySelector('.fs-root:not(.is-phone) .fs-viewer');
        if (!viewer) { return; }
        var top = viewer.getBoundingClientRect().top + window.scrollY;
        viewer.style.setProperty('--fs-viewer-top', Math.round(top) + 'px');
    }
    fitViewer();
    window.addEventListener('resize', fitViewer);
    document.addEventListener('htmx:afterSettle', fitViewer);
    function closeAll(except) {
        document.querySelectorAll('.fs-menu[open]').forEach(function (m) {
            if (m !== except) { m.open = false; m.classList.remove('at-pointer'); }
        });
    }
    document.addEventListener('contextmenu', function (e) {
        var item = e.target.closest('[data-menu-host]');
        if (!item) { return; }
        var menu = item.querySelector('.fs-menu');
        if (!menu) { return; }
        e.preventDefault();
        closeAll(menu);
        menu.style.setProperty('--menu-x', e.clientX + 'px');
        menu.style.setProperty('--menu-y', e.clientY + 'px');
        menu.classList.add('at-pointer');
        menu.open = true;
        var first = menu.querySelector('.fs-menu-list a, .fs-menu-list button');
        if (first) { first.focus(); }
    });
    document.addEventListener('click', function (e) {
        var menu = e.target.closest('.fs-menu');
        closeAll(menu);
        if (menu && e.target.closest('summary')) { menu.classList.remove('at-pointer'); }
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            closeAll(null);
            var dialog = document.querySelector('#files-dialog dialog');
            if (dialog) { dialog.remove(); }
        }
    });
    // The attribute carries the file's store path, never a URL: the viewer
    // URL is built here under a fixed prefix, one encoded segment at a time,
    // so nothing in a file name can turn the navigation into another host or
    // a javascript: URL.
    document.addEventListener('dblclick', function (e) {
        var item = e.target.closest('[data-open-path]');
        if (!item) { return; }
        var segments = item.getAttribute('data-open-path').split('/').map(encodeURIComponent);
        window.location.assign('/files/view/' + segments.join('/'));
    });

    // Column dividers. Name is never sized directly: it takes what the fixed
    // columns leave, so moving a boundary trades width between its two
    // neighbours and the table never overflows. Widths are kept per view in
    // localStorage -- a person's preference, never app state.
    var MIN = { name: 128, where: 56, date: 64, size: 48, kind: 56 };
    function keysOf(table) { return (table.dataset.cols || '').split(','); }
    function head(table, key) { return table.querySelector('th[data-col-key="' + key + '"]'); }
    function widthOf(table, key) { return head(table, key).getBoundingClientRect().width; }
    function storeKey(table) { return 'oa-files-columns:' + table.dataset.columnsKey; }
    function setWidth(table, key, px) {
        if (key !== 'name') { table.style.setProperty('--w-' + key, Math.round(px) + 'px'); }
    }
    function save(table) {
        var widths = {};
        keysOf(table).forEach(function (key) {
            if (key !== 'name') { widths[key] = Math.round(widthOf(table, key)); }
        });
        try { localStorage.setItem(storeKey(table), JSON.stringify(widths)); } catch (err) {}
    }
    // A view nobody has resized opens fitted: every column to its contents,
    // so Name shows as much of each filename as the window allows. Not saved
    // -- it follows the contents until someone drags a divider.
    function restoreColumns() {
        document.querySelectorAll('.fs-table[data-cols]').forEach(function (table) {
            var widths = null;
            try { widths = JSON.parse(localStorage.getItem(storeKey(table)) || 'null'); } catch (err) {}
            if (!widths) { fitOthers(table); return; }
            Object.keys(widths).forEach(function (key) {
                if (head(table, key)) { setWidth(table, key, widths[key]); }
            });
        });
    }
    // Trade `delta` px from the right neighbour to the left one, within limits.
    function move(table, left, right, startLeft, startRight, delta) {
        delta = Math.max(MIN[left] - startLeft, Math.min(startRight - MIN[right], delta));
        setWidth(table, left, startLeft + delta);
        setWidth(table, right, startRight - delta);
    }
    // The widest content in a column, header included. text-overflow only
    // paints the ellipsis, so a range still measures the full text.
    function contentWidth(table, key) {
        var index = keysOf(table).indexOf(key), widest = 0, range = document.createRange();
        var cells = [head(table, key).querySelector('.fs-col-label')];
        table.querySelectorAll('tbody tr').forEach(function (row) { cells.push(row.children[index]); });
        cells.forEach(function (cell) {
            if (!cell) { return; }
            range.selectNodeContents(cell);
            var box = cell.closest('td, th'), style = getComputedStyle(box);
            var width = range.getBoundingClientRect().width + parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
            widest = Math.max(widest, width);
        });
        return Math.ceil(widest) + 2;
    }
    function fitOthers(table) {
        keysOf(table).forEach(function (k) {
            if (k !== 'name') { setWidth(table, k, Math.max(MIN[k], contentWidth(table, k))); }
        });
    }
    function fit(table, key) {
        if (key === 'name') {
            // "Show me the whole name": every other column down to its contents.
            fitOthers(table);
        } else {
            var room = widthOf(table, key) + widthOf(table, 'name') - MIN.name;
            setWidth(table, key, Math.max(MIN[key], Math.min(room, contentWidth(table, key))));
        }
        save(table);
    }
    function neighbours(handle) {
        var table = handle.closest('.fs-table'), keys = keysOf(table), left = handle.dataset.col;
        return { table: table, left: left, right: keys[keys.indexOf(left) + 1] };
    }
    document.addEventListener('pointerdown', function (e) {
        var handle = e.target.closest('.fs-col-resize');
        if (!handle || e.button !== 0) { return; }
        e.preventDefault();
        var n = neighbours(handle), x = e.clientX;
        var startLeft = widthOf(n.table, n.left), startRight = widthOf(n.table, n.right);
        handle.setPointerCapture(e.pointerId);
        handle.classList.add('is-dragging');
        n.table.classList.add('is-resizing');
        function onMove(ev) { move(n.table, n.left, n.right, startLeft, startRight, ev.clientX - x); }
        function onUp() {
            handle.removeEventListener('pointermove', onMove);
            handle.removeEventListener('pointerup', onUp);
            handle.classList.remove('is-dragging');
            n.table.classList.remove('is-resizing');
            save(n.table);
        }
        handle.addEventListener('pointermove', onMove);
        handle.addEventListener('pointerup', onUp);
    });
    document.addEventListener('dblclick', function (e) {
        var handle = e.target.closest('.fs-col-resize');
        if (handle) { fit(handle.closest('.fs-table'), handle.dataset.col); }
    });
    document.addEventListener('keydown', function (e) {
        var handle = e.target.closest && e.target.closest('.fs-col-resize');
        if (!handle) { return; }
        var n = neighbours(handle);
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            e.preventDefault();
            var step = (e.shiftKey ? 48 : 16) * (e.key === 'ArrowLeft' ? -1 : 1);
            move(n.table, n.left, n.right, widthOf(n.table, n.left), widthOf(n.table, n.right), step);
            save(n.table);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            fit(n.table, n.left);
        }
    });
    restoreColumns();
    document.addEventListener('htmx:afterSettle', restoreColumns);
})();
