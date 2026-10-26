# Vendored frontend assets

Third-party JavaScript and CSS, committed rather than fetched at page load.

## Why these are checked in

`fast_app()` / `FastHTML()` load htmx, Pico and three helper scripts from
`cdn.jsdelivr.net` by default. On a host with no outbound network — which is
what the SLURM eval nodes are — none of it arrives, and the failure is silent
and severe:

* **htmx missing** means every `hx-get` / `hx-post` / `hx-put` in every app is
  inert. A checkbox still *appears* to toggle, because that is native browser
  behaviour, but no request is sent and no server state changes. An agent
  clicks the right element, the screenshot shows the click landed, and the task
  scores zero.
* **Pico missing** means every page renders unstyled. For a vision agent scored
  on screenshots, that changes the observation itself.

Neither failure raises an error anywhere. Serving these locally is what makes
the environment behave the same offline as it does on a laptop.

There is precedent: `../js/jquery.min.js` and `../css/fontawesome-all.min.css`
are already vendored the same way.

## Contents

| File | Version | Upstream | License |
|------|---------|----------|---------|
| `htmx-2.0.4.min.js` | 2.0.4 | https://github.com/bigskysoftware/htmx | 0BSD |
| `pico-2.1.1.min.css` | 2.1.1 | https://github.com/picocss/pico | MIT |
| `pdf-6.4.299.min.js` | 6.4.299 (legacy build) | https://github.com/mozilla/pdf.js | Apache-2.0 |
| `pdf.worker-6.4.299.min.js` | 6.4.299 (legacy build) | https://github.com/mozilla/pdf.js | Apache-2.0 |

htmx and Pico are permissive and require no notice retention beyond the copyright
headers already inside the files. Pico's header is intact at the top of the
CSS; htmx's minified bundle carries no header, so its copyright is recorded
here: *Copyright (c) 2020, Big Sky Software — Zero-Clause BSD.*

Versions are pinned in the filename on purpose. FastHTML's default pulled
`@picocss/pico@latest`, so the styling of an eval run depended on when it ran.

pdf.js (the Files app's PDF viewer) is Apache-2.0: its license notice is
kept at the top of both bundles, and the full license text sits next to them
as `pdfjs-LICENSE`. Upstream ships them as `build/pdf.min.mjs` and
`build/pdf.worker.min.mjs` from the `pdfjs-dist` package. They're renamed to
`.js` here because the static route serves `.js` as `text/javascript`, which a
module script and a module worker both require; `.mjs` isn't on the route's
extension list. Headless Chromium downloads PDFs rather than showing its
built-in viewer, which is why the viewer is vendored at all.

The files are pdf.js's **legacy** build (`legacy/build/` in `pdfjs-dist`), not
`build/`. The modern build calls `URL.parse`, `Math.sumPrecise` and
`Uint8Array.fromBase64` with no fallback. The Chromium that Playwright
installs here (125) has none of them, so the viewer failed before drawing a
page. The legacy build carries its own polyfills and runs on that Chromium.
Agents drive whatever browser their harness pins, so the older target is the
safe one.

## Updating

```sh
V=2.0.5
curl -fsSL "https://cdn.jsdelivr.net/npm/htmx.org@${V}/dist/htmx.min.js" \
  -o "src/open_apps/apps/assets/vendor/htmx-${V}.min.js"
```

```sh
V=6.4.299
B="https://cdn.jsdelivr.net/npm/pdfjs-dist@${V}"
D=src/open_apps/apps/assets/vendor
curl -fsSL "$B/legacy/build/pdf.min.mjs" -o "$D/pdf-${V}.min.js"
curl -fsSL "$B/legacy/build/pdf.worker.min.mjs" -o "$D/pdf.worker-${V}.min.js"
curl -fsSL "$B/LICENSE" -o "$D/pdfjs-LICENSE"
```

Then update the constant in `src/open_apps/frontend.py` (or the pdf.js
version wherever the viewer references it), delete the old file, and update
the table above. `tests/test_no_egress.py` will fail if a page ends
up referencing an origin that isn't explicitly allowed.

## What is deliberately *not* vendored

`fasthtml.js`, `surreal.js` and `css-scope-inline` are the remaining FastHTML
defaults. Nothing in this repo uses them, so they are switched off rather than
copied in — every vendored file is one more thing to keep patched.
