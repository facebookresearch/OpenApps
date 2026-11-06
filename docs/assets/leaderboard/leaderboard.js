/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

/*
 * <openapps-leaderboard src="leaderboard.json"> -- the OpenApps leaderboard.
 *
 * Renders the document `uv run -m open_apps.leaderboard.build` (submission
 * bundles) or `uv run -m open_apps.leaderboard.preview` (your own logs)
 * writes: one tab per suite, a table of models ranked by success with steps,
 * task and token totals, and a page per model (click its name) with absolute
 * numbers, the launch config, breakdowns by app and layout, theme and
 * device, and every episode's outcome and action log. Every number comes
 * from that file; nothing is computed here except sort order and formatting.
 *
 * Framework-free and build-free, like the config composer next to it
 * (docs/assets/composer/): a shadow root with one stylesheet, light/dark
 * mirrored from the docs theme toggle, and the view state (suite, sort,
 * success format, token columns, open model and episode) in the URL hash so
 * a link reproduces it.
 *
 * Attributes (all optional):
 *   src         leaderboard.json URL (default: next to this script)
 *   suite       the tab to open when the URL does not pick one
 *   media-base  where screenshots and recordings are served (the preview
 *               server sets it; a relative path or same-origin URL). Without
 *               it the model page says how many there are and where to look.
 *   scheme      light | dark (default: follow <html class="dark">)
 *
 * The data is still treated as data: text only ever goes through
 * textContent, and the only links are in-page hash links and media URLs
 * under media-base.
 */
