"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Component CSS for :mod:`open_apps.ui`.

One stylesheet for every atom and molecule, written entirely against
``var(--token)``. Not a single literal colour appears below -- that is the
property that makes a theme swap, or the light/dark toggle, repaint the whole
shell without re-rendering any Python or reloading the page.

Emitted as an inline ``<style>``. No stylesheet request, no bundler, nothing
fetched at page load; the eval nodes have no outbound network.

Every value that could reasonably differ between themes reads a token. Where a
component needs something the theme does not define -- a hover tint, say -- it
uses ``color-mix()`` against an existing token rather than inventing a hex, so
it stays correct in both modes instead of only the one it was eyeballed in.
"""
from __future__ import annotations

from fasthtml.common import Style

_CSS = """
/* ---- atoms ------------------------------------------------------------ */
.ui-surface {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  color: var(--color-fg);
}
.ui-surface.is-elevated { background: var(--color-surface); }

.ui-stack-row, .ui-stack-col {
  display: flex;
  gap: calc(var(--space) * var(--ui-stack-gap, 1));
  align-items: var(--ui-stack-align, stretch);
}
.ui-stack-row { flex-direction: row; }
.ui-stack-col { flex-direction: column; }

.ui-text { font-family: var(--font-family); color: var(--color-fg); }
.ui-text.is-title {
  font-family: var(--font-heading);
  font-size: calc(var(--font-size-base) * 1.5);
  font-weight: 600;
}
.ui-text.is-body { font-size: var(--font-size-base); }
.ui-text.is-caption {
  font-size: calc(var(--font-size-base) * 0.8125);
  color: var(--color-muted);
}

.ui-btn {
  font-family: var(--font-family);
  font-size: var(--font-size-base);
  border-radius: var(--radius);
  border: 1px solid transparent;
  padding: calc(var(--space) * 0.75) calc(var(--space) * 1.5);
  cursor: pointer;
  line-height: 1.2;
}
.ui-btn.is-primary { background: var(--color-primary); color: var(--color-on-primary); }
.ui-btn.is-neutral { background: var(--color-neutral); color: var(--color-btn-fg); }
.ui-btn.is-danger  { background: var(--color-danger);  color: var(--color-btn-fg); }
.ui-btn.is-ghost   { background: transparent; color: var(--color-fg); border-color: var(--color-border); }
/* Hover derives from the button's own colour rather than a separate token, so
   a theme only has to define the base and both modes stay consistent. */
.ui-btn:hover { filter: brightness(0.94); }

.ui-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius);
  color: var(--color-fg);
  padding: calc(var(--space) * 0.5);
  cursor: pointer;
}
.ui-icon-btn:hover { background: color-mix(in srgb, var(--color-fg) 8%, transparent); }
.ui-icon-btn:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 1px; }

.ui-badge {
  display: inline-block;
  font-size: calc(var(--font-size-base) * 0.75);
  border-radius: 999px;
  padding: 2px calc(var(--space) * 0.75);
  color: var(--color-btn-fg);
}
.ui-badge.is-neutral { background: var(--color-neutral); }
.ui-badge.is-success { background: var(--color-success); }
.ui-badge.is-warning { background: var(--color-warning); color: var(--color-fg); }
.ui-badge.is-danger  { background: var(--color-danger); }

.ui-divider { height: 1px; background: var(--color-border); width: 100%; }

/* ---- molecules -------------------------------------------------------- */
.ui-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space);
  padding: calc(var(--space) * 0.75) calc(var(--space) * 1.5);
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}
.ui-toolbar-side { display: flex; align-items: center; gap: var(--space); }

.ui-chip {
  display: inline-flex;
  align-items: center;
  gap: calc(var(--space) * 0.5);
  color: var(--color-muted);
  padding: calc(var(--space) * 0.25) calc(var(--space) * 0.75);
  border-radius: var(--radius);
}

/* ---- desktop ---------------------------------------------------------- */
.ui-desktop {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--color-bg);
  color: var(--color-fg);
  font-family: var(--font-family);
  font-size: var(--font-size-base);
}
.ui-desktop-surface { flex: 1; padding: calc(var(--space) * 3); }
.ui-tile-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
  gap: calc(var(--space) * 2);
  max-width: 760px;
}
.ui-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: calc(var(--space) * 0.75);
  padding: calc(var(--space) * 1.5) var(--space);
  border-radius: var(--radius);
  text-decoration: none;
  color: var(--color-fg);
}
.ui-tile:hover { background: color-mix(in srgb, var(--color-fg) 6%, transparent); }
.ui-tile-glyph {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px; height: 48px;
  border-radius: var(--radius);
  /* Falls back to the primary token when a tile passes no accent, so an
     un-accented tile is still branded rather than colourless. */
  background: var(--ui-tile-accent, var(--color-primary));
  color: var(--color-on-primary);
}
.ui-tile-label { text-align: center; }

/* ---- launcher --------------------------------------------------------- */
.ui-launcher { position: relative; }
.ui-launcher-btn.is-open { background: color-mix(in srgb, var(--color-fg) 10%, transparent); }
.ui-launcher-panel {
  position: absolute;
  top: calc(100% + var(--space));
  left: 0;
  z-index: 50;
  min-width: 260px;
  padding: calc(var(--space) * 0.5);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  box-shadow: 0 8px 24px color-mix(in srgb, var(--color-fg) 18%, transparent);
}
.ui-launcher-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space);
  border-radius: var(--radius);
  padding-right: calc(var(--space) * 0.5);
}
.ui-launcher-item:hover { background: color-mix(in srgb, var(--color-fg) 6%, transparent); }
.ui-launcher-link {
  display: flex;
  align-items: center;
  gap: var(--space);
  flex: 1;
  padding: calc(var(--space) * 0.75);
  text-decoration: none;
  color: var(--color-fg);
}
.ui-launcher-glyph {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px; height: 28px;
  border-radius: calc(var(--radius) * 0.75);
  background: var(--color-surface);
  color: var(--color-accent);
}

/* The pin is always in the DOM; hover only changes its opacity. Rendering it
   on hover would put it out of reach of keyboards and of any agent acting off
   the accessibility tree. It stays visible while focused, and while pinned --
   otherwise unpinning would require discovering a control you cannot see. */
.ui-pin-btn { opacity: 0; transition: opacity 120ms ease; }
.ui-launcher-item:hover .ui-pin-btn,
.ui-launcher-item:focus-within .ui-pin-btn,
.ui-pin-btn:focus-visible,
.ui-pin-btn.is-pinned { opacity: 1; }
.ui-pin-btn.is-pinned { color: var(--color-accent); }
"""


def component_styles() -> Style:
    """The stylesheet for every atom and molecule, as an inline ``<style>``.

    Include once per page, alongside the theme's ``:root`` block from
    ``open_apps.theme.theme_style``. Order does not matter -- these rules read
    tokens, they never define them.
    """
    return Style(_CSS)
