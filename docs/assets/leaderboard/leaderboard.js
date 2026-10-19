/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

/*
 * <openapps-leaderboard src="leaderboard.json"> -- the OpenApps-Align table.
 *
 * Renders the document `uv run -m open_apps.leaderboard.build` writes: one
 * tab per frozen suite, ranked rows sortable on every column, partial
 * submissions listed apart, and a per-row drill-down. Every number comes from
 * that file; nothing is computed here except sort order and formatting.
 *
 * Framework-free and build-free, like the config composer next to it
 * (docs/assets/composer/): a shadow root with one stylesheet, light/dark
 * mirrored from the docs theme toggle, and the view state (suite, sort,
 * expanded row) in the URL hash so a link reproduces it.
 *
 * Attributes (all optional):
 *   src              leaderboard.json URL (default: next to this script)
 *   composer         the config composer page, for Reproduce (default ../composer/)
 *   repo             GitHub repo URL for bundle links
 *   branch           branch for bundle links (default main)
 *   suite            the tab to open when the URL does not pick one
 *   delta-threshold  |held-out - public| aligned rate that flags a verified
 *                    row as a possible overfit (default 0.1 = 10 points)
 *   scheme           light | dark (default: follow <html class="dark">)
 *
 * The data is from reviewed bundles, but it is still treated as data: text
 * only ever goes through textContent, and links are https or repo paths.
 */
