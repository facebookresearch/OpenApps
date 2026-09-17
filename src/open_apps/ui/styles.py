"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Component CSS for :mod:`open_apps.ui`.

One stylesheet for every atom and molecule, written against ``var(--token)``.
That is the property that makes a theme swap, or the light/dark toggle, repaint
the whole shell without re-rendering any Python or reloading the page.

Three literal colours survive, all deliberate and all black:

* the two ``mask-image`` gradients on the wallpaper overlays, where the colour
  is never painted -- only its alpha is read;
* ``--ui-shadow-raised`` and the launcher scrim, which are *absences of light*
  rather than surfaces. A shadow derived from ``--color-fg`` would come out
  white on the dark theme and smear under every icon.

Anything that is a surface or ink reads a token. Where a component needs
something the theme does not define -- a hover tint, say -- it uses
``color-mix()`` against an existing token rather than inventing a hex, so it
stays correct in both modes instead of only the one it was eyeballed in.

Emitted as an inline ``<style>``. No stylesheet request, no bundler, nothing
fetched at page load; the eval nodes have no outbound network.
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

/* Perfect circles. A fixed box plus 50% rather than padding plus a radius:
   padding makes the width depend on the glyph, so a 16px icon and a 20px icon
   would give two different ovals sitting next to each other in the toolbar. */
.ui-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: var(--ui-icon-btn-size, 34px);
  height: var(--ui-icon-btn-size, 34px);
  padding: 0;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 50%;
  color: var(--color-fg);
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
/* Dark mode flips the mark vertically, so the V opens upward. Scoped to the
   mark group, not the <svg>: flipping the whole thing would mirror the word
   too. transform-box: fill-box makes the origin the group's own bounds rather
   than the SVG viewport, which is what keeps it spinning in place. */
#desktop-shell[data-mode="dark"] .ui-wordmark-mark {
  transform: scaleY(-1);
  transform-box: fill-box;
  transform-origin: center;
}
.ui-brand { display: inline-flex; align-items: center; }

.ui-chip {
  display: inline-flex;
  align-items: center;
  gap: calc(var(--space) * 0.5);
  color: var(--color-muted);
  padding: calc(var(--space) * 0.25) calc(var(--space) * 0.75);
  border-radius: var(--radius);
}
/* In the shell the toolbar is frosted over the wallpaper, and --color-muted is
   chosen to read on --color-bg. The dark theme's is a mid grey (#8a8d91) that
   lands on a dark band of the image, leaving the clock and temperature dimmer
   than the mode toggle sitting right beside them. Deriving from --color-fg
   keeps them muted relative to their neighbours without picking a per-mode
   value: dark ink at 72% in the light theme, white at 72% in the dark one.
   The inner .ui-text.is-caption sets its own colour, so it is named too. */