(function () {
  "use strict";

  const SCRIPT_URL = (document.currentScript && document.currentScript.src) || location.href;
  const SVG_NS = "http://www.w3.org/2000/svg";

  // Table columns, in order. `better` is the direction a first click sorts
  // (best first). `total` reads row.details.totals[total]; `group: tokens`
  // columns hide with the Tokens toggle.
  const COLUMNS = [
    { key: "rank", label: "Rank", kind: "rank", better: "asc" },
    { key: "agent", label: "Model", kind: "agent", better: "asc" },
    {
      key: "success", label: "Success", kind: "rate", better: "desc",
      description: "share of episodes whose task check passed, with a 95% CI",
    },
    {
      key: "steps", label: "Avg steps", kind: "mean", better: "asc",
      description: "mean steps per episode, over every run: successes and failures (a run that hits the step limit counts its full budget)",
    },
    { key: "total_steps", label: "Total steps", kind: "total", total: "steps", better: "asc", description: "steps summed across every run" },
    { key: "tasks", label: "Tasks", kind: "total", total: "tasks", better: "desc", description: "episodes run (re-runs of a cell count once)" },
    { key: "tokens_in", label: "Input tokens", kind: "total", total: "tokens_in", better: "asc", group: "tokens", description: "prompt tokens summed across every run, where the client reported them" },
    { key: "tokens_out", label: "Output tokens", kind: "total", total: "tokens_out", better: "asc", group: "tokens", description: "completion tokens summed across every run, where the client reported them" },
  ];
  const DEFAULT_SORT = { key: "success", dir: "desc" };

  // The model page's breakdowns: [hash value, tab label, details.breakdowns key].
  const BREAKDOWNS = [
    ["app", "App & layout", "app"],
    ["theme", "Theme", "theme"],
    ["device", "Device", "device"],
  ];

  // Config-group names (an episode's app) as people say them.
  const APP_LABELS = {
    start_page: "Start page",
    todo: "Todo",
    calendar: "Calendar",
    messenger: "Messenger",
    contacts: "Contacts",
    maps: "Maps",
    code_editor: "Code editor",
    files: "Files",
    openbanking: "Open banking",
    onlineshop: "Online shop",
    ui_library: "UI library",
    "multi-app": "Multi-app",
    other: "Other",
  };

  // Why an episode ended (the record's termination reason), as a tag.
  const ENDINGS = {
    success: "Reached the goal",
    claim: "Claimed done",
    refusal: "Refused",
    max_steps: "Ran out of steps",
    error: "Error",
  };

  // Episodes listed before "Show all".
  const LIST_PAGE = 40;

  const DASH = "—";

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "style") el.style.cssText = v;
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : String(kid));
    }
    return el;
  }

  function svg(tag, attrs, ...kids) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
    kids.forEach((kid) => el.append(kid));
    return el;
  }

  /** The OpenApps logomark (docs/assets/brand/logomark.svg), drawn here so
   *  the page stays one self-contained script. */
  function logomark() {
    const id = `oa-mark-${Math.random().toString(36).slice(2, 8)}`;
    return svg("svg", { viewBox: "0 0 28 28", width: 30, height: 30, class: "logomark", "aria-hidden": "true" },
      svg("defs", {},
        svg("linearGradient", { id, gradientUnits: "userSpaceOnUse", x1: 0, y1: 28, x2: 0, y2: 0 },
          svg("stop", { offset: "0%", "stop-color": "var(--accent)" }),
          svg("stop", { offset: "38%", "stop-color": "var(--mark)" }),
          svg("stop", { offset: "100%", "stop-color": "var(--mark)" }))),
      svg("g", { fill: "none", stroke: `url(#${id})`, "stroke-width": "4.114", "stroke-linecap": "round" },
        svg("path", { d: "M 6.749 23.565 A 11.993 11.993 0 1 1 21.351 23.565" }),
        svg("path", { d: "M 14.050 14.050 L 4.924 25.943" }),
        svg("path", { d: "M 14.050 14.050 L 23.176 25.943" })),
    );
  }

  const has = (obj, key) => obj != null && Object.prototype.hasOwnProperty.call(obj, key);
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const clamp01 = (v) => Math.min(1, Math.max(0, v));
  const srOnly = (text) => h("span", { class: "sr-only", text });
  const totalsOf = (row) => (row && row.details && row.details.totals) || null;
  const appLabel = (app) => APP_LABELS[app] || String(app || DASH).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

  // -- formatting ------------------------------------------------------------

  function pct(rate, digits = 1) {
    return isNum(rate) ? `${(rate * 100).toFixed(digits)}%` : DASH;
  }

  /** Exact counts, grouped: totals are absolute numbers, not estimates. */
  function int(v) {
    return isNum(v) ? Math.round(v).toLocaleString("en-US") : DASH;
  }

  /** Compact counts for the narrow token columns (1.2M, 34.5k). */
  function compact(v) {
    if (!isNum(v)) return DASH;
    if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (Math.abs(v) >= 1e4) return `${(v / 1e3).toFixed(1)}k`;
    return int(v);
  }

  const mean = (v) => (isNum(v) ? v.toFixed(1) : DASH);

  function deviceLabel(entry) {
    const vp = entry.viewport;
    return Array.isArray(vp) && vp.length === 2 ? `${entry.value} · ${vp[0]}×${vp[1]}` : String(entry.value);
  }

  // A local preview ranks every model it finds, complete or not
  // (open_apps.leaderboard.preview); say how much of the suite a row covers.
  const isLocal = (row) => Array.isArray(row.logs);

  /** The sortable value of a row for a column (null sorts last). */
  function sortValue(row, col) {
    switch (col.kind) {
      case "rank": return isNum(row.rank) ? row.rank : null;
      case "agent": return `${row.agent || ""} ${row.org || ""}`.toLowerCase();
      case "total": {
        const t = totalsOf(row);
        return t && isNum(t[col.total]) ? t[col.total] : null;
      }
      default: {
        const cell = row[col.key];
        return cell && isNum(cell.rate) ? cell.rate : null;
      }
    }
  }

  function sortRows(rows, key, dir) {
    const col = COLUMNS.find((c) => c.key === key) || COLUMNS[2];
    const sign = dir === "asc" ? 1 : -1;
    return rows
      .map((row, i) => ({ row, i, v: sortValue(row, col) }))
      .sort((a, b) => {
        if (a.v === null || b.v === null) {
          if (a.v !== b.v) return a.v === null ? 1 : -1; // missing data last, either way
        } else if (a.v !== b.v) {
          return (a.v < b.v ? -1 : 1) * sign;
        }
        return (a.row.rank || 0) - (b.row.rank || 0) || a.i - b.i;
      })
      .map((x) => x.row);
  }

  class OpenAppsLeaderboardElement extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this.doc = null;
      this.resetView();
      this.writtenHash = null;
      this.onHashChange = this.onHashChange.bind(this);
      this.schemeObserver = new MutationObserver(() => this.syncScheme());
    }

    resetView() {
      this.suite = null;
      this.sort = { ...DEFAULT_SORT };
      this.float = false; // success as 0-1 instead of %
      this.tokens = true; // token columns shown
      this.model = null; // open model page (row id)
      this.by = BREAKDOWNS[0][0];
      this.examples = "mistakes";
      this.episode = null;
      this.showAll = false;
    }

    connectedCallback() {
      this.syncScheme();
      this.schemeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
      window.addEventListener("hashchange", this.onHashChange);
      window.addEventListener("popstate", this.onHashChange);
      const css = this.getAttribute("stylesheet") || SCRIPT_URL.replace(/\.js(\?.*)?$/, ".css");
      this.shadowRoot.replaceChildren(
        h("link", { rel: "stylesheet", href: new URL(css, document.baseURI).href }),
        (this.root = h("div", { class: "olb" }, this.header(),
          h("p", { class: "status-line", text: "Loading the leaderboard…" }))),
      );
      this.load();
    }

    disconnectedCallback() {
      this.schemeObserver.disconnect();
      window.removeEventListener("hashchange", this.onHashChange);
      window.removeEventListener("popstate", this.onHashChange);
    }

    /** Light or dark: an explicit `scheme` attribute, else the docs toggle. */
    syncScheme() {
      const explicit = this.getAttribute("scheme");
      const dark = explicit ? explicit === "dark" : document.documentElement.classList.contains("dark");
      this.dataset.scheme = dark ? "dark" : "light";
    }

    async load() {
      const attr = this.getAttribute("src");
      const url = new URL(attr || "leaderboard.json", attr ? document.baseURI : SCRIPT_URL);
      try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        this.doc = await response.json();
        if (!this.doc || typeof this.doc.suites !== "object") throw new Error("no `suites` in the file");
      } catch (e) {
        this.root.replaceChildren(
          this.header(),
          h("p", { class: "status-line", role: "alert" },
            `Couldn't load the leaderboard from ${url.protocol === "data:" ? "the page" : url.pathname} (${e.message}). Build it with `,
            h("code", { text: "uv run -m open_apps.leaderboard.build" }), "."),
        );
        return;
      }
      this.readHash();
      if (!this.suite) this.suite = this.defaultSuite();
      this.render();
    }

    suites() {
      return Object.keys(this.doc.suites).sort();
    }

    defaultSuite() {
      const names = this.suites();
      const wanted = this.getAttribute("suite");
      if (wanted && names.includes(wanted)) return wanted;
      return names.find((n) => (this.doc.suites[n].rows || []).length) || names[0] || null;
    }

    tab() {
      return (this.suite && this.doc.suites[this.suite]) || null;
    }

    findRow(id) {
      const tab = this.tab();
      return (tab && (tab.rows || []).find((r) => r.id === id)) || null;
    }

    // -- URL hash: #suite=align_v1&sort=steps&dir=asc&fmt=float&tokens=off&model=<id>&by=theme&ex=successes&ep=3

    readHash() {
      const raw = location.hash.slice(1);
      // A plain #anchor (a heading link) is not view state.
      if (!raw.includes("=")) return;
      const params = new URLSearchParams(raw);
      const suite = params.get("suite");
      if (suite && has(this.doc.suites, suite)) this.suite = suite;
      const key = params.get("sort");
      if (key && COLUMNS.some((c) => c.key === key)) {
        const dir = params.get("dir");
        this.sort = { key, dir: dir === "asc" || dir === "desc" ? dir : COLUMNS.find((c) => c.key === key).better };
      }
      this.float = params.get("fmt") === "float";
      this.tokens = params.get("tokens") !== "off";
      this.model = params.get("model");
      const by = params.get("by");
      if (BREAKDOWNS.some(([k]) => k === by)) this.by = by;
      this.examples = params.get("ex") === "successes" ? "successes" : "mistakes";
      const ep = Number(params.get("ep"));
      this.episode = params.has("ep") && Number.isInteger(ep) ? ep : null;
      this.showAll = params.get("all") === "1";
      this.writtenHash = raw;
    }

    /** The hash for the current view, with `changes` applied. */
    hashFor(changes = {}) {
      const v = {
        suite: this.suite, sort: this.sort, float: this.float, tokens: this.tokens, model: this.model,
        by: this.by, examples: this.examples, episode: this.episode, showAll: this.showAll, ...changes,
      };
      const params = new URLSearchParams();
      if (v.suite && v.suite !== this.defaultSuite()) params.set("suite", v.suite);
      if (v.sort.key !== DEFAULT_SORT.key || v.sort.dir !== DEFAULT_SORT.dir) {
        params.set("sort", v.sort.key);
        params.set("dir", v.sort.dir);
      }
      if (v.float) params.set("fmt", "float");
      if (!v.tokens) params.set("tokens", "off");
      if (v.model) {
        params.set("model", v.model);
        if (v.by !== BREAKDOWNS[0][0]) params.set("by", v.by);
        if (v.examples !== "mistakes") params.set("ex", v.examples);
        if (v.episode !== null) params.set("ep", String(v.episode));
        if (v.showAll) params.set("all", "1");
      }
      return params.toString();
    }

    /** Mirror the view into the URL. Opening or closing a model page is a
     *  new history entry (Back returns to the table); everything else
     *  replaces the current one. */
    writeHash(push = false) {
      const hash = this.hashFor();
      this.writtenHash = hash;
      const url = location.pathname + location.search + (hash ? "#" + hash : "");
      if (location.hash.slice(1) === hash) return;
      if (push) history.pushState(history.state, "", url);
      else history.replaceState(history.state, "", url);
    }

    onHashChange() {
      if (!this.doc || location.hash.slice(1) === this.writtenHash) return;
      const raw = location.hash.slice(1);
      if (raw && !raw.includes("=")) return; // an in-page anchor
      const wasModel = this.model;
      this.resetView();
      this.readHash();
      if (!this.suite) this.suite = this.defaultSuite();
      this.render();
      if (this.model && this.model !== wasModel) this.focus(".model-name");
      else if (!this.model && wasModel) this.focus(`[data-model="${CSS.escape(wasModel)}"]`);
    }

    // -- state changes -----------------------------------------------------------

    update(changes, focusSelector, push = false) {
      Object.assign(this, changes);
      this.writeHash(push);
      this.render();
      if (focusSelector) this.focus(focusSelector);
    }

    setSort(key) {
      const col = COLUMNS.find((c) => c.key === key);
      const sort = this.sort.key === key
        ? { key, dir: this.sort.dir === "asc" ? "desc" : "asc" }
        : { key, dir: col.better };
      this.update({ sort }, `[data-sort="${key}"]`);
    }

    setSuite(name, focus) {
      if (name === this.suite) return;
      this.update({ suite: name, model: null }, focus ? `[role="tab"][data-suite="${CSS.escape(name)}"]` : null);
    }

    openModel(id) {
      this.update({ model: id, by: BREAKDOWNS[0][0], examples: "mistakes", episode: null, showAll: false }, null, true);
      this.focus(".model-name");
      this.scrollIntoViewIfNeeded();
    }

    closeModel() {
      const id = this.model;
      this.update({ model: null, episode: null, showAll: false }, null, true);
      if (id) this.focus(`[data-model="${CSS.escape(id)}"]`);
    }

    scrollIntoViewIfNeeded() {
      const top = this.getBoundingClientRect().top;
      if (top < 0) this.scrollIntoView({ block: "start" });
    }

    focus(selector) {
      const el = this.shadowRoot.querySelector(selector);
      if (el) el.focus({ preventScroll: true });
    }

    // -- render -------------------------------------------------------------------

    header() {
      return h("header", { class: "head" },
        h("p", { class: "brand" }, logomark(), h("span", { class: "wordmark", text: "OpenApps" }),
          h("span", { class: "headline", role: "heading", "aria-level": "2", text: "Leaderboard" })),
        h("p", { class: "subtitle", text: "Alignment score" }),
      );
    }

    columns() {
      return COLUMNS.filter((c) => this.tokens || c.group !== "tokens");
    }

    render() {
      const names = this.suites();
      const tab = this.tab();
      const row = this.model ? this.findRow(this.model) : null;
      const kids = [this.header()];
      if (row) {
        kids.push(this.modelPage(row, tab));
      } else {
        if (names.length > 1) kids.push(this.tabs(names));
        kids.push(tab ? this.panel(tab, names.length > 1) : this.empty());
      }
      kids.push(this.footnote());
      this.root.replaceChildren(...kids);
    }

    tabs(names) {
      const onKey = (event) => {
        const i = names.indexOf(this.suite);
        const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: names.length - 1 }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        this.setSuite(names[(next + names.length) % names.length], true);
      };
      return h("div", { class: "tabs", role: "tablist", "aria-label": "Suite", onkeydown: onKey },
        names.map((name) => {
          const selected = name === this.suite;
          const n = (this.doc.suites[name].rows || []).length;
          return h("button", {
            class: "tab", role: "tab", type: "button", id: `tab-${name}`, "data-suite": name,
            "aria-selected": selected ? "true" : "false", "aria-controls": "suite-panel",
            tabindex: selected ? "0" : "-1", onclick: () => this.setSuite(name, false),
          }, name, h("span", { class: "tab-count", text: String(n) }));
        }),
      );
    }

    panel(tab, tabbed) {
      const rows = tab.rows || [];
      const tasks = rows.reduce((n, r) => n + ((totalsOf(r) || {}).tasks || 0), 0);
      const bits = [
        `${rows.length} ${rows.length === 1 ? "model" : "models"}`,
        tasks ? `${int(tasks)} tasks` : null,
        this.doc.generated_at ? `built ${String(this.doc.generated_at).slice(0, 10)}` : null,
      ].filter(Boolean);
      return h("section", tabbed ? { id: "suite-panel", role: "tabpanel", "aria-labelledby": `tab-${tab.suite || this.suite}` } : { id: "suite-panel" },
        h("div", { class: "toolbar" },
          h("p", { class: "meta-line" }, h("span", { class: "suite-name", text: tab.suite || this.suite }), " ", bits.join(" · ")),
          rows.length ? this.controls() : null),
        rows.length ? this.table(rows, tab) : this.empty(),
      );
    }

    /** Success as % or 0-1, and the token columns on or off. */
    controls() {
      const seg = (value, label, title) => h("button", {
        type: "button", class: "seg", "aria-pressed": this.float === value ? "true" : "false", title,
        "data-fmt": value ? "float" : "pct", onclick: () => this.update({ float: value }, `[data-fmt="${value ? "float" : "pct"}"]`),
      }, label);
      return h("div", { class: "controls" },
        h("span", { class: "control", role: "group", "aria-label": "Show success as" },
          h("span", { class: "control-label", "aria-hidden": "true", text: "Success" }),
          h("span", { class: "segmented" }, seg(false, "%", "success as a percentage"), seg(true, "0–1", "success as a fraction from 0 to 1"))),
        h("button", {
          type: "button", class: "toggle", "data-toggle": "tokens", "aria-pressed": this.tokens ? "true" : "false",
          onclick: () => this.update({ tokens: !this.tokens }, '[data-toggle="tokens"]'),
        }, h("span", { class: "box", "aria-hidden": "true" }), "Tokens"),
      );
    }

    /** A success rate in the chosen format. */
    rate(value) {
      if (!isNum(value)) return DASH;
      return this.float ? value.toFixed(3) : pct(value);
    }

    empty() {
      return h("div", { class: "empty", role: "status" },
        h("p", { class: "empty-title", text: "No models yet." }),
        h("p", { class: "empty-body" },
          "Run a sweep, then see it here with ", h("code", { text: "uv run -m open_apps.leaderboard.preview" }), "."),
      );
    }

    table(rows, tab) {
      const cols = this.columns();
      const sorted = sortRows(rows, this.sort.key, this.sort.dir);
      const col = COLUMNS.find((c) => c.key === this.sort.key) || COLUMNS[2];
      return h("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": "Leaderboard table (scrolls sideways)" },
        h("table", { class: "board" },
          h("caption", { class: "sr-only", text:
            `${tab.suite || this.suite} leaderboard, sorted by ${col.label} ${this.sort.dir === "asc" ? "ascending" : "descending"}. ` +
            "Column headers are buttons that sort; model names open the model's page." }),
          h("thead", {}, h("tr", {}, cols.map((c) => this.th(c)))),
          h("tbody", {}, sorted.map((row) => this.tr(row, cols))),
        ),
      );
    }

    th(col) {
      const active = this.sort.key === col.key;
      // ⇅ rather than ↕, which has an emoji form some fonts fall back to.
      const arrow = active ? (this.sort.dir === "asc" ? "↑" : "↓") : "⇅";
      return h("th", {
        scope: "col", class: `col-${col.kind} col-${col.key}`,
        "aria-sort": active ? (this.sort.dir === "asc" ? "ascending" : "descending") : null,
      },
        h("button", {
          class: "sort", type: "button", "data-sort": col.key, title: col.description || null,
          onclick: () => this.setSort(col.key),
        },
          h("span", { class: "th-label", text: col.label }),
          h("span", { class: "arrow", "aria-hidden": "true", text: arrow }),
        ),
      );
    }

    tr(row, cols) {
      const t = totalsOf(row) || {};
      return h("tr", { class: "row" }, cols.map((col) => {
        switch (col.kind) {
          case "rank": return h("td", { class: "col-rank" }, h("span", { class: "rank", text: isNum(row.rank) ? String(row.rank) : DASH }));
          case "agent": return this.agentCell(row);
          case "rate": return this.rateCell(row[col.key], col.key);
          case "mean": return this.numCell(row[col.key] && row[col.key].rate, mean, "not reported");
          case "total": return this.numCell(t[col.total], col.group === "tokens" ? compact : int, "not reported", col.group === "tokens" ? int(t[col.total]) : null);
          default: return h("td", { text: DASH });
        }
      }));
    }

    agentCell(row) {
      const coverage = isLocal(row) && isNum(row.covered) && isNum(row.n_cells)
        ? `${row.covered}/${row.n_cells} cells` : null;
      const sub = [row.org, row.model && row.model !== row.agent ? row.model : null, coverage].filter(Boolean).join(" · ");
      return h("th", { scope: "row", class: "col-agent" },
        h("a", {
          class: "model-link", href: `#${this.hashFor({ model: row.id, episode: null, showAll: false, by: BREAKDOWNS[0][0], examples: "mistakes" })}`,
          "data-model": row.id,
          onclick: (event) => {
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
            event.preventDefault();
            this.openModel(row.id);
          },
        }, h("span", { class: "agent-name", text: row.agent || row.id }), h("span", { class: "go", "aria-hidden": "true", text: "→" })),
        sub ? h("span", { class: "agent-sub", text: sub }) : null,
      );
    }

    rateCell(cell, key) {
      const rate = cell && cell.rate;
      if (!isNum(rate)) return h("td", { class: `num na col-${key}`, title: "not measured" }, h("span", { "aria-hidden": "true", text: DASH }), srOnly("not measured"));
      const lo = cell.ci_low, hi = cell.ci_high;
      const hasCI = isNum(lo) && isNum(hi);
      const ciText = hasCI ? `95% CI ${this.rate(lo)} to ${this.rate(hi)}, n=${cell.n}` : `n=${cell.n}`;
      return h("td", { class: `num col-${key}`, title: ciText },
        h("span", { class: "val", text: this.rate(rate) }),
        hasCI ? h("span", { class: "whisker", "aria-hidden": "true" },
          h("span", { class: "ci", style: `left:${clamp01(lo) * 100}%;width:${Math.max(0, clamp01(hi) - clamp01(lo)) * 100}%` }),
          h("span", { class: "dot", style: `left:${clamp01(rate) * 100}%` })) : null,
        srOnly(` (${ciText})`),
      );
    }

    numCell(value, fmt, why, title) {
      if (!isNum(value)) return h("td", { class: "num na", title: why }, h("span", { "aria-hidden": "true", text: DASH }), srOnly(why));
      return h("td", { class: "num", title: title || null }, h("span", { class: "val", text: fmt(value) }));
    }

    // -- the model page -----------------------------------------------------------

    modelPage(row, tab) {
      const d = row.details || {};
      const t = d.totals || {};
      const episodes = d.episodes || [];
      const where = [
        isNum(t.tasks) ? `${int(t.tasks)} tasks` : null,
        isNum(t.apps) ? `${t.apps} ${t.apps === 1 ? "app" : "apps"}` : null,
      ].filter(Boolean).join(" · ");
      const context = [
        isNum(t.distinct_tasks) ? `${t.distinct_tasks} distinct tasks` : null,
        tab.suite || this.suite,
        isNum(row.covered) && isNum(row.n_cells) ? `${row.covered}/${row.n_cells} cells` : null,
        row.date ? `${isLocal(row) ? "last run" : "packed"} ${row.date}` : null,
      ].filter(Boolean).join(" · ");
      return h("section", { class: "model", "aria-labelledby": "model-name" },
        h("nav", { class: "crumbs", "aria-label": "Breadcrumb" },
          h("a", {
            href: `#${this.hashFor({ model: null, episode: null, showAll: false })}`,
            onclick: (event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
              event.preventDefault();
              this.closeModel();
            },
          }, "Leaderboard"),
          h("span", { class: "sep", "aria-hidden": "true", text: "/" }),
          h("span", { "aria-current": "page", text: row.agent || row.id })),
        h("div", { class: "model-head" },
          h("div", {},
            row.org || row.model ? h("p", { class: "model-org", text: [row.org, row.model && row.model !== row.agent ? row.model : null].filter(Boolean).join(" · ") }) : null,
            h("h3", { class: "model-name", id: "model-name", tabindex: "-1" },
              h("span", { text: row.agent || row.id }),
              isNum(row.rank) ? h("span", { class: "rank-badge", text: `Rank #${row.rank}` }) : null),
            context ? h("p", { class: "model-context", text: context }) : null),
          where ? h("p", { class: "model-where", text: where }) : null),
        this.tiles(row, t),
        this.config(row, d),
        this.breakdowns(d),
        this.examplesPanel(row, episodes),
      );
    }

    tiles(row, t) {
      const s = row.success || {};
      const ci = isNum(s.ci_low) && isNum(s.ci_high) ? ` · 95% CI ${this.rate(s.ci_low)}–${this.rate(s.ci_high)}` : "";
      const tile = (label, value, sub, cls) => h("div", { class: `tile ${cls || ""}` },
        h("p", { class: "tile-label", text: label }),
        h("p", { class: "tile-value", text: value }),
        sub ? h("p", { class: "tile-sub", text: sub }) : null);
      const avgIn = isNum(t.tokens_in) && t.tasks ? t.tokens_in / t.tasks : null;
      const avgOut = isNum(t.tokens_out) && t.tasks ? t.tokens_out / t.tasks : null;
      return h("div", { class: "tiles" },
        tile("Success", this.rate(s.rate), isNum(t.successes) ? `${int(t.successes)} successful / ${int(t.scored)} tasks${ci}` : null, "good"),
        tile("Mistakes", int(t.mistakes), isNum(t.mistakes) && t.scored ? `${pct(t.mistakes / t.scored)} of tasks failed their check` : null, t.mistakes ? "bad" : "bad zero"),
        tile("Steps", int(t.steps), isNum(t.steps_mean) ? `avg ${mean(t.steps_mean)} · median ${mean(t.steps_median)} · avg on successes ${mean(t.steps_mean_success)}` : null),
        tile("Tokens in / out", `${compact(t.tokens_in)} / ${compact(t.tokens_out)}`,
          isNum(avgIn) ? `${int(t.tokens_in)} in · ${int(t.tokens_out)} out · ${compact(avgIn)} / ${compact(avgOut)} per task` : "not reported by the client"),
      );
    }

    config(row, d) {
      const sections = d.config || [];
      const facts = [
        isNum(d.config_variants) && d.config_variants > 1 ? `${d.config_variants} agent configs across these runs; the latest is shown` : null,
        d.git_commit ? `commit ${String(d.git_commit).slice(0, 12)}` : null,
      ].filter(Boolean).join(" · ");
      return h("section", { class: "panel config", "aria-labelledby": "config-title" },
        h("div", { class: "panel-head" },
          h("h4", { class: "panel-title", id: "config-title", text: "Launch config" }),
          h("p", { class: "panel-note", text: facts || (!sections.length ? null : isLocal(row) ? "from the run's saved config.yaml" : "from the bundle's agent.yaml") })),
        d.command ? this.command("Reproduce", d.command) : null,
        sections.length
          ? h("div", { class: "config-grid" }, sections.map((sec) => this.configSection(sec)))
          : h("p", { class: "muted pad", text: "No config was found next to these runs." }),
        isLocal(row) ? this.logs(row) : null,
      );
    }

    /** A local-preview row: the logs dirs it was read from, not a bundle. */
    logs(row) {
      const dupes = isNum(row.duplicates_dropped) && row.duplicates_dropped > 0
        ? ` ${row.duplicates_dropped} re-run duplicates were dropped (the later run, or the re-run of an error, is kept).` : "";
      return h("div", { class: "logs" },
        h("p", { class: "label", text: "Logs" }),
        h("ul", {}, row.logs.map((p) => h("li", {}, h("code", { text: String(p) })))),
        h("p", { class: "muted small", text:
          `Local preview: scored from these runs, not validated against the ${this.suite} suite lock.${dupes}` }),
      );
    }

    command(label, text) {
      const copy = h("button", { type: "button", class: "copy", text: "Copy" });
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(text);
          copy.textContent = "Copied";
        } catch (e) {
          copy.textContent = "Select and copy";
        }
        setTimeout(() => { copy.textContent = "Copy"; }, 1600);
      });
      return h("div", { class: "command" },
        h("p", { class: "label", text: label }),
        h("div", { class: "command-box" }, h("code", { text }), copy));
    }

    configSection(sec) {
      return h("div", { class: "config-sec" },
        h("p", { class: "label", text: sec.name }),
        h("table", { class: "kv" },
          h("caption", { class: "sr-only", text: `${sec.name} config` }),
          h("tbody", {}, (sec.items || []).map(([k, v]) => {
            const text = v === null || v === undefined ? "null" : typeof v === "object" ? JSON.stringify(v) : String(v);
            return h("tr", {},
              h("th", { scope: "row", text: k }),
              h("td", {}, text.length > 120
                ? h("details", {}, h("summary", { text: `${text.slice(0, 80)}…` }), h("pre", { text }))
                : h("code", { text })));
          })),
        ),
      );
    }

    breakdowns(d) {
      const data = d.breakdowns || {};
      const names = BREAKDOWNS.map(([k]) => k);
      const onKey = (event) => {
        const i = names.indexOf(this.by);
        const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: names.length - 1 }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        const by = names[(next + names.length) % names.length];
        this.update({ by }, `[data-by="${by}"]`);
      };
      const [, label, key] = BREAKDOWNS.find(([k]) => k === this.by) || BREAKDOWNS[0];
      const rows = data[key] || [];
      const maxMistakes = Math.max(1, ...rows.map((r) => r.mistakes || 0));
      return h("section", { class: "panel breakdown", "aria-labelledby": "breakdown-title" },
        h("div", { class: "panel-head" },
          h("h4", { class: "panel-title", id: "breakdown-title", text: "Breakdown" }),
          h("div", { class: "subtabs", role: "tablist", "aria-label": "Break down by", onkeydown: onKey },
            BREAKDOWNS.map(([k, l]) => h("button", {
              type: "button", class: "subtab", role: "tab", "data-by": k, "aria-selected": k === this.by ? "true" : "false",
              "aria-controls": "breakdown-table", tabindex: k === this.by ? "0" : "-1", onclick: () => this.update({ by: k }, `[data-by="${k}"]`),
            }, l)))),
        rows.length ? h("div", { class: "table-wrap", id: "breakdown-table", role: "tabpanel", tabindex: "0", "aria-label": `${label} breakdown` },
          h("table", { class: "board breakdown-table" },
            h("thead", {}, h("tr", {},
              [this.by === "app" ? "App / layout" : label, "Tasks", "Success", "Mistakes", "Avg steps"].map((t, i) => h("th", { scope: "col", class: i ? "num" : null, text: t })))),
            h("tbody", {}, this.by === "app"
              ? rows.flatMap((app) => [
                this.breakdownRow(appLabel(app.value), app, maxMistakes, "group"),
                ...(data.layout || []).filter((l) => l.app === app.value)
                  .map((l) => this.breakdownRow(l.value, l, maxMistakes, "sub")),
              ])
              : rows.map((r) => this.breakdownRow(key === "device" ? deviceLabel(r) : String(r.value), r, maxMistakes)))))
          : h("p", { class: "muted pad", text: "No episodes to break down." }),
      );
    }

    breakdownRow(name, r, maxMistakes, kind) {
      const rate = r.success_rate;
      return h("tr", { class: kind ? `b-${kind}` : null },
        h("th", { scope: "row" }, kind === "sub" ? h("span", { class: "indent", "aria-hidden": "true", text: "↳ " }) : null, name),
        h("td", { class: "num", text: int(r.tasks) }),
        h("td", { class: "num bar-cell" },
          h("span", { class: "val", text: this.rate(rate) }),
          isNum(rate) ? h("span", { class: "meter good", "aria-hidden": "true" }, h("span", { style: `width:${clamp01(rate) * 100}%` })) : null),
        h("td", { class: "num bar-cell" },
          h("span", { class: "val", text: int(r.mistakes) }),
          h("span", { class: "meter bad", "aria-hidden": "true" }, h("span", { style: `width:${clamp01((r.mistakes || 0) / maxMistakes) * 100}%` }))),
        h("td", { class: "num", text: mean(r.steps_mean) }),
      );
    }

    // -- examples -------------------------------------------------------------------

    examplesPanel(row, episodes) {
      const mistakes = episodes.filter((e) => e.success === false);
      const successes = episodes.filter((e) => e.success === true);
      const list = this.examples === "successes" ? successes : mistakes;
      const selected = list.find((e) => e.id === this.episode) || list[0] || null;
      const shown = this.showAll ? list : list.slice(0, LIST_PAGE);
      const kinds = [["mistakes", "Mistakes", mistakes.length], ["successes", "Successes", successes.length]];
      const onKey = (event) => {
        if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const ex = this.examples === "mistakes" ? "successes" : "mistakes";
        this.update({ examples: ex, episode: null, showAll: false }, `[data-ex="${ex}"]`);
      };
      return h("section", { class: "panel examples", "aria-labelledby": "examples-title" },
        h("div", { class: "panel-head" },
          h("h4", { class: "panel-title", id: "examples-title", text: "Examples" }),
          h("div", { class: "subtabs", role: "tablist", "aria-label": "Episodes", onkeydown: onKey },
            kinds.map(([k, l, n]) => h("button", {
              type: "button", class: "subtab", role: "tab", "data-ex": k, "aria-selected": k === this.examples ? "true" : "false",
              "aria-controls": "examples-body", tabindex: k === this.examples ? "0" : "-1",
              onclick: () => this.update({ examples: k, episode: null, showAll: false }, `[data-ex="${k}"]`),
            }, l, h("span", { class: "tab-count", text: String(n) }))))),
        h("div", { class: "examples-body", id: "examples-body", role: "tabpanel" },
          list.length ? [
            h("div", { class: "episode-list" },
              h("table", { class: "board episodes" },
                h("caption", { class: "sr-only", text: `${this.examples}: choose an episode to see its outcome and action log` }),
                h("thead", {}, h("tr", {}, ["Task", "Ended", "Steps"].map((t, i) => h("th", { scope: "col", class: i === 2 ? "num" : null, text: t })))),
                h("tbody", {}, shown.map((e) => this.episodeRow(e, selected)))),
              list.length > shown.length ? h("button", {
                type: "button", class: "more", onclick: () => this.update({ showAll: true }),
              }, `Show all ${list.length}`) : null,
              h("p", { class: "list-note", text: `Showing ${shown.length} of ${list.length}` })),
            selected ? this.episodeDetail(row, selected) : null,
          ] : h("p", { class: "muted pad", text: this.examples === "mistakes" ? "No mistakes: every scored task passed its check." : "No successes yet." }),
        ),
      );
    }

    episodeRow(e, selected) {
      const active = selected && e.id === selected.id;
      const cell = [appLabel(e.app), e.layout !== "default" ? e.layout : null, e.theme !== "default" ? e.theme : null, e.device !== "desktop" ? e.device : null].filter(Boolean).join(" · ");
      return h("tr", { class: active ? "ep active" : "ep" },
        h("th", { scope: "row" },
          h("button", {
            type: "button", class: "ep-pick", "data-ep": String(e.id), "aria-pressed": active ? "true" : "false",
            onclick: () => this.update({ episode: e.id }, `[data-ep="${e.id}"]`),
          }, h("span", { class: "ep-task", text: e.task || `episode ${e.id}` }), h("span", { class: "ep-cell", text: cell }))),
        h("td", {}, h("span", { class: `tag ${e.success ? "good" : "bad"}`, text: ENDINGS[e.termination] || String(e.termination || DASH) })),
        h("td", { class: "num", text: int(e.steps) }),
      );
    }

    mediaURL(row, e, name) {
      const attr = this.getAttribute("media-base");
      if (!attr || /^[a-z][a-z0-9+.-]*:/i.test(attr)) return null;
      const base = new URL(attr, document.baseURI);
      if (base.origin !== location.origin) return null;
      return new URL(`${encodeURIComponent(row.id)}/${e.id}/${name}`, base.href.endsWith("/") ? base.href : base.href + "/").href;
    }

    episodeDetail(row, e) {
      const meta = [appLabel(e.app), `layout ${e.layout}`, `theme ${e.theme}`, e.device, `${int(e.steps)} steps`,
        isNum(e.tokens_in) ? `${compact(e.tokens_in)} in / ${compact(e.tokens_out)} out` : null,
        isNum(e.wall_s) ? `${Math.round(e.wall_s)}s` : null].filter(Boolean).join(" · ");
      const outcome = [
        e.message ? h("p", {}, h("span", { class: "k", text: "Agent said " }), h("q", { text: e.message })) : null,
        e.error ? h("p", { class: "err" }, h("span", { class: "k", text: "Error " }), e.error) : null,
        e.final_action ? h("p", {}, h("span", { class: "k", text: "Last action " }), h("code", { text: e.final_action })) : null,
        Array.isArray(e.changes)
          ? (e.changes.length
            ? h("div", {}, h("p", { class: "k", text: "Changed" }), h("ul", { class: "changes" }, e.changes.map((c) => h("li", { text: c }))))
            : h("p", {}, h("span", { class: "k", text: "Changed " }), "nothing in any app"))
          : null,
      ];
      return h("article", { class: `episode ${e.success ? "is-good" : "is-bad"}`, "aria-labelledby": "episode-title" },
        h("div", { class: "episode-head" },
          h("p", { class: "eyebrow-sm", text: `${e.success ? "Success" : "Mistake"} · episode ${e.id}` }),
          h("span", { class: `tag ${e.success ? "good" : "bad"}`, text: ENDINGS[e.termination] || String(e.termination || DASH) })),
        h("h5", { class: "episode-title", id: "episode-title", text: e.task || `episode ${e.id}` }),
        h("p", { class: "episode-meta", text: meta }),
        h("div", { class: "quote expected" }, h("p", { class: "label", text: "Goal" }), h("p", { text: e.goal || DASH })),
        h("div", { class: `quote ${e.success ? "expected" : "actual"}` }, h("p", { class: "label", text: "Outcome" }), outcome),
        this.media(row, e),
        this.trace(row, e),
        e.command ? this.command("Rerun this episode", e.command) : null,
      );
    }

    media(row, e) {
      const m = e.media || {};
      const shots = Array.isArray(m.screenshots) ? m.screenshots : [];
      if (!shots.length && !m.video) return null;
      const video = m.video && this.mediaURL(row, e, "video.webm");
      const canShow = shots.length ? this.mediaURL(row, e, `screenshot/${shots[0]}.png`) : video;
      if (!canShow) {
        const what = [shots.length ? `${shots.length} screenshots` : null, m.video ? "a recording" : null].filter(Boolean).join(" and ");
        return h("p", { class: "muted small media-note" }, `This episode has ${what} next to its logs; the served preview (`,
          h("code", { text: "uv run -m open_apps.leaderboard.preview" }), ") shows them.");
      }
      return h("div", { class: "media" },
        video ? h("video", { class: "recording", src: video, controls: true, preload: "metadata", "aria-label": "Episode recording" }) : null,
        shots.length ? h("div", { class: "shots", role: "list", "aria-label": "Screenshots by step" },
          shots.map((step) => {
            const url = this.mediaURL(row, e, `screenshot/${step}.png`);
            return h("a", { role: "listitem", class: "shot", href: url, target: "_blank", rel: "noopener", title: `step ${step}` },
              h("img", { src: url, alt: `Screenshot at step ${step}`, loading: "lazy" }),
              h("span", { class: "shot-step", text: String(step) }));
          })) : null,
      );
    }

    trace(row, e) {
      const steps = e.trace || [];
      if (!steps.length) return null;
      return h("details", { class: "trace", open: steps.length <= 12 },
        h("summary", {}, `Action log · ${steps.length} ${steps.length === 1 ? "step" : "steps"}`),
        h("ol", { class: "steps" }, steps.map((s) => h("li", { class: s.error ? "step has-error" : "step" },
          h("span", { class: "step-n", text: String(s.step) }),
          h("div", { class: "step-body" },
            s.action ? h("code", { class: "step-action", text: s.action }) : null,
            s.think ? h("p", { class: "step-think", text: s.think }) : null,
            s.error ? h("p", { class: "step-error", text: s.error }) : null)))),
      );
    }

    footnote() {
      const boot = this.doc && this.doc.bootstrap;
      const method = boot && typeof boot.ci_method === "string" ? boot.ci_method : null;
      return h("div", { class: "footnote" },
        h("p", {},
          "Success is the share of episodes whose final app state passed the task's own check, with a 95% CI (",
          boot ? `${boot.n_boot}-sample task-clustered bootstrap` : "task-clustered bootstrap",
          "; Wilson for an all-0 or all-1 rate). A mistake is an episode that failed its check; \"ended\" is why the episode stopped. ",
          "Steps and tokens are per episode, totals are summed across every run, and re-runs of a cell count once. ",
          this.doc && this.doc.local
            ? "This is a local preview of your own runs, not checked against a suite lock: a model may cover only part of its suite."
            : "Every number is recomputed from the episodes by the scorer, never taken from a summary."),
        method ? h("details", { class: "ci-method" },
          h("summary", { text: "How the intervals are computed" }),
          h("p", { text: method })) : null,
      );
    }
  }

  window.OpenAppsLeaderboard = { sortRows, COLUMNS };
  if (!customElements.get("openapps-leaderboard")) {
    customElements.define("openapps-leaderboard", OpenAppsLeaderboardElement);
  }
})();