(function () {
  "use strict";

  const SCRIPT_URL = (document.currentScript && document.currentScript.src) || location.href;
  const DEFAULT_REPO = "https://github.com/facebookresearch/OpenApps";
  // The composer reads `#agent=<option>` today. Its catalog does not offer
  // the `benchmark` group yet (config/benchmark is newer than the composer),
  // and it drops hash keys it does not know -- so a `benchmark=` here would be
  // silently ignored. Flip this once the composer lists benchmark=align_v1.
  const COMPOSER_KNOWS_BENCHMARK = false;

  // Table columns, in order. `metric` reads row[metric].rate; `better` is the
  // direction a first click sorts (best first). Labels and descriptions are
  // taken from the document's `columns` when it has them.
  const COLUMNS = [
    { key: "rank", label: "Rank", kind: "rank", better: "asc" },
    { key: "agent", label: "Agent", kind: "agent", better: "asc" },
    { key: "aligned", label: "Aligned", kind: "rate", better: "desc" },
    { key: "success", label: "Success", kind: "rate", better: "desc" },
    {
      key: "worst", label: "Worst variation", kind: "worst", better: "desc",
      description: "aligned rate in the agent's worst variation cell of the core block (the same tasks in every cell)",
    },
    { key: "fooled", label: "Fooled", kind: "rate", better: "asc", note: "exposed only" },
    { key: "harm", label: "Harm", kind: "rate", better: "asc" },
    { key: "honest", label: "Honest", kind: "rate", better: "desc" },
    { key: "ask_at_ambiguous", label: "Ask@ambiguous", kind: "rate", better: "desc" },
    { key: "steps", label: "Steps", kind: "mean", better: "asc" },
    { key: "tokens", label: "Tokens", kind: "count", better: "asc" },
  ];
  const DEFAULT_SORT = { key: "aligned", dir: "desc" };

  // Drill-down axes (aggregate's by_axis keys), in reading order; any other
  // axis the data has is shown after these under its own name. A per-app
  // axis is keyed "<app>.<axis>" (e.g. "todo.content") and shown in place of
  // its app-less "<axis>" key, which only holds the cell's combination of
  // apps; the app-less key is the fallback for rows built before per-app keys.
  const AXES = [
    ["theme", "Appearance"],
    ["content", "Content"],
    ["layout", "Layout"],
    ["device", "Device"],
    ["phrasing", "Goal phrasing"],
    ["event", "Event category"],
    ["pop_ups", "Pop-ups"],
  ];

  // Config-group names (the "<app>" of a per-app axis) as people say them.
  const APP_LABELS = {
    start_page: "Start page",
    todo: "Todo",
    calendar: "Calendar",
    messenger: "Messenger",
    maps: "Maps",
    code_editor: "Code editor",
    openbanking: "Open banking",
    onlineshop: "Online shop",
    ui_library: "UI library",
  };

  const DASH = "—";
  const MINUS = "−";

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

  const has = (obj, key) => obj != null && Object.prototype.hasOwnProperty.call(obj, key);
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const clamp01 = (v) => Math.min(1, Math.max(0, v));
  const httpsURL = (u) => (typeof u === "string" && /^https:\/\/[^\s"'<>]+$/.test(u) ? u : null);
  const srOnly = (text) => h("span", { class: "sr-only", text });

  // -- formatting ------------------------------------------------------------

  function pct(rate, digits = 1) {
    return isNum(rate) ? `${(rate * 100).toFixed(digits)}%` : DASH;
  }

  function points(delta) {
    const v = Math.round(delta * 1000) / 10;
    return `${v > 0 ? "+" : v < 0 ? MINUS : ""}${Math.abs(v).toFixed(1)} pts`;
  }

  function count(v) {
    if (!isNum(v)) return DASH;
    if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (Math.abs(v) >= 1e4) return `${(v / 1e3).toFixed(1)}k`;
    return Math.round(v).toLocaleString("en-US");
  }

  const mean = (v) => (isNum(v) ? v.toFixed(1) : DASH);

  /** "todo.content" -> "Todo · content". */
  function axisLabel(key) {
    const dot = key.indexOf(".");
    const app = key.slice(0, dot);
    const fallback = app.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
    return `${APP_LABELS[app] || fallback} · ${key.slice(dot + 1)}`;
  }

  /** ``[[key, label], ...]`` in reading order: per-app keys in place of the
   *  app-less axis they break down, then axes outside AXES. */
  function axisOrder(axes) {
    const keys = Object.keys(axes);
    const perApp = keys.filter((k) => k.includes(".")).sort();
    const axisOf = (k) => k.slice(k.indexOf(".") + 1);
    const split = new Set(perApp.map(axisOf));
    const order = [];
    for (const [axis, label] of AXES) {
      if (split.has(axis)) perApp.filter((k) => axisOf(k) === axis).forEach((k) => order.push([k, axisLabel(k)]));
      else if (has(axes, axis)) order.push([axis, label]);
    }
    const known = new Set(AXES.map(([a]) => a));
    perApp.filter((k) => !known.has(axisOf(k))).forEach((k) => order.push([k, axisLabel(k)]));
    keys.filter((k) => !k.includes(".") && !known.has(k) && !split.has(k)).sort().forEach((k) => order.push([k, k]));
    return order;
  }

  const unscorableNote = (n) => (isNum(n) && n > 0
    ? h("span", { class: "note-unscorable", title: "episodes whose final state could not be read: counted, never scored", text: `${n} unscorable` })
    : null);

  /** The sortable value of a row for a column (null sorts last). */
  function sortValue(row, col) {
    switch (col.kind) {
      case "rank": return isNum(row.rank) ? row.rank : null;
      case "agent": return `${row.agent || ""} ${row.org || ""}`.toLowerCase();
      case "worst": {
        const w = row.worst_variation && row.worst_variation.aligned;
        return w && isNum(w.rate) ? w.rate : null;
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
      this.suite = null;
      this.sort = { ...DEFAULT_SORT };
      this.open = new Set();
      this.writtenHash = null;
      this.onHashChange = this.onHashChange.bind(this);
      this.schemeObserver = new MutationObserver(() => this.syncScheme());
    }

    connectedCallback() {
      this.syncScheme();
      this.schemeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
      window.addEventListener("hashchange", this.onHashChange);
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
            `Couldn't load the leaderboard from ${url.pathname} (${e.message}). Build it with `,
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

    // -- URL hash: #suite=align_v1&sort=harm&dir=asc&open=<bundle id> ---------

    readHash() {
      const raw = location.hash.slice(1);
      // A plain #anchor (a heading link such as #submit) is not table state.
      if (!raw.includes("=")) return;
      const params = new URLSearchParams(raw);
      const suite = params.get("suite");
      if (suite && has(this.doc.suites, suite)) this.suite = suite;
      const key = params.get("sort");
      if (key && COLUMNS.some((c) => c.key === key)) {
        const dir = params.get("dir");
        this.sort = { key, dir: dir === "asc" || dir === "desc" ? dir : COLUMNS.find((c) => c.key === key).better };
      }
      this.open = new Set(params.getAll("open"));
      this.writtenHash = raw;
    }

    writeHash() {
      const params = new URLSearchParams();
      if (this.suite && this.suite !== this.defaultSuite()) params.set("suite", this.suite);
      if (this.sort.key !== DEFAULT_SORT.key || this.sort.dir !== DEFAULT_SORT.dir) {
        params.set("sort", this.sort.key);
        params.set("dir", this.sort.dir);
      }
      for (const id of this.open) params.append("open", id);
      const hash = params.toString();
      this.writtenHash = hash;
      const url = location.pathname + location.search + (hash ? "#" + hash : "");
      if (location.hash.slice(1) !== hash) history.replaceState(history.state, "", url);
    }

    onHashChange() {
      if (!this.doc || location.hash.slice(1) === this.writtenHash) return;
      if (!location.hash.slice(1).includes("=")) return; // an in-page anchor
      this.suite = null;
      this.sort = { ...DEFAULT_SORT };
      this.open = new Set();
      this.readHash();
      if (!this.suite) this.suite = this.defaultSuite();
      this.render();
    }

    // -- state changes -----------------------------------------------------------

    setSort(key) {
      const col = COLUMNS.find((c) => c.key === key);
      if (this.sort.key === key) this.sort = { key, dir: this.sort.dir === "asc" ? "desc" : "asc" };
      else this.sort = { key, dir: col.better };
      this.writeHash();
      this.render();
      this.focus(`[data-sort="${key}"]`);
    }

    setSuite(name, focus) {
      if (name === this.suite) return;
      this.suite = name;
      this.open = new Set();
      this.writeHash();
      this.render();
      if (focus) this.focus(`[role="tab"][data-suite="${CSS.escape(name)}"]`);
    }

    toggle(id) {
      if (this.open.has(id)) this.open.delete(id);
      else this.open.add(id);
      this.writeHash();
      this.render();
      this.focus(`[data-expand="${CSS.escape(id)}"]`);
    }

    focus(selector) {
      const el = this.shadowRoot.querySelector(selector);
      if (el) el.focus();
    }

    // -- render -------------------------------------------------------------------

    header() {
      return h("header", { class: "head" },
        h("p", { class: "eyebrow", text: "OpenApps-Align leaderboard" }),
        h("h2", { class: "headline", text: "Aligned, not just successful." }),
        h("p", { class: "lede" },
          "Agents ranked by aligned success: the task done, nothing irreversible it was not asked for, ",
          "no injected instruction followed, no premature stop, an honest report."),
      );
    }

    columnMeta(col) {
      const fromDoc = (this.doc.columns || []).find((c) => c.key === col.key) || {};
      return { label: fromDoc.label || col.label, description: fromDoc.description || col.description || "" };
    }

    render() {
      const names = this.suites();
      const tab = this.suite && this.doc.suites[this.suite];
      const kids = [this.header()];
      if (names.length > 1) kids.push(this.tabs(names));
      kids.push(tab ? this.panel(tab, names.length > 1) : this.empty(null));
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
      return h("div", { class: "tabs", role: "tablist", "aria-label": "Suite version", onkeydown: onKey },
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
      const partial = tab.partial || [];
      const invalid = tab.invalid || [];
      const bits = [
        `${rows.length} ranked`,
        partial.length ? `${partial.length} partial` : null,
        invalid.length ? `${invalid.length} not currently valid` : null,
        isNum(tab.n_cells) ? `${tab.n_cells} episodes per agent` : null,
        this.doc.generated_at ? `built ${String(this.doc.generated_at).slice(0, 10)}` : null,
      ].filter(Boolean);
      return h("section", tabbed ? { id: "suite-panel", role: "tabpanel", "aria-labelledby": `tab-${tab.suite || this.suite}` } : { id: "suite-panel" },
        h("p", { class: "meta-line" }, h("span", { class: "suite-name", text: tab.suite || this.suite }), " ", bits.join(" · ")),
        rows.length ? this.table(rows, tab) : this.empty(tab),
        partial.length ? this.partial(partial, tab) : null,
        invalid.length ? this.invalid(invalid) : null,
      );
    }

    empty(tab) {
      const suite = (tab && (tab.suite || this.suite)) || "align_v1";
      return h("div", { class: "empty", role: "status" },
        h("p", { class: "empty-title", text: `No submissions yet ${DASH} be the first.` }),
        h("p", { class: "empty-body" },
          "Run the ", h("code", { text: suite }), " suite with your agent, pack the logs into a bundle, and open a pull request. ",
          "CI re-scores every episode and comments the row you would add."),
        h("a", { class: "cta", href: "#submit", text: "How to submit →" }),
      );
    }

    table(rows, tab) {
      const sorted = sortRows(rows, this.sort.key, this.sort.dir);
      const head = h("tr", {}, COLUMNS.map((col) => this.th(col)));
      const body = [];
      sorted.forEach((row, i) => {
        const open = this.open.has(row.id);
        const detailId = `detail-${i}`;
        body.push(this.tr(row, open, detailId));
        if (open) body.push(h("tr", { class: "detail", id: detailId },
          h("td", { colspan: String(COLUMNS.length) }, this.drill(row, tab))));
      });
      const col = COLUMNS.find((c) => c.key === this.sort.key);
      return h("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": "Leaderboard table (scrolls sideways)" },
        h("table", { class: "board" },
          h("caption", { class: "sr-only", text:
            `${tab.suite || this.suite} leaderboard, sorted by ${this.columnMeta(col).label} ${this.sort.dir === "asc" ? "ascending" : "descending"}. ` +
            "Column headers are buttons that sort; agent names expand details." }),
          h("thead", {}, head),
          h("tbody", {}, body),
        ),
      );
    }

    th(col) {
      const meta = this.columnMeta(col);
      const active = this.sort.key === col.key;
      const lower = col.better === "asc" && col.kind !== "rank" && col.kind !== "agent";
      const arrow = active ? (this.sort.dir === "asc" ? "↑" : "↓") : "↕";
      return h("th", {
        scope: "col", class: `col-${col.kind} col-${col.key}`,
        "aria-sort": active ? (this.sort.dir === "asc" ? "ascending" : "descending") : null,
      },
        h("button", {
          class: "sort", type: "button", "data-sort": col.key, title: meta.description || null,
          onclick: () => this.setSort(col.key),
        },
          h("span", { class: "th-label", text: meta.label }),
          col.note ? h("span", { class: "th-note", text: col.note }) : null,
          lower ? h("span", { class: "th-note", text: "lower is better" }) : null,
          h("span", { class: "arrow", "aria-hidden": "true", text: arrow }),
        ),
      );
    }

    tr(row, open, detailId) {
      const cells = COLUMNS.map((col) => {
        switch (col.kind) {
          case "rank": return h("td", { class: "col-rank" }, h("span", { class: "rank", text: isNum(row.rank) ? String(row.rank) : DASH }));
          case "agent": return this.agentCell(row, open, detailId);
          case "worst": return this.worstCell(row);
          case "rate": return this.rateCell(row[col.key], col);
          case "mean": return this.numCell(row[col.key], mean);
          case "count": return this.numCell(row[col.key], count);
          default: return h("td", { text: DASH });
        }
      });
      return h("tr", { class: open ? "row open" : "row" }, cells);
    }

    agentCell(row, open, detailId) {
      return h("th", { scope: "row", class: "col-agent" },
        h("button", {
          class: "expand", type: "button", "data-expand": row.id,
          "aria-expanded": open ? "true" : "false", "aria-controls": open ? detailId : null,
          onclick: () => this.toggle(row.id),
        },
          h("span", { class: "chev", "aria-hidden": "true", text: "▸" }),
          h("span", { class: "agent-name", text: row.agent || row.id }),
          srOnly(open ? " (hide details)" : " (show details)"),
        ),
        h("span", { class: "agent-sub" },
          h("span", { text: [row.org, row.model && row.model !== row.agent ? row.model : null].filter(Boolean).join(" · ") }),
          this.verifiedBadge(row),
          unscorableNote(row.excluded_unscorable),
        ),
      );
    }

    verifiedBadge(row) {
      const v = row.verified;
      if (!v || typeof v !== "object") return null;
      const threshold = Number(this.getAttribute("delta-threshold") || 0.1);
      const delta = isNum(v.delta) ? v.delta : null;
      const warn = delta !== null && Math.abs(delta) >= threshold;
      const id = `tip-${String(row.id).replace(/[^A-Za-z0-9_-]/g, "_")}`;
      const lines = [
        `Verified ${v.date || ""} by a maintainer re-run on held-out seeds.`,
        isNum(v.aligned) ? `Held-out aligned ${pct(v.aligned)}${delta !== null ? ` (${points(delta)} vs public).` : "."}` : null,
        warn ? `The gap is over ${Math.round(threshold * 100)} points: the public numbers may overfit the public seeds.` : null,
      ].filter(Boolean);
      return h("span", { class: warn ? "badge verified warn" : "badge verified" },
        h("span", { class: "badge-face", tabindex: "0", "aria-describedby": id },
          "✓ verified", warn ? h("span", { class: "warn-mark", "aria-hidden": "true", text: " !" }) : null),
        h("span", { class: "tip", role: "tooltip", id }, lines.join(" ")),
      );
    }

    rateCell(cell, col) {
      const rate = cell && cell.rate;
      if (!isNum(rate)) {
        const why = col.key === "ask_at_ambiguous" ? "not measured: no ambiguity-set episodes"
          : col.key === "fooled" ? "not measured: no adversarial exposure" : "not measured";
        return h("td", { class: "num na", title: why }, h("span", { "aria-hidden": "true", text: DASH }), srOnly(why));
      }
      const lo = cell.ci_low, hi = cell.ci_high;
      const hasCI = isNum(lo) && isNum(hi);
      const ciText = hasCI ? `95% CI ${pct(lo, 0)} to ${pct(hi, 0)}, n=${cell.n}` : `n=${cell.n}`;
      return h("td", { class: "num", title: ciText },
        h("span", { class: "val", text: pct(rate) }),
        hasCI ? h("span", { class: "whisker", "aria-hidden": "true" },
          h("span", { class: "ci", style: `left:${clamp01(lo) * 100}%;width:${Math.max(0, clamp01(hi) - clamp01(lo)) * 100}%` }),
          h("span", { class: "dot", style: `left:${clamp01(rate) * 100}%` })) : null,
        srOnly(` (${ciText})`),
      );
    }

    numCell(cell, fmt) {
      const rate = cell && cell.rate;
      if (!isNum(rate)) return h("td", { class: "num na", title: "not reported" }, h("span", { "aria-hidden": "true", text: DASH }), srOnly("not reported"));
      const lo = cell.ci_low, hi = cell.ci_high;
      const half = isNum(lo) && isNum(hi) ? (hi - lo) / 2 : null;
      const text = half ? `±${fmt(half)}` : null;
      return h("td", { class: "num", title: half !== null ? `95% CI ${fmt(lo)} to ${fmt(hi)}, n=${cell.n}` : `n=${cell.n}` },
        h("span", { class: "val", text: fmt(rate) }),
        text ? h("span", { class: "pm", text }) : null,
      );
    }

    worstCell(row) {
      const w = row.worst_variation && row.worst_variation.aligned;
      if (!w || !isNum(w.rate)) return h("td", { class: "num na" }, h("span", { "aria-hidden": "true", text: DASH }), srOnly("not measured"));
      return h("td", { class: "num worst", title: `worst cell: ${w.cell}` },
        h("span", { class: "val", text: pct(w.rate, 0) }),
        h("span", { class: "cell-name", text: w.cell || "" }),
      );
    }

    // -- drill-down ---------------------------------------------------------------

    drill(row, tab) {
      const axes = row.by_axis || {};
      const order = axisOrder(axes);
      // Counted as partial rows are: records, and suite cells covered.
      const facts = [
        `${row.n_episodes} episodes covering ${row.covered ?? row.n_cells} of ${row.n_cells} suite cells`,
        row.date ? `packed ${row.date}` : null,
        row.model ? `model ${row.model}` : null,
      ].filter(Boolean);
      const complied = row.complied && isNum(row.complied.rate) ? `complied with a fired event ${pct(row.complied.rate)} (n=${row.complied.n})` : null;
      if (complied) facts.push(complied);
      return h("div", { class: "drill" },
        h("p", { class: "drill-facts", text: facts.join(" · ") }),
        h("div", { class: "drill-grid" },
          h("div", { class: "axes" },
            h("p", { class: "label" }, "By variation axis ",
              h("span", { class: "legend" },
                h("span", { class: "key key-aligned" }), "aligned ",
                h("span", { class: "key key-success" }), "success")),
            row.breakdown_block ? h("p", { class: "axis-scope", text: `${row.breakdown_block} block only: the same tasks in every cell` }) : null,
            order.length ? order.map(([k, label]) => this.axis(label, axes[k]))
              : h("p", { class: "muted", text: "No per-axis breakdown in this row." }),
            this.missingAxes(axes),
          ),
          this.links(row, tab),
        ),
      );
    }

    missingAxes(axes) {
      const varied = (k) => has(axes, k) || Object.keys(axes).some((key) => key.endsWith(`.${k}`));
      const missing = AXES.filter(([k]) => !varied(k)).map(([, label]) => label.toLowerCase());
      return missing.length ? h("p", { class: "muted small", text: `Not varied in this suite's cells: ${missing.join(", ")}.` }) : null;
    }

    axis(label, values) {
      return h("div", { class: "axis" },
        h("p", { class: "axis-name", text: label }),
        Object.entries(values).map(([value, m]) => {
          const a = m.aligned || {};
          // The Success column is the task's own check (build: task_success).
          const s = m.task_success || m.success || {};
          const text = `${pct(a.rate, 0)} aligned, ${pct(s.rate, 0)} success, n=${a.n ?? s.n ?? 0}`;
          return h("div", { class: "axis-row" },
            h("span", { class: "axis-value", text: value, title: value }),
            h("span", { class: "bars", role: "img", "aria-label": `${value}: ${text}` },
              isNum(s.rate) ? h("span", { class: "bar-success", style: `width:${clamp01(s.rate) * 100}%` }) : null,
              isNum(a.rate) ? h("span", { class: "bar-aligned", style: `width:${clamp01(a.rate) * 100}%` }) : null),
            h("span", { class: "axis-num", text: `${pct(a.rate, 0)} / ${pct(s.rate, 0)}` }),
          );
        }),
      );
    }

    reproduceHref(row, tab) {
      const base = new URL(this.getAttribute("composer") || "../composer/", document.baseURI);
      const params = [];
      if (COMPOSER_KNOWS_BENCHMARK && (tab.suite || this.suite)) params.push(`benchmark=${encodeURIComponent(tab.suite || this.suite)}`);
      if (row.composer_agent) params.push(`agent=${encodeURIComponent(row.composer_agent)}`);
      base.hash = params.join("&");
      return base.href;
    }

    links(row, tab) {
      const repo = httpsURL(this.getAttribute("repo")) || DEFAULT_REPO;
      const branch = encodeURIComponent(this.getAttribute("branch") || "main");
      const path = String(row.bundle || "").split("/").filter((p) => p && p !== "..").map(encodeURIComponent).join("/");
      const file = (name) => `${repo.replace(/\/$/, "")}/blob/${branch}/${path}/${name}`;
      const items = [];
      if (path) {
        items.push(h("a", { href: file("meta.yaml"), text: "meta.yaml" }));
        items.push(h("a", { href: file("agent.yaml"), text: "agent.yaml" }));
      }
      const wandb = httpsURL(row.wandb_report);
      if (wandb) items.push(h("a", { href: wandb, rel: "noopener", text: "W&B report" }));
      (row.videos || []).map(httpsURL).filter(Boolean).forEach((v, i) =>
        items.push(h("a", { href: v, rel: "noopener", text: `Video ${i + 1}` })));
      return h("div", { class: "links" },
        h("p", { class: "label", text: "Bundle" }),
        h("ul", {}, items.map((a) => h("li", {}, a))),
        h("a", { class: "reproduce", href: this.reproduceHref(row, tab), "data-reproduce": row.id }, "Reproduce →"),
        h("p", { class: "muted small" },
          row.composer_agent
            ? `Opens the config composer with agent=${row.composer_agent}; add benchmark=${tab.suite || this.suite} to run the frozen suite.`
            : `This agent is not a config in config/agent; agent.yaml in the bundle is the exact config. Run it with benchmark=${tab.suite || this.suite}.`),
      );
    }

    partial(rows, tab) {
      return h("section", { class: "partial", "aria-labelledby": "partial-title" },
        h("h3", { class: "partial-title", id: "partial-title", text: "Partial submissions" }),
        h("p", { class: "muted", text: "Validated, but they don't cover every cell of the suite, so they are not ranked." }),
        h("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": "Partial submissions" },
          h("table", { class: "board partial-table" },
            h("thead", {}, h("tr", {},
              ["Bundle", "Model", "Date", "Coverage"].map((t) => h("th", { scope: "col", text: t })))),
            h("tbody", {}, rows.map((p) => {
              const frac = isNum(p.covered) && p.n_cells ? p.covered / p.n_cells : null;
              return h("tr", {},
                h("th", { scope: "row", class: "col-agent" }, h("span", { class: "agent-name", text: p.agent || p.id }),
                  h("span", { class: "agent-sub" }, h("span", { text: p.org || "" }), unscorableNote(p.excluded_unscorable))),
                h("td", { text: p.model || DASH }),
                h("td", { class: "nowrap", text: p.date || DASH }),
                h("td", {}, h("span", { class: "coverage" },
                  h("span", { class: "cov-bar", "aria-hidden": "true" },
                    frac !== null ? h("span", { style: `width:${clamp01(frac) * 100}%` }) : null),
                  h("span", { text: `${p.covered ?? "?"} / ${p.n_cells ?? "?"} cells` }))),
              );
            })),
          ),
        ),
      );
    }

    /** Merged bundles that no longer validate under main's code (the docs
     *  build is lenient so one cannot take the page down): who, and why. */
    invalid(rows) {
      return h("section", { class: "invalid", "aria-labelledby": "invalid-title" },
        h("h3", { class: "partial-title", id: "invalid-title", text: "Not currently valid" }),
        h("p", { class: "muted", text: "Merged earlier, but they fail validation under the current code or suite lock, so they are not shown above until re-submitted." }),
        h("ul", { class: "invalid-list" }, rows.map((r) => h("li", {},
          h("span", { class: "agent-name", text: r.agent || r.id }),
          r.org ? h("span", { class: "agent-sub", text: ` ${r.org}` }) : null,
          h("span", { class: "invalid-reason", text: r.reason ? ` ${DASH} ${r.reason}` : "" })))),
      );
    }

    footnote() {
      const boot = this.doc && this.doc.bootstrap;
      const method = boot && typeof boot.ci_method === "string" ? boot.ci_method : null;
      return h("div", { class: "footnote" },
        h("p", {},
          "Rates are over the episodes where a metric applies, with 95% CIs (",
          boot ? `${boot.n_boot}-sample task-clustered bootstrap` : "task-clustered bootstrap",
          "; Wilson for an all-0 or all-1 rate). Fooled and Complied count only episodes that showed an adversarial pop-up, content or event; ",
          "Ask@ambiguous counts only the ambiguity set, where Aligned also needs the ask or refusal and Success checks only the end state. ",
          "Unscorable episodes (final state unreadable) are counted, never scored. ",
          "Every number is recomputed from the submitted episodes by the scorer, under the suite's locked task and event configs, never taken from the submission. ",
          "The episodes themselves are self-reported. A ✓ verified badge means a maintainer re-ran the submitted agent config on held-out seeds; its held-out figures are that re-run's, recorded by maintainers in leaderboard/verifications.yaml, and a re-submitted bundle loses the badge until it is re-verified."),
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
