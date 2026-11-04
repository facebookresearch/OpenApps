"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The icon-and-text "Copy" pill, shared by OpenBanking and Contacts.

The value being copied is never in the markup. The button carries a URL and
the click fetches the value from the server: a ``data-`` attribute holding an
account number would hand it to any agent reading the HTML and quietly undo a
click-to-reveal next to it. The fetch is a plain read, invisible to every
``/<app>_all`` scoring endpoint.

Class names take a ``prefix`` so an app keeps the markup its tests and UI
questions already know (the bank's pill is ``ob-copy``); the script keys off
``data-copy-url``, so one listener serves every prefix.

Usage::

    from open_apps.ui.copy import COPY_SCRIPT, CopyButton, copy_styles

    Style(copy_styles("ct-copy"))
    CopyButton("Mobile", url="/contacts/3/copy/7", copy_label="Copy", ...)
    COPY_SCRIPT  # once per page
"""
from __future__ import annotations

from fasthtml.common import Button, Script, Span

from open_apps.icons import Icon, icon


def CopyButton(
    label: str,
    *,
    url: str,
    copy_label: str,
    copied_label: str,
    failed_label: str,
    prefix: str = "ui-copy",
    testid: str | None = None,
    cls: str = "",
):
    """A Copy pill for the value behind ``url``. ``label`` names the value
    ("Account number", "mobile") in the accessible name and tooltip; the
    visible caption is ``copy_label``, swapped for ``copied_label`` or
    ``failed_label`` for a moment after a click."""
    return Button(
        Span(icon(Icon.COPY, size=14), cls=f"{prefix}-icon {prefix}-idle"),
        Span(icon(Icon.CHECK, size=14), cls=f"{prefix}-icon {prefix}-done"),
        # Polite live region, so the swap to "Copied" is announced -- the only
        # feedback a screen-reader user (or an a11y-tree agent) gets.
        Span(copy_label, cls=f"{prefix}-text", aria_live="polite"),
        type="button",
        cls=f"{prefix} {cls}".strip(),
        title=f"{copy_label}: {label}",
        aria_label=f"{copy_label}: {label}",
        data_copy_url=url,
        data_copy_prefix=prefix,
        data_copy_label=copy_label,
        data_copied_label=copied_label,
        data_copy_failed_label=failed_label,
        data_testid=testid,
    )


def copy_styles(prefix: str = "ui-copy") -> str:
    """The pill's CSS for one class prefix. Shared design tokens only.

    A quiet secondary control: outlined in the border token so it reads as a
    button without competing with the value beside it. The check and
    "Copied" replace the glyph and caption in place rather than adding a
    toast somewhere else."""
    p = f".{prefix}"
    return f"""
    {p} {{
        display: inline-flex;
        align-items: center;
        gap: 0.3rem;
        width: auto;
        margin: 0;
        padding: 0.15rem 0.55rem 0.15rem 0.45rem;
        border: 1px solid var(--color-border);
        border-radius: 999px;
        background: transparent;
        color: var(--color-muted);
        font-family: inherit;
        font-size: 0.8rem;
        font-weight: 500;
        line-height: 1.4;
        cursor: pointer;
    }}
    {p}:hover {{ color: var(--color-fg); border-color: var(--color-muted); }}
    /* Pico's focus glow is sized for full-width form buttons; on a pill this
       small it reads as a second border. Keyboard focus keeps a ring. */
    {p}:focus {{ box-shadow: none; }}
    {p}:focus-visible {{ outline: 2px solid var(--color-accent); outline-offset: 2px; }}
    {p}-icon {{ display: inline-flex; }}
    {p}-done {{ display: none; }}
    {p}[data-state="copied"] {{ color: var(--color-accent); border-color: currentColor; }}
    {p}[data-state="copied"] {p}-idle {{ display: none; }}
    {p}[data-state="copied"] {p}-done {{ display: inline-flex; }}
    {p}[data-state="failed"] {{ color: var(--color-danger); border-color: currentColor; }}
    """


# One delegated listener for every Copy pill on the page, so pills that arrive
# in an htmx swap work without re-binding.
#
# The clipboard write is attempted three ways because each fails somewhere:
# `ClipboardItem` with a promise keeps the user activation alive across the
# fetch (Safari requires that shape); plain `writeText` after the fetch covers
# Chromium builds without promise-valued ClipboardItems; and the textarea +
# `execCommand('copy')` fallback is what still works in a headless browser
# with no clipboard permission granted -- the case an eval run is in.
COPY_SCRIPT = Script("""
(function () {
    if (window.oaCopyBound) { return; }
    window.oaCopyBound = true;

    function legacyCopy(text) {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        var ok = false;
        try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
        ta.remove();
        return ok;
    }

    function settle(btn, ok) {
        var text = btn.querySelector('.' + btn.dataset.copyPrefix + '-text');
        btn.dataset.state = ok ? 'copied' : 'failed';
        text.textContent = ok ? btn.dataset.copiedLabel : btn.dataset.copyFailedLabel;
        clearTimeout(btn.oaCopyTimer);
        btn.oaCopyTimer = setTimeout(function () {
            delete btn.dataset.state;
            text.textContent = btn.dataset.copyLabel;
        }, 1800);
    }

    document.addEventListener('click', function (event) {
        var btn = event.target.closest('button[data-copy-url]');
        if (!btn) { return; }
        event.preventDefault();
        var text = fetch(btn.dataset.copyUrl).then(function (r) {
            if (!r.ok) { throw new Error('copy source ' + r.status); }
            return r.text();
        });
        var write;
        if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
            write = navigator.clipboard.write([new ClipboardItem({
                'text/plain': text.then(function (t) { return new Blob([t], {type: 'text/plain'}); })
            })]);
        } else if (navigator.clipboard && navigator.clipboard.writeText) {
            write = text.then(function (t) { return navigator.clipboard.writeText(t); });
        } else {
            write = Promise.reject(new Error('no async clipboard'));
        }
        write.then(function () { settle(btn, true); }, function () {
            text.then(function (t) {
                var ok = legacyCopy(t);
                btn.focus();
                settle(btn, ok);
            }, function () { settle(btn, false); });
        });
    });
})();
""")
