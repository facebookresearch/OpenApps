/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * The Files app's PDF rendering, on the vendored pdf.js.
 *
 * Two jobs:
 *
 * - `#files-viewer[data-src]`: the full viewer. Every page is a canvas with
 *   pdf.js's text layer over it, so the document's words are in the DOM and
 *   the accessibility tree. That is the point of rendering PDFs ourselves:
 *   headless Chromium turns a PDF navigation into a download, and in a headed
 *   browser the native viewer's text never reaches an agent. Thumbnails, page
 *   up/down and zoom work like Preview.
 *
 * - `[data-pdf-thumb]`: a first-page picture for grid tiles and the preview
 *   pane. Canvas only, no text layer. A folder of PDFs would otherwise put
 *   every document's text into the page; reading a file means opening it.
 *
 * Same-origin only: the library and its worker come from /assets/vendor, and
 * the files from /files/raw. Nothing here reaches the network beyond that.
 */
import * as pdfjsLib from '/assets/vendor/pdf-6.4.299.min.js';

pdfjsLib.GlobalWorkerOptions.workerSrc = '/assets/vendor/pdf.worker-6.4.299.min.js';

const ZOOM_STEPS = [0.5, 0.67, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3];
const THUMB_WIDTH = 104;

function open(url) {
    // isEvalSupported off: the document is data, never code.
    return pdfjsLib.getDocument({ url, isEvalSupported: false });
}

async function drawPage(page, canvas, scale) {
    const viewport = page.getViewport({ scale });
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;
    const transform = ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null;
    await page.render({ canvas, canvasContext: canvas.getContext('2d'), viewport, transform }).promise;
    return viewport;
}

// --- Thumbnails in the browser ---------------------------------------------

async function drawThumb(host) {
    if (host.dataset.drawn) { return; }
    host.dataset.drawn = '1';
    try {
        const task = open(host.dataset.pdfThumb);
        const pdf = await task.promise;
        const page = await pdf.getPage(1);
        const box = host.getBoundingClientRect();
        const base = page.getViewport({ scale: 1 });
        const scale = Math.min((box.width || 104) / base.width, (box.height || 134) / base.height);
        const canvas = document.createElement('canvas');
        await drawPage(page, canvas, scale);
        host.replaceChildren(canvas);
        task.destroy();
    } catch (err) {
        // Leave the file glyph in place; the file still opens from its row.
        console.warn('files: thumbnail failed', err);
    }
}

function initThumbs() {
    const hosts = document.querySelectorAll('[data-pdf-thumb]');
    if (!hosts.length) { return; }
    if (!('IntersectionObserver' in window)) { hosts.forEach(drawThumb); return; }
    const seen = new IntersectionObserver((entries) => {
        entries.forEach((e) => {
            if (e.isIntersecting) { seen.unobserve(e.target); drawThumb(e.target); }
        });
    }, { rootMargin: '200px' });
    hosts.forEach((h) => seen.observe(h));
}

// --- The viewer --------------------------------------------------------------

