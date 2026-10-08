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

Both are permissive and require no notice retention beyond the copyright
headers already inside the files. Pico's header is intact at the top of the
CSS; htmx's minified bundle carries no header, so its copyright is recorded
here: *Copyright (c) 2020, Big Sky Software — Zero-Clause BSD.*

Versions are pinned in the filename on purpose. FastHTML's default pulled
`@picocss/pico@latest`, so the styling of an eval run depended on when it ran.

## Updating

```sh
V=2.0.5
curl -fsSL "https://cdn.jsdelivr.net/npm/htmx.org@${V}/dist/htmx.min.js" \
  -o "src/open_apps/apps/assets/vendor/htmx-${V}.min.js"
```

Then update the constant in `src/open_apps/frontend.py`, delete the old file,
and update the table above. `tests/test_no_egress.py` will fail if a page ends
up referencing an origin that isn't explicitly allowed.

## What is deliberately *not* vendored

`fasthtml.js`, `surreal.js` and `css-scope-inline` are the remaining FastHTML
defaults. Nothing in this repo uses them, so they are switched off rather than
copied in — every vendored file is one more thing to keep patched.
