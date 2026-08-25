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
  /* Frosted rather than opaque, so the wallpaper reads continuously behind the
     chrome. Mixed with transparent rather than given an alpha literal, because
     --color-surface is an opaque hex in light mode and already a translucent
     tint in dark -- color-mix handles both without a per-mode override. */
  background: color-mix(in srgb, var(--color-surface) 82%, transparent);
  backdrop-filter: blur(18px) saturate(150%);
  -webkit-backdrop-filter: blur(18px) saturate(150%);
  border-bottom: 1px solid var(--color-border);
}
.ui-toolbar-side { display: flex; align-items: center; gap: var(--space); }

.ui-wordmark { display: block; }
.ui-brand { display: inline-flex; align-items: center; }

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
  color: var(--color-fg);
  font-family: var(--font-family);
  font-size: var(--font-size-base);
  /* The shell sets --ui-wallpaper inline when a generated image is available.
     When it is not -- rendering failed, or nothing is on disk yet -- this
     falls back to a token gradient of the same hues, so the desktop still
     looks deliberate rather than blank. */
  background-color: var(--color-bg);
  background-image: var(--ui-wallpaper, linear-gradient(
      180deg,
      var(--ring-dark-blue) 0%,
      var(--ring-violet) 45%,
      var(--ring-pink) 68%,
      var(--color-accent) 100%));
  background-size: cover;
  background-position: center bottom;
  background-repeat: no-repeat;
  /* Own stacking context, so the two overlay layers below cannot escape it and
     land on top of a modal or the launcher panel. */
  position: relative;
  isolation: isolate;
}

/* Depth-of-field pass. A flat frosted sheet over the whole image would just
   look out of focus; masking the blur so it peaks at the top and bottom edges
   and clears through the middle band reads as depth instead, and keeps the
   ridgeline legible while softening everything the UI actually sits on. */
.ui-desktop::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  backdrop-filter: blur(var(--ui-wallpaper-blur, 4px)) saturate(112%);
  -webkit-backdrop-filter: blur(var(--ui-wallpaper-blur, 4px)) saturate(112%);
  -webkit-mask-image: linear-gradient(180deg,
      rgb(0 0 0 / 100%) 0%,
      rgb(0 0 0 / 18%) 38%,
      rgb(0 0 0 / 40%) 62%,
      rgb(0 0 0 / 100%) 100%);
  mask-image: linear-gradient(180deg,
      rgb(0 0 0 / 100%) 0%,
      rgb(0 0 0 / 18%) 38%,
      rgb(0 0 0 / 40%) 62%,
      rgb(0 0 0 / 100%) 100%);
}

/* Fade toward the page background so the wallpaper sits behind the UI rather
   than competing with it. The gradient shape is fixed and the overall strength
   is a single tunable, which keeps `wallpaper.fade` in config to one number
   instead of a set of stops that have to stay consistent with each other. */
.ui-desktop::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background: linear-gradient(180deg,
      var(--color-bg) 0%,
      color-mix(in srgb, var(--color-bg) 65%, transparent) 42%,
      var(--color-bg) 100%);
  opacity: var(--ui-wallpaper-fade, 0.7);
}

/* Both overlays are absolutely positioned at z-index 0; real content has to be
   lifted above them or the toolbar would end up underneath the scrim. */
.ui-desktop > * { position: relative; z-index: 1; }

/* Shortcuts dock: pinned to the bottom-right, stacking upward.
   column-reverse puts the first pinned app at the bottom so the stack grows
   toward the top of the window; wrap-reverse spills a full column leftward
   instead of running off the top edge. */
/* Two rows: a centred headline pinned to the top, and everything else pushed
   to the bottom-right. Splitting them means the dock keeps its own alignment
   instead of the headline dragging it back into the centre. */
.ui-desktop-surface {
  flex: 1;
  padding: calc(var(--space) * 3);
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.ui-desktop-headline {
  text-align: center;
  margin: calc(var(--space) * 2) auto 0;
  max-width: 34ch;
  /* Same reasoning as the tiles: this sits on the wallpaper, not on
     --color-bg, so --color-fg would vanish against the image in light mode. */
  color: var(--color-on-primary);
  text-shadow: 0 1px 8px rgb(0 0 0 / 40%);
}
.ui-desktop-headline .ui-text { color: inherit; }
.ui-dock-row {
  flex: 1;
  display: flex;
  align-items: flex-end;
  justify-content: flex-end;
  min-height: 0;
}
.ui-tile-dock {
  display: flex;
  flex-direction: column-reverse;
  flex-wrap: wrap-reverse;
  align-content: flex-end;
  gap: calc(var(--space) * 1.5);
  max-height: 100%;
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
.ui-tile:hover { background: color-mix(in srgb, var(--color-on-primary) 18%, transparent); }
/* These sit over the wallpaper, not over --color-bg, so they cannot use
   --color-fg: it is near-black in light mode and would vanish into the dark
   lower half of the image. --color-on-primary is white in both modes, which is
   what "text on a saturated brand surface" means here. */
.ui-tile { color: var(--color-on-primary); text-shadow: 0 1px 3px rgb(0 0 0 / 45%); }
.ui-tile .ui-text { color: inherit; }
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
  /* Same frosting as the toolbar. Kept fairly opaque -- this one holds a list
     of text labels an agent has to read, so legibility beats the effect. */
  background: color-mix(in srgb, var(--color-bg) 90%, transparent);
  backdrop-filter: blur(20px) saturate(150%);
  -webkit-backdrop-filter: blur(20px) saturate(150%);
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