async function initViewer(root) {
    const pagesEl = root.querySelector('#files-pages');
    const thumbsEl = root.querySelector('#files-thumbs');
    const status = root.querySelector('#files-viewer-status');
    const indicator = root.querySelector('#files-page-indicator');
    const zoomLabel = root.querySelector('#files-zoom-level');
    const template = indicator ? indicator.dataset.template : 'Page {n} of {total}';

    let pdf;
    try {
        pdf = await open(root.dataset.src).promise;
    } catch (err) {
        console.warn('files: could not open PDF', err);
        if (status) { status.textContent = root.dataset.error || 'This PDF could not be displayed.'; }
        return;
    }
    const total = pdf.numPages;
    const pages = [];
    for (let n = 1; n <= total; n++) { pages.push(await pdf.getPage(n)); }

    // Fit the first page's width to the well, capped at 100%: a letter page
    // at 100% is already as wide as a laptop window wants.
    const base = pages[0].getViewport({ scale: 1 });
    const room = Math.max(240, pagesEl.clientWidth - 48);
    let scale = Math.min(1.25, room / base.width);
    let current = 1;

    const holders = pages.map((page, i) => {
        const holder = document.createElement('div');
        holder.className = 'fs-page';
        holder.id = `files-page-${i + 1}`;
        holder.setAttribute('role', 'region');
        holder.setAttribute('aria-label', template.replace('{n}', i + 1).replace('{total}', total));
        const canvas = document.createElement('canvas');
        canvas.setAttribute('aria-hidden', 'true');
        const text = document.createElement('div');
        text.className = 'textLayer';
        holder.append(canvas, text);
        return holder;
    });

    async function render() {
        for (let i = 0; i < pages.length; i++) {
            const holder = holders[i];
            holder.style.setProperty('--total-scale-factor', String(scale));
            const viewport = await drawPage(pages[i], holder.firstChild, scale);
            holder.style.width = `${Math.floor(viewport.width)}px`;
            holder.style.height = `${Math.floor(viewport.height)}px`;
            const old = holder.querySelector('.textLayer');
            const text = document.createElement('div');
            text.className = 'textLayer';
            old.replaceWith(text);
            const layer = new pdfjsLib.TextLayer({
                textContentSource: pages[i].streamTextContent(),
                container: text,
                viewport,
            });
            await layer.render();
        }
        if (zoomLabel) { zoomLabel.textContent = `${Math.round(scale * 100)}%`; }
    }

    pagesEl.replaceChildren(...holders);
    await render();
    root.dataset.ready = '1';

    // Thumbnails: one button per page.
    const thumbButtons = [];
    if (thumbsEl) {
        for (let i = 0; i < pages.length; i++) {
            const li = document.createElement('li');
            const button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('aria-label', template.replace('{n}', i + 1).replace('{total}', total));
            const canvas = document.createElement('canvas');
            const thumbScale = THUMB_WIDTH / pages[i].getViewport({ scale: 1 }).width;
            await drawPage(pages[i], canvas, thumbScale);
            const label = document.createElement('span');
            label.textContent = String(i + 1);
            label.setAttribute('aria-hidden', 'true');
            button.append(canvas, label);
            button.addEventListener('click', () => goTo(i + 1));
            li.append(button);
            thumbsEl.append(li);
            thumbButtons.push(button);
        }
    }

    function show(n) {
        current = n;
        if (indicator) { indicator.textContent = template.replace('{n}', n).replace('{total}', total); }
        thumbButtons.forEach((b, i) => b.setAttribute('aria-current', String(i + 1 === n)));
    }

    function goTo(n) {
        const target = Math.min(total, Math.max(1, n));
        holders[target - 1].scrollIntoView({ block: 'start', behavior: 'auto' });
        show(target);
    }

    // The page indicator follows scrolling: the page holding the well's
    // upper third is the current one.
    pagesEl.addEventListener('scroll', () => {
        const top = pagesEl.getBoundingClientRect().top + pagesEl.clientHeight / 3;
        let n = 1;
        holders.forEach((h, i) => { if (h.getBoundingClientRect().top <= top) { n = i + 1; } });
        if (n !== current) { show(n); }
    }, { passive: true });

    function zoom(direction) {
        const next = direction > 0
            ? ZOOM_STEPS.find((s) => s > scale + 0.001)
            : [...ZOOM_STEPS].reverse().find((s) => s < scale - 0.001);
        if (next === undefined) { return; }
        scale = next;
        render().then(() => goTo(current));
    }

    root.querySelector('#files-prev-page')?.addEventListener('click', () => goTo(current - 1));
    root.querySelector('#files-next-page')?.addEventListener('click', () => goTo(current + 1));
    root.querySelector('#files-zoom-in')?.addEventListener('click', () => zoom(1));
    root.querySelector('#files-zoom-out')?.addEventListener('click', () => zoom(-1));
    pagesEl.addEventListener('keydown', (e) => {
        if (e.key === 'PageDown') { e.preventDefault(); goTo(current + 1); }
        if (e.key === 'PageUp') { e.preventDefault(); goTo(current - 1); }
    });
    show(1);
}

const viewer = document.getElementById('files-viewer');
if (viewer && viewer.dataset.src) { initViewer(viewer); }
initThumbs();
