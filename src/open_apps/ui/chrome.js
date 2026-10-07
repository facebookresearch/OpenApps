/*
 * Global window chrome: agent cursor, dock launcher, dock shortcuts.
 *
 * Inlined at the end of <body> by open_apps.chrome_middleware, after the
 * markup it drives, so it runs synchronously before `load` -- the first
 * screenshot of a page already has the cursor where the last action left it.
 *
 * No dependencies. The maps template ships without htmx and the shop is
 * Jinja, so nothing here may assume anything the FastHTML apps load.
 */
(() => {
  "use strict";

  // ---- agent cursor ----------------------------------------------------
  //
  // Playwright teleports the real pointer: one mousemove at the target, then
  // the press. This draws a cursor that *travels* there instead, easing from
  // wherever it last was, so a recording shows a hand moving rather than a
  // sequence of things lighting up.
  //
  // The position survives navigation through sessionStorage -- a click on a
  // link loads a new document, and a cursor that vanished on every page load
  // would be absent from exactly the screenshots that follow a click. The
  // *target* is stored, not the in-flight position, so a navigation that
  // lands mid-glide restores the cursor where the agent actually clicked.
  const STORE_KEY = "oa-cursor";

  function initCursor(root) {
    const mode = root.dataset.show;
    const automated = navigator.webdriver === true;
    if (mode === "never" || (mode !== "always" && !automated)) {
      root.remove();
      return;
    }

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const glideMs = reducedMotion ? 0 : Number(root.dataset.glide || 0);
    const ripple = root.dataset.ripple === "true";
    const followFocus = root.dataset.followFocus === "true";
    const layer = root.parentElement;

    let current = null; // {x, y} as drawn
    let from = null, to = null, startedAt = 0, duration = 0, frame = 0;
    let lastPointerAt = -Infinity;
    const bornAt = performance.now();

    const draw = (p) => {
      root.style.transform = `translate3d(${p.x}px, ${p.y}px, 0)`;
      root.hidden = false;
    };

    // easeInOutCubic: accelerates off the mark and settles onto the target,
    // which reads as deliberate. easeOut alone looks like it was flung.
    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

    const step = (now) => {
      const t = Math.min(1, (now - startedAt) / duration);
      const k = ease(t);
      current = { x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k };
      draw(current);
      frame = t < 1 ? requestAnimationFrame(step) : 0;
    };

    const moveTo = (x, y) => {
      try {
        sessionStorage.setItem(STORE_KEY, JSON.stringify({ x, y }));
      } catch (_) { /* storage disabled: the cursor just will not persist */ }
      if (current === null || glideMs <= 0) {
        current = { x, y };
        draw(current);
        return;
      }
      // Short hops take less time than long ones, but never less than 40% of
      // the budget -- a near-instant twitch reads as a glitch, not a move.
      const distance = Math.hypot(x - current.x, y - current.y);
      from = { ...current };
      to = { x, y };
      startedAt = performance.now();
      duration = glideMs * Math.min(1, Math.max(0.4, distance / 600));
      if (!frame) frame = requestAnimationFrame(step);
    };

    try {
      const saved = JSON.parse(sessionStorage.getItem(STORE_KEY) || "null");
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
        current = { x: saved.x, y: saved.y };
        draw(current);
      }
    } catch (_) { /* corrupt or unavailable: start hidden, show on first move */ }

    const onPointer = (event) => {
      lastPointerAt = performance.now();
      moveTo(event.clientX, event.clientY);
    };
    window.addEventListener("pointermove", onPointer, { capture: true, passive: true });

    window.addEventListener("pointerdown", (event) => {
      onPointer(event);
      root.classList.add("is-pressed");
      if (!ripple) return;
      const ring = document.createElement("div");
      ring.className = "oa-cursor-ripple";
      ring.setAttribute("aria-hidden", "true");
      ring.style.left = `${event.clientX}px`;
      ring.style.top = `${event.clientY}px`;
      ring.addEventListener("animationend", () => ring.remove());
      layer.appendChild(ring);
    }, { capture: true, passive: true });

    window.addEventListener("pointerup", () => root.classList.remove("is-pressed"),
      { capture: true, passive: true });

    if (followFocus) {
      // Playwright's fill() focuses a field without moving the pointer, so a
      // typing step would otherwise be invisible. Only for focus that did not
      // come from a pointer (a click focuses too) and not during page load,
      // where an `autofocus` field is the page's doing, not the agent's.
      document.addEventListener("focusin", (event) => {
        const now = performance.now();
        if (now - lastPointerAt < 400 || now - bornAt < 300) return;
        const el = event.target;
        if (!(el instanceof Element) || el.closest("#oa-chrome, #oa-titlebar")) return;
        const box = el.getBoundingClientRect();
        if (box.width === 0 || box.bottom < 0 || box.top > innerHeight) return;
        // Just inside the left edge, where a caret would be -- not the centre
        // of a 600px-wide textarea.
        moveTo(box.left + Math.min(16, box.width / 2), box.top + Math.min(box.height / 2, 14));
      });
    }
  }

  // ---- dock: all-apps panel --------------------------------------------
  //
  // Toggles `hidden`, so a closed panel is absent from the accessibility tree
  // and an agent sees one "All apps" button rather than every app twice.
  function initLauncher(button, panel) {
    const setOpen = (open) => {
      panel.hidden = !open;
      button.setAttribute("aria-expanded", String(open));
    };
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      setOpen(panel.hidden);
    });
    document.addEventListener("click", (event) => {
      if (!panel.hidden && !panel.contains(event.target)) setOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !panel.hidden) {
        setOpen(false);
        button.focus();
      }
    });
  }

  // ---- dock: keyboard shortcuts ----------------------------------------
  //
  // Alt+N opens the dock item advertising it in aria-keyshortcuts. Matched on
  // event.code, not event.key: on a Mac, Alt+1 produces "¡" as the key.
  function initShortcuts(dock) {
    const targets = new Map();
    for (const el of dock.querySelectorAll("[aria-keyshortcuts]")) {
      targets.set(el.getAttribute("aria-keyshortcuts"), el);
    }
    document.addEventListener("keydown", (event) => {
      if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const match = /^Digit(\d)$/.exec(event.code);
      const el = match && targets.get(`Alt+${match[1]}`);
      if (!el) return;
      event.preventDefault();
      window.location.assign(el.href);
    }, { capture: true });
  }

  const cursor = document.getElementById("oa-cursor");
  if (cursor) initCursor(cursor);

  const launcherButton = document.getElementById("oa-dock-launcher-btn");
  const launcherPanel = document.getElementById("oa-dock-panel");
  if (launcherButton && launcherPanel) initLauncher(launcherButton, launcherPanel);

  const dock = document.getElementById("oa-dock");
  if (dock && dock.dataset.shortcuts === "true") initShortcuts(dock);
})();