.ui-desktop .ui-chip,
.ui-desktop .ui-chip .ui-text {
  color: color-mix(in srgb, var(--color-fg) 72%, transparent);
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

  /* Backing for text that sits on the wallpaper rather than on --color-bg.
     The ink itself is --color-fg, which is already correct in both modes --
     near-black in the light theme, white in the dark one. What it needs is a
     halo, because the wallpaper is an image and no single ink survives every
     band of it. Deriving the halo from --color-bg means it is always the
     surface the text would have been legible against anyway, so this needs no
     per-mode branch and holds for a theme that is neither Meta one. */
  --ui-wallpaper-halo: color-mix(in srgb, var(--color-bg) 80%, transparent);

  /* Elevation under something sitting on the wallpaper. Stays dark in both
     modes on purpose: depth is an absence of light, and deriving it from
     --color-fg the way the launcher panel does would put a white smear under
     every app icon on the dark theme.
     Only light mode really sees it -- on the dark wallpaper a black shadow has
     nothing to contrast against and simply disappears, which is why this was
     tuned by eye against the dark theme and came out far too heavy on the
     pale one: 28% at a 16px blur read as a hard blob under each icon. */
  --ui-shadow-raised: 0 3px 10px rgb(0 0 0 / 13%);
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

/* ...but the toolbar has to outrank the surface, not just the overlays.
   `> *` gives both of them z-index 1, and the surface comes later in the DOM,
   so it painted on top. The launcher panel's own z-index could not save it:
   z-index 50 is scoped to the toolbar's stacking context, which as a whole was
   still below the surface. The panel rendered, the surface covered it, and
   every click on an app link or a pin button landed on the desktop instead. */
.ui-desktop > .ui-toolbar { z-index: 3; }

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
  /* Sits on the wallpaper, not on --color-bg. --color-fg is still the right
     ink -- it is what the active theme calls legible -- but it needs the halo
     to survive the image. It used to be --color-on-primary, which is white in
     *both* themes, so the light mode rendered white display type over a pale
     wallpaper. */
  color: var(--color-fg);
  text-shadow: 0 1px 10px var(--ui-wallpaper-halo), 0 0 3px var(--ui-wallpaper-halo);
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
/* Hover lifts a surface under the tile rather than tinting it. A --color-fg
   wash is the same colour as the label sitting on top of it, so contrast went
   *down* on hover in both modes -- the one direction a hover state must not
   go. Washing toward --color-bg restores the fg-on-bg pairing the tokens are
   designed around: a pale card under dark text in the light theme, a dark one
   under white text in the dark theme. */
.ui-tile:hover { background: color-mix(in srgb, var(--color-bg) 55%, transparent); }
/* Over the wallpaper, so the same halo the headline uses. Previously pinned to
   --color-on-primary (white in both themes) on the theory that a tile label is
   "text on a saturated surface" -- but only the glyph tile is saturated; the
   label sits on the wallpaper underneath it and was white-on-pale in light
   mode. The glyph keeps --color-on-primary, which is correct there. */
.ui-tile { color: var(--color-fg); text-shadow: 0 1px 3px var(--ui-wallpaper-halo), 0 0 2px var(--ui-wallpaper-halo); }
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
.ui-pin-btn { --ui-icon-btn-size: 28px; opacity: 0; transition: opacity 120ms ease; }
.ui-launcher-item:hover .ui-pin-btn,
.ui-launcher-item:focus-within .ui-pin-btn,
.ui-pin-btn:focus-visible,
.ui-pin-btn.is-pinned { opacity: 1; }
.ui-pin-btn.is-pinned { color: var(--color-accent); }

/* ---- phone home screen ------------------------------------------------ */
/* Applied when `device.form_factor` is phone -- the shell root carries
   `is-phone`, and apps/start_page/main.py has already emitted a different
   composition: a status bar, a wordmark widget, a grid of the apps that are
   not pinned, and a real dock element holding the ones that are.
 *
 * Keyed off the root class, not a width, and that is the substantive
 * difference from doing this with a media query. The device is configuration
 * (config/device/), so it is known at render time and settled for the whole
 * episode: the phone rendering holds at any window size, it appears in the
 * saved config and in W&B next to every other axis, and -- because the markup
 * differs rather than only the styling -- the layout can say things a
 * stylesheet cannot, like which apps belong in the dock. The narrow-window
 * case, a person dragging a desktop window small, is a different problem and
 * is handled separately at the bottom of this file.
 */
.ui-desktop.is-phone { min-height: 100dvh; }

/* Status bar. Slimmer than the toolbar, and the launcher is not in it -- it
   moved to the dock -- so the left slot is the clock alone. */
.is-phone .ui-toolbar {
  padding: calc(var(--space) * 0.5) var(--space);
  border-bottom: none;
}
.is-phone .ui-toolbar-side { gap: calc(var(--space) * 0.5); }
.is-phone .ui-chip { padding: 0; }

.is-phone .ui-desktop-surface { padding: calc(var(--space) * 1.5); }

/* The widget: wordmark over the headline, on a frosted card. A phone home
   screen has no room for centred display type across the wallpaper, and it
   is where the brand goes now that the status bar is only indicators. */
.is-phone .ui-desktop-headline {
  display: flex;
  flex-direction: column;
  gap: calc(var(--space) * 0.75);
  margin: 0 0 calc(var(--space) * 2.5);
  max-width: none;
  text-align: left;
  padding: calc(var(--space) * 1.25) calc(var(--space) * 1.5);
  border: 1px solid var(--color-border);
  border-radius: calc(var(--radius) * 2);
  background: color-mix(in srgb, var(--color-surface) 78%, transparent);
  backdrop-filter: blur(18px) saturate(140%);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  /* On its own surface, not on the image, so it wants the plain token and no
     halo -- the desktop's wallpaper backing would just smear here. Same ink
     either way; this only drops the shadow. */
  color: var(--color-fg);
  text-shadow: none;
}
.is-phone .ui-desktop-headline .ui-text { line-height: 1.35; }

/* Icons fill from the top-left. Four columns, fixed: a grid whose column count
   followed the width would put the same app in a different place on two
   phones, and a coordinate-grounded action would stop transferring between
   them for no reason the task can see. */
.is-phone .ui-dock-row { align-items: flex-start; justify-content: stretch; }
.is-phone .ui-tile-dock {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: calc(var(--space) * 2) var(--space);
  width: 100%;
  max-height: none;
}
.is-phone .ui-tile { padding: 0; gap: calc(var(--space) * 0.75); }
.is-phone .ui-tile-glyph {
  width: 58px;
  height: 58px;
  /* A percentage, so the corners stay proportional at any size -- and rounded
     far enough to read as an app icon rather than as a card. */
  border-radius: 28%;
  box-shadow: var(--ui-shadow-raised);
}
.is-phone .ui-tile-label {
  display: block;
  max-width: 100%;
  font-size: calc(var(--font-size-base) * 0.75);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* The dock: pinned apps, then the launcher. In flow as the shell's last child
   rather than positioned over it, so nothing has to reserve space for it and
   the grid above simply gets what is left.
 *
 * The dock itself must NOT set overflow. The launcher panel opens upward out
 * of it, and any overflow value other than visible makes this a scroll
 * container that clips the panel to the dock's own box -- the panel renders,
 * `aria-expanded` flips, and nothing appears on screen. Scrolling belongs to
 * the strip of pinned icons below, which is the part that can actually run
 * out of room. */
.ui-phone-dock {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: calc(var(--space) * 1.5);
  padding: var(--space) var(--space) calc(var(--space) * 1.75);
  background: color-mix(in srgb, var(--color-surface) 74%, transparent);
  backdrop-filter: blur(20px) saturate(150%);
  -webkit-backdrop-filter: blur(20px) saturate(150%);
  border-top: 1px solid var(--color-border);
}
/* Pin every app and the icons still have to fit on a 390px screen: the strip
   scrolls, and the launcher beside it stays put rather than being pushed off
   the edge with them. */
.ui-phone-dock-apps {
  display: flex;
  align-items: center;
  gap: calc(var(--space) * 1.5);
  min-width: 0;
  overflow-x: auto;
  /* Room for the icons' drop shadow. A scroll container clips at its own
     edges, so without the inset the shadow would end in a hard line; the
     matching negative margin gives the space back, leaving the dock's own
     padding to set the visual height. */
  padding-block: calc(var(--space) * 0.75);
  margin-block: calc(var(--space) * -0.75);
}
/* Home indicator. A pseudo-element because it is decoration: an empty <div>
   would show up in the accessibility tree as an unnamed node for an agent to
   wonder about. */
.ui-phone-dock { position: relative; }
.ui-phone-dock::after {
  content: "";
  position: absolute;
  bottom: 6px;
  left: 50%;
  width: 120px;
  height: 4px;
  transform: translateX(-50%);
  border-radius: 999px;
  background: color-mix(in srgb, var(--color-fg) 32%, transparent);
}
/* Dock icons are smaller than the grid's and drop their labels, the way a
   phone's do -- the dock is for apps you already recognise. */
.ui-phone-dock .ui-tile { padding: 0; }
.ui-phone-dock .ui-tile-glyph { width: 46px; height: 46px; border-radius: 28%; }
.ui-phone-dock .ui-tile-label { display: none; }
.is-phone .ui-launcher-btn {
  --ui-icon-btn-size: 46px;
  background: color-mix(in srgb, var(--color-fg) 8%, transparent);
}
/* The launcher panel rises from the dock instead of hanging off the toolbar,
   and it is anchored to the dock rather than to the button inside it -- hence
   dropping the launcher's own `position: relative` here. The dock spans the
   screen and its contents are centred, so a panel hung off the button's right
   edge starts wherever the number of pinned icons happens to put it and runs
   off the left of a 390px screen at anything near its full width. Off the
   dock it is a sheet: full width, inset by the dock's own padding. */
/* The phone does not use the anchored panel at all -- LauncherSheet renders a
   full-screen drawer at the shell root instead. A popover here had to fit in
   whatever vertical space the dock left it and was one `overflow` on any
   ancestor away from being clipped to nothing, with correct markup and a
   correct `aria-expanded` either way: a silent failure.

   The overlay is `fixed` and MUST stay outside `.ui-phone-dock`, which sets
   `backdrop-filter`. Any of filter/backdrop-filter/transform makes an element
   the containing block for fixed descendants, so nested in the dock `inset: 0`
   would resolve to the dock's ~88px box rather than the viewport. */
.ui-launcher-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}
.ui-launcher-scrim {
  position: absolute;
  inset: 0;
  border: 0;
  padding: 0;
  background: color-mix(in srgb, #000 45%, transparent);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
}
.ui-launcher-sheet {
  position: relative;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  padding: var(--space) var(--space) calc(var(--space) * 2.5);
  background: color-mix(in srgb, var(--color-bg) 96%, transparent);
  backdrop-filter: blur(20px) saturate(150%);
  -webkit-backdrop-filter: blur(20px) saturate(150%);
  border-top: 1px solid var(--color-border);
  border-radius: calc(var(--radius) * 3) calc(var(--radius) * 3) 0 0;
  box-shadow: 0 -8px 32px color-mix(in srgb, var(--color-fg) 22%, transparent);
}
.ui-launcher-sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: calc(var(--space) * 0.5) var(--space) var(--space);
}
.ui-launcher-sheet-list {
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: calc(var(--space) * 0.25);
}
.is-phone .ui-launcher-link { padding: var(--space); gap: calc(var(--space) * 1.25); }
.is-phone .ui-launcher-glyph { width: 34px; height: 34px; }

/* ---- tablet ----------------------------------------------------------- */
/* Keeps the desktop composition -- there is room for it -- and grows the hit
   targets, because the pointer is a finger. ~44px is the smallest target that
   is comfortable to tap; the desktop's 34px buttons are sized for a cursor. */
.is-tablet .ui-icon-btn { --ui-icon-btn-size: 44px; }
.is-tablet .ui-pin-btn { --ui-icon-btn-size: 36px; }
.is-tablet .ui-launcher-item { padding-right: var(--space); }
.is-tablet .ui-launcher-link { padding: var(--space); }

/* ---- touch pointers ---------------------------------------------------- */
/* A hover-revealed pin is a control that cannot be found on a touch screen,
   and unlike the keyboard case focus-within never rescues it -- there is
   nothing to tab with. Keyed off the pointer rather than the form factor so it
   covers any device config with `has_touch: true`, which is what makes the
   flag worth setting: without it the emulation is a lie the CSS never sees. */
@media (hover: none) {
  .ui-pin-btn { opacity: 1; }
}

/* ---- narrow windows ---------------------------------------------------- */
/* A person dragging a desktop window small, or a device whose layouts have not
   been written yet. Not the phone rendering -- that is a composition, chosen
   by config -- just enough to keep the desktop one from breaking up. Scoped
   with :not(.is-phone) so it cannot reach the phone markup, which is already
   laid out for this width. */
@media (max-width: 640px) {
  .ui-desktop:not(.is-phone) .ui-toolbar { padding: calc(var(--space) * 0.5) var(--space); }
  /* CSS beats the SVG height attribute, so the lockup shrinks without the
     Python having to know how wide the window is. */
  .ui-desktop:not(.is-phone) .ui-wordmark { height: 22px; }
  .ui-desktop:not(.is-phone) .ui-desktop-surface { padding: calc(var(--space) * 1.5); }
  .ui-desktop:not(.is-phone) .ui-desktop-headline { max-width: none; }
  /* Shortcuts run out of vertical room long before horizontal: wrap them into
     rows instead of one tall column. */
  .ui-desktop:not(.is-phone) .ui-tile-dock {
    flex-direction: row;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--space);
  }
}
"""


def component_styles() -> Style:
    """The stylesheet for every atom and molecule, as an inline ``<style>``.

    Include once per page, alongside the theme's ``:root`` block from
    ``open_apps.theme.theme_style``. Order does not matter -- these rules read
    tokens, they never define them.
    """
    return Style(_CSS)
