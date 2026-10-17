/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

/*
 * <openapps-composer catalog="catalog.json"> -- pick options, get a correct,
 * minimal launch command.
 *
 * Framework-free and build-free on purpose: the docs site is static, and a
 * Node toolchain for one page is more machinery than the page. Two halves:
 *
 *   1. A port of open_apps.compose.command (render / parse / normalize) and
 *      open_apps.compose.rules (facts / evaluate / applyFix). The Python is
 *      the reference; tests/test_compose_js.py runs the shared fixtures
 *      through this file in a browser and fails on any divergence -- so when
 *      one side changes, change the other in the same commit.
 *   2. The custom element, which only ever holds a Selection (the same JSON
 *      shape as Selection.to_dict()) and re-renders from it.
 *
 * The engine is exposed as window.OpenAppsComposer for the tests and for
 * anything else that wants a command without the UI.
 */
(function () {
  "use strict";

  // -------------------------------------------------------------------------
  // Python semantics the reference leans on
  // -------------------------------------------------------------------------

  class CommandError extends Error {
    constructor(message) {
      super(message);
      this.name = "CommandError";
    }
  }

  // Catalog keys come from user input (a pasted command, the URL hash), so
  // never let `constructor` or `__proto__` resolve through the prototype.
  const has = (obj, key) =>
    obj != null && Object.prototype.hasOwnProperty.call(obj, key);
  const isBool = (v) => typeof v === "boolean";
  const isNum = (v) => typeof v === "number";

  /** Python `==` on JSON values: True == 1, lists and dicts compare deeply. */
  function pyEq(a, b) {
    if ((isBool(a) || isNum(a)) && (isBool(b) || isNum(b))) return Number(a) === Number(b);
    if (Array.isArray(a) || Array.isArray(b)) {
      return Array.isArray(a) && Array.isArray(b) && a.length === b.length &&
        a.every((x, i) => pyEq(x, b[i]));
    }
    if (a && b && typeof a === "object" && typeof b === "object") {
      const ka = Object.keys(a);
      return ka.length === Object.keys(b).length && ka.every((k) => has(b, k) && pyEq(a[k], b[k]));
    }
    return a === b;
  }

  /** `list(dict.fromkeys(values))`, with Python's notion of equal keys. */
  function dedupe(values) {
    const out = [];
    for (const v of values) if (!out.some((o) => pyEq(o, v))) out.push(v);
    return out;
  }

  /** Python `repr()` of a str (what `f"{x!r}"` puts in error messages). */
  function pyRepr(v) {
    if (typeof v !== "string") return pyStr(v);
    const quote = v.includes("'") && !v.includes('"') ? '"' : "'";
    let body = v.replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/\t/g, "\\t");
    if (quote === "'") body = body.replace(/'/g, "\\'");
    return quote + body + quote;
  }

  /** Python `str()` for the JSON values a selection holds. */
  function pyStr(v) {
    if (v === null || v === undefined) return "None";
    if (v === true) return "True";
    if (v === false) return "False";
    if (isNum(v)) {
      if (v === Infinity) return "inf";
      if (v === -Infinity) return "-inf";
      if (Number.isNaN(v)) return "nan";
      return String(v);
    }
    if (Array.isArray(v)) return "[" + v.map(pyRepr).join(", ") + "]";
    if (typeof v === "object") {
      return "{" + Object.keys(v).map((k) => pyRepr(k) + ": " + pyRepr(v[k])).join(", ") + "}";
    }
    return String(v);
  }

  /** Python `float(x)`, or null where it would raise. Booleans never get here. */
  function pyFloat(v) {
    if (isNum(v)) return v;
    if (typeof v !== "string") return null;
    const s = v.trim().replace(/(\d)_(?=\d)/g, "$1");
    if (/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(s)) return Number(s);
    if (/^[+-]?(inf|infinity)$/i.test(s)) return s[0] === "-" ? -Infinity : Infinity;
    if (/^[+-]?nan$/i.test(s)) return NaN;
    return null;
  }

  const pySplit = (s) => s.split(/\s+/).filter(Boolean);

  // -------------------------------------------------------------------------
  // shlex (POSIX mode), as Python's shlex.split / shlex.quote
  // -------------------------------------------------------------------------

  function shlexSplit(s) {
    const out = [];
    let token = null;
    let i = 0;
    while (i < s.length) {
      const ch = s[i];
      if (/\s/.test(ch)) {
        if (token !== null) out.push(token);
        token = null;
        i++;
      } else if (ch === "'") {
        const end = s.indexOf("'", i + 1);
        if (end < 0) throw new CommandError("No closing quotation");
        token = (token || "") + s.slice(i + 1, end);
        i = end + 1;
      } else if (ch === '"') {
        token = token || "";
        i++;
        for (;;) {
          if (i >= s.length) throw new CommandError("No closing quotation");
          const c = s[i];
          if (c === '"') { i++; break; }
          // Inside double quotes a backslash escapes only `"` and itself.
          if (c === "\\" && i + 1 < s.length && (s[i + 1] === '"' || s[i + 1] === "\\")) {
            token += s[i + 1];
            i += 2;
          } else {
            token += c;
            i++;
          }
        }
      } else if (ch === "\\") {
        if (i + 1 >= s.length) throw new CommandError("No escaped character");
        token = (token || "") + s[i + 1];
        i += 2;
      } else {
        token = (token || "") + ch;
        i++;
      }
    }
    if (token !== null) out.push(token);
    return out;
  }

  function shlexQuote(s) {
    if (!s) return "''";
    if (!/[^\w@%+=:,./-]/.test(s)) return s;
    return "'" + s.replace(/'/g, "'\"'\"'") + "'";
  }

  // -------------------------------------------------------------------------
  // Override values: Hydra's grammar (for parse) and YAML (for rule facts)
  // -------------------------------------------------------------------------

  // Stand-in for what Hydra parses to something that is neither a list nor a
  // string (a choice sweep, a function call, a dict): parse treats all of
  // those alike -- not modelled, keep the token verbatim.
  const OPAQUE = Object.freeze({ opaque: true });

  /**
   * The value of a Hydra override's right-hand side, or throws where Hydra's
   * grammar would. Covers what parse looks at -- lists, quoted strings and
   * typed primitives; sweeps, functions and dicts come back as OPAQUE.
   */
  function hydraValue(text) {
    let i = 0;
    const fail = () => { throw new SyntaxError("not a Hydra override value: " + text); };
    const ws = () => { while (i < text.length && /[ \t]/.test(text[i])) i++; };

    function quoted() {
      const q = text[i++];
      let out = "";
      while (i < text.length && text[i] !== q) {
        if (text[i] === "\\" && text[i + 1] === q) { out += q; i += 2; }
        else out += text[i++];
      }
      if (i >= text.length) fail();
      i++;
      return out;
    }

    function primitive() {
      let raw = "";
      let escaped = false;
      while (i < text.length) {
        const c = text[i];
        if (c === "\\" && i + 1 < text.length && "\\()[]{}:=, \t".includes(text[i + 1])) {
          raw += text[i + 1];
          escaped = true;
          i += 2;
        } else if (c === "$" && text[i + 1] === "{") {
          const end = text.indexOf("}", i);
          if (end < 0) fail();
          raw += text.slice(i, end + 1);
          i = end + 1;
        } else if (/[\p{L}\p{N}_\-/\\+.$%*@?|: \t]/u.test(c)) {
          raw += c;
          i++;
        } else break;
      }
      if (text[i] === "(") return function_();
      const s = raw.trim();
      if (!s) fail();
      if (escaped) return s;
      if (/^[+-]?(0|[1-9](_?\d)*)$/.test(s)) return Number(s.replace(/_/g, ""));
      if (/^[+-]?((\d(_?\d)*)?\.\d(_?\d)*|\d(_?\d)*\.?)([eE][+-]?\d(_?\d)*)?$/.test(s) && /[.eE]/.test(s)) {
        return Number(s.replace(/_/g, ""));
      }
      if (/^[+-]?inf$/i.test(s)) return s[0] === "-" ? -Infinity : Infinity;
      if (/^[+-]?nan$/i.test(s)) return NaN;
      if (/^(true|false)$/i.test(s)) return s.toLowerCase() === "true";
      if (/^null$/i.test(s)) return null;
      return s;
    }

    function function_() {
      // choice(...), range(...), int(...): never a plain list or string.
      let depth = 0;
      do {
        if (text[i] === "(") depth++;
        else if (text[i] === ")") depth--;
        else if (text[i] === "'" || text[i] === '"') { quoted(); continue; }
        i++;
      } while (i < text.length && depth > 0);
      if (depth) fail();
      return OPAQUE;
    }

    function container(open, close) {
      i++;
      const items = [];
      ws();
      if (text[i] === close) { i++; return items; }
      for (;;) {
        ws();
        if (open === "{") {
          while (i < text.length && text[i] !== ":") i++;
          i++;
          ws();
        }
        items.push(element());
        ws();
        if (text[i] === ",") { i++; continue; }
        if (text[i] === close) { i++; break; }
        fail();
      }
      return open === "[" ? items : OPAQUE;
    }

    function element() {
      ws();
      const c = text[i];
      let v;
      if (c === "[") v = container("[", "]");
      else if (c === "{") v = container("{", "}");
      else if (c === "'" || c === '"') v = quoted();
      else v = primitive();
      ws();
      return v;
    }

    const first = element();
    if (i < text.length && text[i] === ",") {
      // a,b: a simple choice sweep
      while (i < text.length && text[i] === ",") { i++; element(); }
      return i === text.length ? OPAQUE : fail();
    }
    return i === text.length ? first : fail();
  }

  /**
   * `yaml.safe_load(raw)` for the scalars an extra override sets -- YAML 1.1
   * as PyYAML resolves it (yes/no/on/off are booleans, `~` is null) -- or the
   * raw text where PyYAML would fail. Flow sequences are read one level at a
   * time; flow mappings and anchors are left as text.
   */
  function yamlScalar(raw) {
    const s = raw.trim();
    if (s === "" || s === "~" || /^(null|Null|NULL)$/.test(s)) return null;
    if (/^(yes|Yes|YES|true|True|TRUE|on|On|ON)$/.test(s)) return true;
    if (/^(no|No|NO|false|False|FALSE|off|Off|OFF)$/.test(s)) return false;
    if (/^[-+]?0b[01_]+$/.test(s)) return signed(s, (t) => parseInt(t.replace(/_/g, "").slice(2), 2));
    if (/^[-+]?0x[0-9a-fA-F_]+$/.test(s)) return signed(s, (t) => parseInt(t.replace(/_/g, "").slice(2), 16));
    if (/^[-+]?0[0-7_]+$/.test(s)) return signed(s, (t) => parseInt(t.replace(/_/g, ""), 8));
    if (/^[-+]?(0|[1-9][0-9_]*)$/.test(s)) return Number(s.replace(/_/g, ""));
    if (/^[-+]?([0-9][0-9_]*)?\.[0-9_]*([eE][-+][0-9]+)?$/.test(s) && /\d/.test(s)) {
      return Number(s.replace(/_/g, ""));
    }
    if (/^[-+]?\.(inf|Inf|INF)$/.test(s)) return s[0] === "-" ? -Infinity : Infinity;
    if (/^\.(nan|NaN|NAN)$/.test(s)) return NaN;
    if (/^'([^']|'')*'$/.test(s)) return s.slice(1, -1).replace(/''/g, "'");
    if (/^"([^"\\]|\\.)*"$/.test(s)) {
      try { return JSON.parse(s); } catch (e) { return raw; }
    }
    if (s[0] === "[" && s[s.length - 1] === "]") {
      const inner = s.slice(1, -1).trim();
      return inner ? splitTopLevel(inner).map(yamlScalar) : [];
    }
    // A plain scalar loads trimmed; one opening with an indicator character
    // is a YAML error, which the reference catches and keeps as raw text.
    return /^[@`%*&!|>]/.test(s) ? raw : s;
  }

  function signed(s, convert) {
    const neg = s[0] === "-";
    const v = convert(s.replace(/^[-+]/, ""));
    return neg ? -v : v;
  }

  function splitTopLevel(s) {
    const parts = [];
    let depth = 0;
    let start = 0;
    for (let i = 0; i < s.length; i++) {
      if (s[i] === "[" || s[i] === "{") depth++;
      else if (s[i] === "]" || s[i] === "}") depth--;
      else if (s[i] === "," && depth === 0) { parts.push(s.slice(start, i)); start = i + 1; }
    }
    parts.push(s.slice(start));
    return parts;
  }

  // -------------------------------------------------------------------------
  // Selection
  // -------------------------------------------------------------------------

  const ALL_TASKS = "all";
  const POOL_TARGETS = ["pool", "slurm_pool"];

  /** The full form of a Selection.to_dict() (missing fields filled in). */
  function fromDict(data) {
    data = data || {};
    const choices = {};
    for (const [k, v] of Object.entries(data.choices || {})) choices[k] = [...v];
    return {
      choices,
      target: data.target || null,
      count: data.count ?? null,
      max_parallel: data.max_parallel ?? null,
      extra: [...(data.extra || [])],
      env: [...(data.env || [])],
      sbatch_args: [...(data.sbatch_args || [])],
    };
  }

  /** Selection.to_dict(): empty fields omitted -- what the fixtures store. */
  function toDict(sel) {
    const out = {};
    if (sel.target) out.target = sel.target;
    if (Object.keys(sel.choices).length) {
      out.choices = {};
      for (const [k, v] of Object.entries(sel.choices)) out.choices[k] = [...v];
    }
    for (const key of ["count", "max_parallel"]) if (sel[key] !== null) out[key] = sel[key];
    for (const key of ["extra", "env", "sbatch_args"]) if (sel[key].length) out[key] = [...sel[key]];
    return out;
  }

  const listEq = (a, b) => pyEq(a || null, b);

  // -------------------------------------------------------------------------
  // Targets and defaults (command.py)
  // -------------------------------------------------------------------------

  function resolveTarget(sel, catalog) {
    const targets = catalog.targets;
    const choices = sel.choices;
    const slurm = (choices.mode || []).includes("slurm_cluster");
    if (sel.target) {
      if (!has(targets, sel.target)) {
        throw new CommandError(
          `unknown target ${pyRepr(sel.target)}; one of ${Object.keys(targets).join(", ")}`
        );
      }
      // The Worker pool pill on the SLURM mode (see Target.pill).
      if (sel.target === "pool" && slurm) return "slurm_pool";
      return sel.target;
    }
    if ((choices.agent || []).length > 1 || (sel.count || 0) > 1 || sel.max_parallel !== null) {
      return slurm ? "slurm_pool" : "pool";
    }
    const varied = Object.keys(choices).filter((k) => choices[k].length > 1 && k !== "agent");
    if (varied.length || listEq(choices.task_name, [ALL_TASKS])) return "sweep";
    return slurm ? "slurm_pool" : "single";
  }

  function defaultFor(key, target, catalog) {
    const spec = catalog.targets[target];
    if (has(spec.defaults, key)) return spec.defaults[key];
    if (key === "task_name") return catalog.scalars.task_name.defaults.config;
    if (has(catalog.groups, key)) {
      const group = catalog.groups[key];
      return group.defaults[group.sweepable ? "config" : spec.config_name];
    }
    if (has(catalog.scalars, key)) return catalog.scalars[key].defaults[spec.config_name];
    return null;
  }

  function normalizeSel(selection, catalog) {
    const choices = {};
    for (const [key, values] of Object.entries(selection.choices)) {
      const unique = dedupe(values);
      if (unique.length) choices[key] = unique;
    }
    let sel = { ...selection, choices };
    if (sel.target === "slurm_pool") {
      // Not a pill: Worker pool + the SLURM mode. Keeps old links working.
      if (!has(choices, "mode")) choices.mode = ["slurm_cluster"];
      sel = { ...sel, target: "pool" };
    }
    const target = resolveTarget(sel, catalog);
    if (target === "slurm_pool" && !has(choices, "mode")) choices.mode = ["slurm_cluster"];
    if (POOL_TARGETS.includes(target)) {
      // render always spells out AGENTS, so the agent is never dropped as a
      // default: that would lose it on a switch to the other pool, whose
      // script defaults differ. Fill in the one the UI shows instead.
      if (!has(choices, "agent")) choices.agent = [defaultFor("agent", target, catalog)];
      if (target === "slurm_pool" && sel.count === choices.agent.length) sel = { ...sel, count: null };
    }
    if (!POOL_TARGETS.includes(target)) sel = { ...sel, count: null, max_parallel: null, sbatch_args: [] };
    if (target !== "slurm_pool") sel = { ...sel, sbatch_args: [] };

    for (const key of Object.keys(choices)) {
      const values = choices[key];
      if (key === "mode" && POOL_TARGETS.includes(target)) {
        if (listEq(values, ["local"])) delete choices[key];
        continue;
      }
      if (key === "agent" && POOL_TARGETS.includes(target)) continue; // always emitted, see above
      if (values.length === 1 && pyEq(values[0], defaultFor(key, target, catalog))) delete choices[key];
    }
    if (sel.target && resolveTarget({ ...sel, target: null }, catalog) === target) {
      sel = { ...sel, target: null };
    }
    return sel;
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const formatValue = (v) => (isBool(v) ? (v ? "True" : "False") : pyStr(v));

  function override(key, value, catalog) {
    const group = has(catalog.groups, key) ? catalog.groups[key] : null;
    return `${group && group.append ? "+" : ""}${key}=${formatValue(value)}`;
  }

  function orderedKeys(choices, catalog) {
    const known = Object.keys(catalog.groups).filter((k) => has(choices, k));
    known.push(...Object.keys(catalog.scalars).filter((k) => has(choices, k)));
    return known.concat(Object.keys(choices).filter((k) => !known.includes(k)));
  }

  function checkExpressible(sel, target, catalog) {
    for (const key of Object.keys(sel.choices)) {
      if (!has(catalog.groups, key) && !has(catalog.scalars, key)) {
        throw new CommandError(`unknown key ${pyRepr(key)}; pass it as an extra override`);
      }
    }
    const varied = Object.keys(sel.choices).filter((k) => sel.choices[k].length > 1);
    let allowed;
    if (target === "single") allowed = [];
    else if (target === "sweep") {
      allowed = Object.keys(catalog.groups).filter((k) => catalog.groups[k].sweepable);
      allowed.push("task_name");
    } else allowed = ["agent"];
    for (const key of varied) {
      if (allowed.includes(key)) continue;
      let why;
      if (key === "agent") why = "several agents run as a worker pool, which cannot also sweep";
      else if (POOL_TARGETS.includes(target)) why = "a worker pool varies only the agent; run one sweep per agent";
      else if (has(catalog.groups, key) && target === "sweep") {
        why = "a sweep varies only apps/* groups and task_name -- create_configs copies just `apps` out of each cell";
      } else why = `a ${catalog.targets[target].label.toLowerCase()} takes one value per axis`;
      throw new CommandError(`cannot vary ${pyRepr(key)}: ${why}`);
    }
    if (listEq(sel.choices.task_name, [ALL_TASKS]) && target !== "sweep") {
      throw new CommandError("task_name=all needs the sweep target");
    }
    if (POOL_TARGETS.includes(target)) {
      const expected = target === "slurm_pool" ? "slurm_cluster" : "local";
      if (!listEq(sel.choices.mode || [expected], [expected])) {
        throw new CommandError(`mode must be ${pyRepr(expected)} for the ${target} target`);
      }
    }
  }

  const hydraList = (items, quote) => "[" + items.map((i) => (quote ? `"${i}"` : i)).join(",") + "]";

  function sweepCells(sel, catalog) {
    const axes = Object.entries(catalog.groups)
      .filter(([path, group]) => group.sweepable && has(sel.choices, path))
      .map(([path]) => sel.choices[path].map((v) => override(path, v, catalog)));
    return axes.reduce((cells, axis) => cells.flatMap((c) => axis.map((v) => [...c, v])), [[]]);
  }

  const hasExtra = (sel, key) => sel.extra.some((t) => t.split("=")[0] === key);

  function renderSel(selection, catalog) {
    const sel = normalizeSel(selection, catalog);
    const target = resolveTarget(sel, catalog);
    checkExpressible(sel, target, catalog);

    const env = [];
    const overrides = [];
    const pool = POOL_TARGETS.includes(target);
    for (const key of orderedKeys(sel.choices, catalog)) {
      const values = sel.choices[key];
      const scalar = has(catalog.scalars, key) ? catalog.scalars[key] : null;
      if (pool && key === "agent") env.push("AGENTS=" + values.join(" "));
      else if (pool && key === "mode") continue;
      else if (pool && scalar && scalar.pool_env) env.push(`${scalar.pool_env}=${formatValue(values[0])}`);
      else if (target === "sweep" && (key === "task_name" || (has(catalog.groups, key) && catalog.groups[key].sweepable))) continue;
      else overrides.push(override(key, values[0], catalog));
    }

    if (pool) {
      let count = sel.count;
      // conduct_slurm.sh defaults COUNT to 1, not to one run per agent.
      if (count === null && target === "slurm_pool" && sel.choices.agent.length > 1) count = sel.choices.agent.length;
      if (count !== null) env.push(`COUNT=${count}`);
      if (sel.max_parallel !== null) env.push(`MAX_PARALLEL=${sel.max_parallel}`);
      const order = ["AGENTS=", "COUNT=", "MAX_PARALLEL=", "HEADLESS="];
      const rank = (a) => order.findIndex((p) => a.startsWith(p));
      env.sort((a, b) => rank(a) - rank(b));
    }

    if (target === "sweep") {
      if (!hasExtra(sel, "parallel_tasks.app_variations")) {
        const cells = sweepCells(sel, catalog);
        overrides.push("parallel_tasks.app_variations=[" + cells.map((c) => hydraList(c, true)).join(",") + "]");
      }
      if (!hasExtra(sel, "parallel_tasks.task_names")) {
        const names = sel.choices.task_name || [defaultFor("task_name", target, catalog)];
        const value = listEq(names, [ALL_TASKS]) ? ALL_TASKS : hydraList(names, false);
        overrides.push(`parallel_tasks.task_names=${value}`);
      }
    }

    let program = pySplit(catalog.targets[target].command);
    if (target === "slurm_pool") program = [program[0], ...sel.sbatch_args, ...program.slice(1)];
    const words = [...program, ...overrides, ...sel.extra].map(shlexQuote);
    const envWords = [...env, ...sel.env].map(quoteAssignment);
    return [...envWords, ...words].join(" ");
  }

  function quoteAssignment(assignment) {
    // Quote the value only: a quoted 'NAME=value' is a command word to the
    // shell, not an assignment.
    const at = assignment.indexOf("=");
    return assignment.slice(0, at) + "=" + shlexQuote(assignment.slice(at + 1));
  }

  function runCountSel(selection, catalog) {
    const sel = normalizeSel(selection, catalog);
    const target = resolveTarget(sel, catalog);
    if (POOL_TARGETS.includes(target)) {
      if (sel.count !== null) return sel.count;
      return sel.choices.agent.length; // COUNT, rendered or the default
    }
    if (target === "sweep") {
      let names = sel.choices.task_name || [null];
      if (listEq(names, [ALL_TASKS])) {
        const option = (sel.choices.tasks || [null])[0] || defaultFor("tasks", target, catalog);
        names = catalog.tasks[option];
      }
      return sweepCells(sel, catalog).length * names.length;
    }
    return 1;
  }

  // -------------------------------------------------------------------------
  // Parse
  // -------------------------------------------------------------------------

  const SCRIPTS = {
    "launch_agent.py": "single",
    "launch_parallel_agents.py": "sweep",
    "conduct.sh": "pool",
    "conduct_slurm.sh": "slurm_pool",
  };
  const ENV_ASSIGNMENT = /^([A-Za-z_][A-Za-z0-9_]*)=([\s\S]*)$/;
  const OVERRIDE = /^(\+\+|\+|~)?([A-Za-z_][\p{L}\p{N}_./@-]*)=([\s\S]*)$/u;

  function parseScalar(raw, kind) {
    if (kind === "bool") {
      if (["true", "false"].includes(raw.toLowerCase())) return raw.toLowerCase() === "true";
      throw new RangeError(raw);
    }
    if (kind === "int") {
      // int(): surrounding whitespace, a sign and digit-group underscores.
      if (!/^\s*[+-]?\d+(_\d+)*\s*$/.test(raw)) throw new RangeError(raw);
      return Number(raw.replace(/_/g, ""));
    }
    return raw;
  }

  function splitProgram(tokens) {
    let i = 0;
    if (tokens[0] === "uv" && tokens[1] === "run") i = 2;
    else if (["python", "python3", "bash", "sh"].includes(tokens[0])) i = 1;
    const sbatch = [];
    if (tokens[i] === "sbatch") {
      i++;
      while (i < tokens.length && tokens[i].startsWith("-")) sbatch.push(tokens[i++]);
    }
    if (i >= tokens.length) throw new CommandError("no launch script found");
    const script = tokens[i].split("/").pop();
    if (!has(SCRIPTS, script)) throw new CommandError(`unknown launch script ${pyRepr(tokens[i])}`);
    return [SCRIPTS[script], sbatch, tokens.slice(i + 1)];
  }

  function decomposeCells(cells, catalog) {
    if (!Array.isArray(cells) || !cells.length) return null;
    if (cells.length === 1 && Array.isArray(cells[0]) && !cells[0].length) return {};
    let keys = null;
    const values = {};
    for (const cell of cells) {
      if (!Array.isArray(cell)) return null;
      const cellKeys = [];
      for (const item of cell) {
        const match = OVERRIDE.exec(pyStr(item));
        if (!match) return null;
        const [, prefix, key, value] = match;
        const group = has(catalog.groups, key) ? catalog.groups[key] : null;
        if (!group || !group.sweepable || (prefix || "") !== (group.append ? "+" : "") ||
            !group.options.some((o) => o.name === value)) {
          return null;
        }
        cellKeys.push(key);
        values[key] = values[key] || [];
        if (!values[key].includes(value)) values[key].push(value);
      }
      if (keys === null) keys = cellKeys;
      else if (!pyEq(keys, cellKeys)) return null;
    }
    const choices = {};
    for (const k of keys || []) choices[k] = values[k];
    const rebuilt = sweepCells({ choices }, catalog);
    return pyEq(rebuilt, cells.map((c) => c.map(pyStr))) ? choices : null;
  }

  function parseCommand(command, catalog) {
    let tokens = shlexSplit(command.split("\\\n").join(" "));
    const env = [];
    while (tokens.length && ENV_ASSIGNMENT.test(tokens[0])) {
      const [, name, value] = ENV_ASSIGNMENT.exec(tokens[0]);
      env.push([name, value]);
      tokens = tokens.slice(1);
    }
    const [target, sbatchArgs, rest] = splitProgram(tokens);
    const pool = POOL_TARGETS.includes(target);

    const sel = fromDict({ sbatch_args: sbatchArgs });
    const choices = sel.choices;
    for (const [name, value] of env) {
      if (pool && name === "AGENTS" && !has(choices, "agent")) choices.agent = pySplit(value);
      else if (pool && (name === "COUNT" || name === "MAX_PARALLEL") && /^[0-9]+$/.test(value)) {
        sel[name.toLowerCase()] = Number(value);
      } else if (pool && name === "HEADLESS" && ["true", "false"].includes(value.toLowerCase())) {
        choices["browsergym_env_args.headless"] = [value.toLowerCase() === "true"];
      } else sel.env.push(`${name}=${value}`);
    }
    if (target === "slurm_pool") {
      choices.mode = ["slurm_cluster"];
      if (sel.count === null) sel.count = 1; // conduct_slurm.sh: COUNT="${COUNT:-1}"
    }

    for (const token of rest) {
      if (!parseOverride(token, target, choices, catalog)) sel.extra.push(token);
    }
    sel.target = resolveTarget(sel, catalog) === target ? null : target;
    return normalizeSel(sel, catalog);
  }

  function parseOverride(token, target, choices, catalog) {
    const match = OVERRIDE.exec(token);
    if (!match) return false;
    const prefix = match[1] || "";
    const key = match[2];
    const raw = match[3];

    if (target === "sweep" && prefix === "" && key.startsWith("parallel_tasks.")) {
      let value;
      try {
        value = hydraValue(raw);
      } catch (e) {
        if (e instanceof SyntaxError) return false; // keep it verbatim
        throw e;
      }
      if (key === "parallel_tasks.app_variations") {
        const axes = decomposeCells(value, catalog);
        if (axes === null || Object.keys(axes).some((k) => has(choices, k))) return false;
        Object.assign(choices, axes);
        return true;
      }
      if (key === "parallel_tasks.task_names" && !has(choices, "task_name")) {
        if (value === ALL_TASKS) {
          choices.task_name = [ALL_TASKS];
          return true;
        }
        if (Array.isArray(value) && value.length && value.every((v) => typeof v === "string")) {
          choices.task_name = [...value];
          return true;
        }
      }
      return false;
    }

    if (has(choices, key)) return false; // repeated: Hydra applies the later one
    if (has(catalog.groups, key)) {
      const group = catalog.groups[key];
      if (group.sweepable && target === "sweep") return false;
      if (prefix !== (group.append ? "+" : "")) return false;
      if (!group.options.some((o) => o.name === raw)) return false;
      if (key === "mode" && POOL_TARGETS.includes(target)) return false;
      choices[key] = [raw];
      return true;
    }
    if (has(catalog.scalars, key) && prefix === "") {
      if (key === "task_name" && target === "sweep") return false;
      try {
        choices[key] = [parseScalar(raw, catalog.scalars[key].type)];
      } catch (e) {
        if (e instanceof RangeError) return false;
        throw e;
      }
      return true;
    }
    return false;
  }

  // -------------------------------------------------------------------------
  // Rules (rules.py)
  // -------------------------------------------------------------------------

  const COMPARISON = /^(<=|>=|!=|<|>)\s*(.+)$/;

  function factsSel(selection, catalog) {
    let target;
    try {
      target = resolveTarget(selection, catalog);
    } catch (e) {
      if (!(e instanceof CommandError)) throw e;
      target = null;
    }
    const effective = target || "single";
    const configName = catalog.targets[effective].config_name;
    const choices = selection.choices;

    const out = {};
    for (const path of Object.keys(catalog.groups)) {
      out[path] = [...(choices[path] && choices[path].length ? choices[path] : [defaultFor(path, effective, catalog)])];
    }

    const tracked = {};
    for (const [p, v] of Object.entries(catalog.baseline)) tracked[p] = [v];
    for (const [path, scalar] of Object.entries(catalog.scalars)) tracked[path] = [scalar.defaults[configName]];
    for (const [path, group] of Object.entries(catalog.groups)) {
      const options = group.options.filter((o) => out[path].includes(o.name));
      const keys = new Set(options.flatMap((o) => Object.keys(o.sets)));
      for (const key of keys) {
        let layered = [];
        for (const option of options) {
          layered = layered.concat(has(option.sets, key) ? [option.sets[key]] : tracked[key]);
        }
        tracked[key] = dedupe(layered);
      }
    }
    for (const [key, value] of Object.entries(catalog.targets[effective].defaults)) {
      if (has(tracked, key)) tracked[key] = [value];
    }
    for (const [key, values] of Object.entries(choices)) {
      if (has(catalog.scalars, key)) tracked[key] = [...values];
    }
    Object.assign(out, tracked);

    for (const token of selection.extra) {
      const at = token.indexOf("=");
      if (at < 0) continue;
      const key = token.slice(0, at);
      if (!/^[+~-]/.test(key)) out[key] = [yamlScalar(token.slice(at + 1))];
    }

    const names = choices.task_name && choices.task_name.length
      ? choices.task_name
      : [defaultFor("task_name", effective, catalog)];
    const apps = new Set();
    let inTasks = true;
    for (const option of out.tasks) {
      const entries = {};
      const list = typeof option === "string" && has(catalog.tasks, option) ? catalog.tasks[option] : [];
      for (const t of list) entries[t.name] = t;
      const wanted = listEq(names, [ALL_TASKS]) ? Object.keys(entries) : names;
      for (const name of wanted) {
        if (typeof name === "string" && has(entries, name)) entries[name].apps.forEach((a) => apps.add(a));
        else inTasks = false;
      }
    }
    out.task_app = [...apps].sort();
    out.task_in_tasks = [inTasks];
    out.target = [target];
    return out;
  }

  function matchesOne(condition, actual) {
    if (Array.isArray(condition)) return condition.some((c) => matchesOne(c, actual));
    if (condition === null) return actual === null;
    if (isBool(condition) || isBool(actual)) {
      // bool is an int in Python; never let True match 1.
      return isBool(condition) && isBool(actual) && condition === actual;
    }
    if (typeof condition === "string") {
      const match = COMPARISON.exec(condition);
      if (match && !condition.includes("\n")) {
        const [, op, raw] = match;
        if (op === "!=") return pyStr(actual) !== raw;
        const a = pyFloat(actual);
        const b = pyFloat(raw);
        if (a === null || b === null) return false;
        return { "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b }[op];
      }
    }
    return pyEq(condition, actual) || pyStr(condition) === pyStr(actual);
  }

  const holds = (condition, actuals) => actuals.some((a) => matchesOne(condition, a));

  function evaluateSel(selection, catalog, rules) {
    const known = factsSel(selection, catalog);
    const found = [];
    for (const rule of rules || catalog.rules) {
      const ok = Object.entries(rule.when).every(([key, cond]) =>
        holds(cond, has(known, key) ? known[key] : [null]));
      if (ok) found.push({ rule_id: rule.id, then: rule.then, reason: rule.reason, fix: rule.fix ?? null });
    }
    return found;
  }

  function applyFixSel(selection, fix, catalog) {
    return normalizeSel(withFix(selection, fix, catalog), catalog);
  }

  /** applyFixSel before normalizing: what the UI keeps as its raw state. */
  function withFix(selection, fix, catalog) {
    const choices = {};
    for (const [k, v] of Object.entries(selection.choices)) choices[k] = [...v];
    const extra = [...selection.extra];
    for (const [key, value] of Object.entries(fix)) {
      if (has(catalog.groups, key) || has(catalog.scalars, key)) choices[key] = [value];
      else {
        const token = `${key}=${formatValue(value)}`;
        let last = -1;
        extra.forEach((t, i) => { if (t.split("=")[0] === key) last = i; });
        if (last >= 0) extra[last] = token; // the one Hydra applies (last wins)
        else extra.push(token);
      }
    }
    return { ...selection, choices, extra };
  }

  function checkOptionSel(selection, key, value, catalog) {
    const before = new Set(evaluateSel(selection, catalog).map((f) => f.rule_id));
    const after = evaluateSel({ ...selection, choices: { ...selection.choices, [key]: [value] } }, catalog);
    return after.filter((f) => !before.has(f.rule_id) || ruleKeys(f.rule_id, catalog).has(key));
  }

  function ruleKeys(ruleId, catalog) {
    const rule = catalog.rules.find((r) => r.id === ruleId);
    return new Set(rule ? Object.keys(rule.when) : []);
  }

  // -------------------------------------------------------------------------
  // Public engine: Selection dicts in, JSON out (what the tests compare)
  // -------------------------------------------------------------------------

  const engine = {
    CommandError,
    ALL_TASKS,
    POOL_TARGETS,
    shlexSplit,
    shlexQuote,
    hydraValue,
    yamlScalar,
    fromDict,
    toDict,
    formatValue,
    defaultFor,
    resolveTarget: (sel, catalog) => resolveTarget(fromDict(sel), catalog),
    normalize: (sel, catalog) => toDict(normalizeSel(fromDict(sel), catalog)),
    render: (sel, catalog) => renderSel(fromDict(sel), catalog),
    runCount: (sel, catalog) => runCountSel(fromDict(sel), catalog),
    parse: (command, catalog) => toDict(parseCommand(command, catalog)),
    facts: (sel, catalog) => factsSel(fromDict(sel), catalog),
    evaluate: (sel, catalog) => evaluateSel(fromDict(sel), catalog),
    applyFix: (sel, fix, catalog) => toDict(applyFixSel(fromDict(sel), fix, catalog)),
    checkOption: (sel, key, value, catalog) => checkOptionSel(fromDict(sel), key, value, catalog),
  };
  window.OpenAppsComposer = engine;

  // -------------------------------------------------------------------------
  // The element
  // -------------------------------------------------------------------------

  // Captured while the script runs: the stylesheet and, without a `catalog`
  // attribute, the catalog sit next to this file.
  const SCRIPT_URL = (document.currentScript && document.currentScript.src) || location.href;

  // Where a group renders. Anything not named here still shows up -- under
  // "Apps" for apps/*, "More options" otherwise -- so a group added under
  // config/ (A1's apps/events, A2's benchmark) needs no change in this file.
  const MAIN_KEYS = [
    "agent", "device", "tasks", "task_name", "apps/theme", "apps/pop_ups",
    "browsergym_env_args", "experiment",
  ];
  const RUN_KEYS = [
    "browsergym_env_args.max_steps", "seed", "use_wandb",
    "browsergym_env_args.headless", "record_video",
  ];
  const THEN_LABELS = { warn: "Warning", require: "Needs", disable: "Blocked" };
  const LOCAL_ACTIONS = ["Validate", "Preview", "Record", "Run"];
  // Rough screen proportions for the preview placeholder, by device option.
  const DEVICE_RATIO = { desktop: "16 / 9", laptop: "16 / 10", phone: "9 / 19.5", tablet: "41 / 59" };
  // Per-app axes whose still the preview insets: apps/<app>/<axis>.
  const STILL_KEY = /^apps\/([^/]+)\/(layout|content)$/;
  const HEARTBEAT_MS = 5000;
  const LOG_LINES = 400;
  // Placeholder swatches (background, surface, ink, accent) for known themes;
  // anything else falls back to the default look.
  const THEME_SWATCH = {
    default: ["#f7f7f8", "#ffffff", "#1f2328", "#2f6feb"],
    dark: ["#16181d", "#22252c", "#e6e6e6", "#6aa7ff"],
    meta_dark: ["#18191a", "#242526", "#e4e6eb", "#2d88ff"],
    vscode_dark: ["#1e1e1e", "#252526", "#d4d4d4", "#3794ff"],
    mono: ["#f2f2f2", "#ffffff", "#111111", "#555555"],
    solarized: ["#fdf6e3", "#eee8d5", "#586e75", "#268bd2"],
    material: ["#fafafa", "#ffffff", "#212121", "#6200ee"],
    bootstrap: ["#f8f9fa", "#ffffff", "#212529", "#0d6efd"],
    meta: ["#f0f2f5", "#ffffff", "#1c1e21", "#0866ff"],
    colorblind: ["#ffffff", "#ffffff", "#000000", "#e69f00"],
    challenging_font: ["#fbfaf5", "#ffffff", "#2a2a2a", "#8a5cf6"],
  };

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

  const editable = (el) =>
    el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

  class OpenAppsComposerElement extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this.catalog = null;
      this.manifest = null;
      // What the user chose, un-normalized: a value equal to the current
      // target's default stays put, so switching target or mode never loses
      // it. `sel` is the normalized view the command and the hash use.
      this.raw = fromDict({});
      this.openSelect = null;
      this.query = {};
      this.multi = {};
      this.sections = {};
      this.info = null;
      this.copyStatus = "";
      this.pasteError = "";
      this.extraError = "";
      this.localResult = null;
      this.live = null;
      this.touched = null;
      this.realAgent = false;
      this.heartbeat = null;
      this.onPageHide = this.onPageHide.bind(this);
      this.writtenHash = null;
      this.onKeydown = this.onKeydown.bind(this);
      this.onDocClick = this.onDocClick.bind(this);
      this.onHashChange = this.onHashChange.bind(this);
      this.schemeObserver = new MutationObserver(() => this.syncScheme());
    }

    connectedCallback() {
      this.syncScheme();
      this.schemeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
      document.addEventListener("keydown", this.onKeydown);
      document.addEventListener("click", this.onDocClick);
      window.addEventListener("hashchange", this.onHashChange);
      const css = this.getAttribute("stylesheet") || SCRIPT_URL.replace(/\.js(\?.*)?$/, ".css");
      this.shadowRoot.replaceChildren(
        h("link", { rel: "stylesheet", href: new URL(css, document.baseURI).href }),
        (this.root = h("div", { class: "oac" }, this.header(), h("p", { class: "status-line", text: "Loading the config catalog…" }))),
      );
      this.load();
    }

    disconnectedCallback() {
      this.onPageHide();
      this.schemeObserver.disconnect();
      document.removeEventListener("keydown", this.onKeydown);
      document.removeEventListener("click", this.onDocClick);
      window.removeEventListener("hashchange", this.onHashChange);
    }

    /** Light or dark: an explicit `scheme` attribute, else the docs toggle. */
    syncScheme() {
      const explicit = this.getAttribute("scheme");
      const dark = explicit ? explicit === "dark" : document.documentElement.classList.contains("dark");
      this.dataset.scheme = dark ? "dark" : "light";
    }

    async load() {
      const attr = this.getAttribute("catalog");
      const catalogURL = new URL(attr || "catalog.json", attr ? document.baseURI : SCRIPT_URL);
      try {
        const response = await fetch(catalogURL);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        this.catalog = await response.json();
      } catch (e) {
        this.root.replaceChildren(
          this.header(),
          h("p", { class: "status-line", role: "alert" },
            `Couldn't load the config catalog from ${catalogURL.pathname} (${e.message}). Build it with `,
            h("code", { text: "uv run -m open_apps.compose.catalog --out docs/assets/composer/catalog.json" }), "."),
        );
        return;
      }
      this.readHash();
      this.render();
      // Preview media is optional: no manifest means the placeholder.
      const mediaAttr = this.getAttribute("media");
      this.manifestURL = new URL(mediaAttr || "media/manifest.json", mediaAttr ? document.baseURI : catalogURL);
      try {
        const response = await fetch(this.manifestURL);
        this.manifest = response.ok ? await response.json() : null;
      } catch (e) {
        this.manifest = null;
      }
      if (this.manifest) this.renderPreview();
    }

    // -- state ---------------------------------------------------------------

    /** The normalized selection (cached per raw state, which is replaced, never mutated). */
    get sel() {
      if (this.selFor !== this.raw || this.selCatalog !== this.catalog) {
        this.selFor = this.raw;
        this.selCatalog = this.catalog;
        this.selCache = this.normalized(this.raw);
      }
      return this.selCache;
    }

    target() {
      try {
        return resolveTarget(this.sel, this.catalog);
      } catch (e) {
        return "single";
      }
    }

    /** The values a key shows as selected: the choice, else the default. */
    current(key) {
      if (has(this.raw.choices, key)) return this.raw.choices[key];
      return [defaultFor(key, this.target(), this.catalog)];
    }

    canVary(key) {
      const group = has(this.catalog.groups, key) ? this.catalog.groups[key] : null;
      return key === "agent" || key === "task_name" || Boolean(group && group.sweepable);
    }

    normalized(sel) {
      try {
        return normalizeSel(sel, this.catalog);
      } catch (e) {
        if (e instanceof CommandError) return sel;
        throw e;
      }
    }

    /**
     * The selection after choosing `values` for `key` (null = back to the
     * default). Changing the tasks group re-points task_name at a task that
     * group has, so a group switch never leaves an unregisterable task.
     */
    withChoice(sel, key, values) {
      const choices = { ...sel.choices };
      if (!values || !values.length || (values.length === 1 && values[0] === null)) delete choices[key];
      else choices[key] = values;
      if (key === "tasks") {
        const probe = { ...sel, choices };
        let target;
        try { target = resolveTarget(probe, this.catalog); } catch (e) { target = "single"; }
        const option = (choices.tasks || [defaultFor("tasks", target, this.catalog)])[0];
        const names = (this.catalog.tasks[option] || []).map((t) => t.name);
        const fallback = this.catalog.scalars.task_name.defaults.config;
        const chosen = choices.task_name || [fallback];
        if (!listEq(chosen, [ALL_TASKS]) && !chosen.every((n) => names.includes(n))) {
          const kept = chosen.filter((n) => names.includes(n));
          const next = kept.length ? kept : [names.includes(fallback) ? fallback : names[0]];
          if (next[0] === undefined) delete choices.task_name;
          else choices.task_name = next;
        }
      }
      return { ...sel, choices };
    }

    update(sel) {
      const choices = {};
      for (const [key, values] of Object.entries(sel.choices)) {
        const unique = dedupe(values);
        if (unique.length) choices[key] = unique;
      }
      this.raw = { ...sel, choices };
      this.writeHash();
      this.render();
    }

    choose(key, values) {
      this.noteTouched(key);
      this.update(this.withChoice(this.raw, key, values));
    }

    /** Remember the last per-app axis changed: its still is the inset. */
    noteTouched(key) {
      if (STILL_KEY.test(key)) this.touched = key;
    }

    toggle(key, value) {
      this.noteTouched(key);
      const base = this.current(key).filter((v) => v !== null);
      const next = base.some((v) => pyEq(v, value)) ? base.filter((v) => !pyEq(v, value)) : [...base, value];
      this.choose(key, next.length ? next : null);
    }

    // -- URL hash: #agent=dummy&apps/theme=dark,mono ---------------------------

    writeHash() {
      const enc = (v) => encodeURIComponent(v).replace(/%2F/gi, "/").replace(/%3A/gi, ":").replace(/%40/g, "@");
      const parts = [];
      const d = toDict(this.sel);
      if (d.target) parts.push(`target=${enc(d.target)}`);
      for (const [k, values] of Object.entries(d.choices || {})) {
        parts.push(`${enc(k)}=${values.map((v) => enc(formatValue(v))).join(",")}`);
      }
      for (const k of ["count", "max_parallel"]) if (d[k] != null) parts.push(`${k}=${d[k]}`);
      for (const [k, field] of [["extra", "extra"], ["env", "env"], ["sbatch", "sbatch_args"]]) {
        for (const v of d[field] || []) parts.push(`${k}=${enc(v)}`);
      }
      const hash = parts.join("&");
      this.writtenHash = hash;
      const url = location.pathname + location.search + (hash ? "#" + hash : "");
      if (location.hash.slice(1) !== hash) history.replaceState(history.state, "", url);
    }

    readHash() {
      const raw = location.hash.slice(1);
      // A plain #anchor (a heading link) is not composer state.
      if (!raw.includes("=")) return;
      const sel = fromDict({});
      for (const part of raw.split("&")) {
        const at = part.indexOf("=");
        if (at < 0) continue;
        let key;
        try { key = decodeURIComponent(part.slice(0, at)); } catch (e) { continue; }
        const value = part.slice(at + 1);
        const dec = (v) => { try { return decodeURIComponent(v); } catch (e) { return v; } };
        if (key === "target") sel.target = has(this.catalog.targets, dec(value)) ? dec(value) : null;
        else if (key === "count" || key === "max_parallel") {
          if (/^\d+$/.test(value)) sel[key] = Number(value);
        } else if (key === "extra") sel.extra.push(dec(value));
        else if (key === "env") sel.env.push(dec(value));
        else if (key === "sbatch") sel.sbatch_args.push(dec(value));
        else if (has(this.catalog.groups, key)) {
          const names = this.catalog.groups[key].options.map((o) => o.name);
          const values = value.split(",").map(dec).filter((v) => names.includes(v));
          if (values.length) sel.choices[key] = values;
        } else if (has(this.catalog.scalars, key)) {
          const values = [];
          for (const v of value.split(",").map(dec)) {
            try { values.push(parseScalar(v, this.catalog.scalars[key].type)); } catch (e) { /* skip */ }
          }
          if (values.length) sel.choices[key] = values;
        }
      }
      this.raw = this.normalized(sel);
      this.writtenHash = raw;
      // A shared link: inset the last per-app axis it sets.
      for (const key of Object.keys(this.raw.choices)) this.noteTouched(key);
    }

    onHashChange() {
      if (!this.catalog || location.hash.slice(1) === this.writtenHash) return;
      this.raw = fromDict({});
      this.readHash();
      this.render();
    }

    // -- events --------------------------------------------------------------

    onKeydown(event) {
      if (!this.catalog) return;
      const path = event.composedPath();
      const inside = path.includes(this);
      if (event.key === "Escape" && this.openSelect && inside) {
        const key = this.openSelect;
        this.openSelect = null;
        this.render();
        this.focusKey(`trigger:${key}`);
        return;
      }
      if (event.key !== "c" || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      if (editable(path[0])) return;
      const active = document.activeElement;
      if (inside || !active || active === document.body || active === this) {
        event.preventDefault();
        this.copy();
      }
    }

    onDocClick(event) {
      // The path holds the elements as they were at dispatch; a click inside a
      // select may already have re-rendered it, so match by class, not node.
      if (!this.openSelect) return;
      const inSelect = event.composedPath().some((el) => el.classList && el.classList.contains("select"));
      if (!inSelect) {
        this.openSelect = null;
        this.render();
      }
    }

    async copy() {
      const text = this.command;
      if (!text) return;
      let ok = false;
      try {
        await navigator.clipboard.writeText(text);
        ok = true;
      } catch (e) {
        const area = h("textarea", { style: "position:fixed;opacity:0" });
        area.value = text;
        this.shadowRoot.append(area);
        area.select();
        try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
        area.remove();
      }
      this.copyStatus = ok ? "Copied" : "Copy failed: select the command and copy it by hand";
      this.renderOutput();
      clearTimeout(this.copyTimer);
      this.copyTimer = setTimeout(() => { this.copyStatus = ""; this.renderOutput(); }, 2500);
    }

    focusKey(fk) {
      const el = fk && this.shadowRoot.querySelector(`[data-fk="${CSS.escape(fk)}"]`);
      if (el) el.focus();
      return el;
    }

    showInfo(info) {
      this.info = info;
      const about = this.shadowRoot.querySelector("[data-part=about]");
      if (about) about.replaceWith(this.about());
    }

    // -- render ----------------------------------------------------------------

    render() {
      const active = this.shadowRoot.activeElement;
      const fk = active && active.dataset ? active.dataset.fk : null;
      const caret = active && "selectionStart" in active ? [active.selectionStart, active.selectionEnd] : null;
      const list = this.shadowRoot.querySelector("[role=listbox]");
      const scroll = list ? [this.openSelect, list.scrollTop] : null;
      // Before the controls: blockedReason compares against these.
      this.findingIds = new Set(evaluateSel(this.sel, this.catalog).map((f) => f.rule_id));

      this.root.replaceChildren(
        this.header(),
        h("div", { class: "body" },
          h("div", { class: "controls", "data-part": "controls" }, this.controls()),
          h("aside", { class: "side" }, h("div", { "data-part": "preview" }), this.about()),
        ),
        h("section", { class: "output", "data-part": "output", "aria-label": "Command" }),
        this.footnote(),
      );
      this.renderOutput();
      this.renderPreview();

      const el = this.focusKey(fk);
      if (el && caret) { try { el.setSelectionRange(caret[0], caret[1]); } catch (e) { /* not a text field */ } }
      const newList = this.shadowRoot.querySelector("[role=listbox]");
      if (newList && scroll && scroll[0] === this.openSelect) newList.scrollTop = scroll[1];
    }

    header() {
      return h("header", { class: "head" },
        h("p", { class: "eyebrow", text: "Config composer" }),
        h("h2", { class: "headline", text: "Build a launch command." }),
        h("p", { class: "lede" },
          "Most experiments require no Python edits.", h("br"),
          "Compose Hydra groups and overrides first."),
      );
    }

    footnote() {
      const code = (t) => h("code", { text: t });
      return h("p", { class: "footnote" },
        "Hydra shorthand: use ", code("key=value"), " to replace existing config, ",
        code("+key=value"), " to append a group absent from defaults, and ",
        code("++key=value"), " to add or replace.");
    }

    controls() {
      const catalog = this.catalog;
      const placed = new Set(["mode", ...MAIN_KEYS, ...RUN_KEYS]);
      const target = this.target();
      const auto = (() => { try { return resolveTarget({ ...this.raw, target: null }, catalog); } catch (e) { return "single"; } })();

      const main = [
        this.field("target", "Target", this.pills("target", [
          { value: null, label: `Auto (${lowerFirst(catalog.targets[auto].label)})` },
          ...Object.values(catalog.targets).filter((t) => t.pill !== false)
            .map((t) => ({ value: t.name, label: t.label })),
        ], [this.raw.target], (v) => this.update({ ...this.raw, target: v }))),
      ];
      if (has(catalog.groups, "mode")) main.push(this.groupField("mode"));
      for (const key of MAIN_KEYS) {
        if (key === "task_name") main.push(this.taskField());
        else if (has(catalog.groups, key)) main.push(this.groupField(key));
      }

      // Per-app axes (apps/todo/layout, ...) grouped by app; other apps/* groups too.
      const apps = [];
      const shell = [];
      for (const [path, group] of Object.entries(catalog.groups)) {
        if (placed.has(path) || !path.startsWith("apps/")) continue;
        const parts = path.split("/");
        if (parts.length === 2) {
          const nested = Object.keys(catalog.groups).some((p) => p.startsWith(path + "/"));
          if (!nested && group.options.length > 1) shell.push(this.groupField(path));
          continue;
        }
        const parent = parts.slice(0, 2).join("/");
        let row = apps.find((r) => r.parent === parent);
        if (!row) apps.push((row = { parent, fields: [] }));
        row.fields.push(this.groupField(path, humanAxis(parts.slice(2).join(" "))));
      }
      const more = [];
      for (const path of Object.keys(catalog.groups)) {
        if (!placed.has(path) && !path.startsWith("apps/")) more.push(this.groupField(path));
      }
      for (const key of Object.keys(catalog.scalars)) {
        if (!placed.has(key) && key !== "task_name") more.push(this.scalarField(key));
      }

      const changed = (keys) => keys.filter((k) => has(this.sel.choices, k)).length;
      const appKeys = Object.keys(catalog.groups).filter((p) => p.startsWith("apps/") && !placed.has(p));
      const runFields = RUN_KEYS.filter((k) => has(catalog.scalars, k)).map((k) => this.scalarField(k));
      runFields.push(this.countField("count", "Count", "Worker pools: total runs, round-robin over the agents."));
      runFields.push(this.countField("max_parallel", "Max parallel", "Worker pools: how many runs at once."));
      const runChanged = changed(RUN_KEYS) + (this.sel.count !== null) + (this.sel.max_parallel !== null);

      return [
        h("div", { class: "grid" }, main),
        this.section("apps", "Apps", "per-app layout and content", changed(appKeys), [
          ...apps.map((row) => h("div", { class: "app-row" },
            h("h3", { class: "app-name", text: catalog.groups[row.parent] ? catalog.groups[row.parent].label : row.parent }),
            h("div", { class: "grid" }, row.fields))),
          shell.length ? h("div", { class: "app-row" }, h("h3", { class: "app-name", text: "Shell" }), h("div", { class: "grid" }, shell)) : null,
        ]),
        this.section("run", "Run settings", "steps, seed, logging, worker pool", runChanged, [h("div", { class: "grid" }, runFields)]),
        more.length ? this.section("more", "More options", "", 0, [h("div", { class: "grid" }, more)]) : null,
        this.extrasField(),
        this.pasteField(),
      ];
    }

    section(id, title, hint, changed, body) {
      const details = h("details", {
        class: "section",
        open: Boolean(this.sections[id]),
        ontoggle: () => { this.sections[id] = details.open; },
      },
      h("summary", {},
        h("span", { class: "section-title", text: title }),
        hint ? h("span", { class: "section-hint", text: hint }) : null,
        changed ? h("span", { class: "badge", text: `${changed} changed` }) : null),
      body);
      return details;
    }

    field(key, label, control, opts = {}) {
      return h("div", { class: "field" + (opts.wide ? " wide" : ""), "data-key": key },
        h("div", { class: "field-head" },
          h("span", { class: "label", id: `label-${key}`, text: label }),
          opts.aside || null),
        control);
    }

    groupField(path, label) {
      const group = this.catalog.groups[path];
      label = label || group.label;
      const options = group.options.map((o) => ({
        value: o.name, label: o.label, summary: o.summary, details: o.details,
        blocked: this.blockedReason(path, o.name),
      }));
      if (group.nullable) {
        // The catalog names the unset choice ("App defaults" for the theme).
        options.unshift({
          value: null,
          label: group.null_label || `No ${label.toLowerCase()}`,
          summary: group.null_summary || "",
        });
      }
      if (group.control === "pills") {
        const varies = this.canVary(path);
        const multi = varies && (this.multi[path] || this.current(path).length > 1);
        const aside = varies
          ? h("button", {
            class: "multi", type: "button", "aria-pressed": String(Boolean(multi)),
            "data-fk": `multi:${path}`, title: "Pick several values to sweep over them",
            onclick: () => { this.multi[path] = !multi; this.render(); },
          }, "Sweep")
          : null;
        return this.field(path, label, this.pills(path, options, this.current(path), (v) => this.choose(path, [v]), multi, label), { aside });
      }
      return this.field(path, label, this.selectControl(path, label, options, group.searchable));
    }

    taskField() {
      const target = this.target();
      const option = this.current("tasks")[0] || defaultFor("tasks", target, this.catalog);
      const tasks = this.catalog.tasks[option] || [];
      const options = [
        { value: ALL_TASKS, label: `All ${tasks.length} tasks in ${option}`, summary: "Every task in the group: a sweep.", single: true },
        ...tasks.map((t) => ({ value: t.name, label: t.label, summary: t.goal, details: `${t.kind} · touches ${t.apps.join(", ") || "no app state"}` })),
      ];
      return this.field("task_name", "Task", this.selectControl("task_name", "Task", options, true), { wide: false });
    }

    blockedReason(key, value) {
      if (!this.disableKeys) {
        this.disableKeys = new Set();
        for (const rule of this.catalog.rules.filter((r) => r.then === "disable")) {
          for (const k of Object.keys(rule.when)) {
            if (has(this.catalog.groups, k)) this.disableKeys.add(k);
            if (k === "task_app" || k === "task_in_tasks") this.disableKeys.add("tasks");
          }
        }
      }
      if (!this.disableKeys.has(key) || this.current(key).includes(value)) return null;
      const next = this.normalized(this.withChoice(this.raw, key, [value]));
      const found = evaluateSel(next, this.catalog).find((f) =>
        f.then === "disable" && (!this.findingIds.has(f.rule_id) || ruleKeys(f.rule_id, this.catalog).has(key)));
      return found ? found.reason : null;
    }

    pills(key, options, selected, onPick, multi = false, label = null) {
      const labelId = `label-${key}`;
      const checked = (o) => selected.some((v) => pyEq(v, o.value));
      const focusable = Math.max(0, options.findIndex(checked));
      const pick = (o, event) => {
        if (o.blocked) return;
        if (o.value !== null && this.canVary(key) && (multi || (event && event.shiftKey))) this.toggle(key, o.value);
        else onPick(o.value);
      };
      const buttons = options.map((o, i) => h("button", {
        type: "button",
        class: "pill",
        role: multi ? "checkbox" : "radio",
        "aria-checked": String(checked(o)),
        "aria-disabled": o.blocked ? "true" : null,
        title: o.blocked || o.summary || null,
        tabindex: multi || i === focusable ? "0" : "-1",
        "data-fk": `pill:${key}:${o.value}`,
        onclick: (event) => pick(o, event),
        onmouseenter: () => this.showInfo(this.infoFor(key, o, label)),
        onfocus: () => this.showInfo(this.infoFor(key, o, label)),
        onkeydown: (event) => {
          const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
          let j = null;
          if (step) j = (i + step + options.length) % options.length;
          else if (event.key === "Home") j = 0;
          else if (event.key === "End") j = options.length - 1;
          if (j === null) return;
          event.preventDefault();
          const next = options[j];
          // Radios follow focus (the WAI-ARIA radio pattern); checkboxes don't.
          if (!multi && !next.blocked) pick(next, null);
          this.focusKey(`pill:${key}:${next.value}`);
        },
      }, o.label));
      return h("div", {
        class: "pills",
        role: multi ? "group" : "radiogroup",
        "aria-labelledby": labelId,
      }, buttons);
    }

    infoFor(key, option, label) {
      return {
        title: option.label,
        token: option.value === null ? `${key}: unset` : `${key}=${formatValue(option.value)}`,
        summary: option.blocked ? `Unavailable: ${option.blocked}` : option.summary || "",
        details: option.details && option.details !== option.summary ? option.details : "",
        group: label,
      };
    }

    selectControl(key, label, options, searchable) {
      const selected = this.current(key);
      const open = this.openSelect === key;
      const varies = this.canVary(key);
      const chosen = options.filter((o) => selected.some((v) => pyEq(v, o.value)));
      const summary = chosen.length === 0 ? "None"
        : chosen.length === 1 ? chosen[0].label
        : `${chosen[0].label} + ${chosen.length - 1} more`;
      const listId = `list-${key}`;
      const trigger = h("button", {
        type: "button",
        class: "select-trigger",
        "aria-haspopup": "listbox",
        "aria-expanded": String(open),
        "aria-controls": open ? listId : null,
        "aria-label": `${label}: ${summary}`,
        "data-fk": `trigger:${key}`,
        onclick: () => {
          this.openSelect = open ? null : key;
          this.render();
          if (!open) this.focusKey(searchable ? `search:${key}` : `opt:${key}:${selected[0]}`) || this.focusKey(`trigger:${key}`);
        },
      },
      h("span", { class: "select-value", text: summary }),
      chosen.length > 1 ? h("span", { class: "badge", text: String(chosen.length) }) : null,
      h("span", { class: "caret", "aria-hidden": "true" }));
      if (!open) return h("div", { class: "select" }, trigger);

      const q = (this.query[key] || "").trim().toLowerCase();
      const visible = q
        ? options.filter((o) => [o.label, o.value, o.summary].some((t) => t != null && String(t).toLowerCase().includes(q)))
        : options;
      const rows = visible.map((o) => {
        const isChecked = selected.some((v) => pyEq(v, o.value));
        const box = varies && !o.single && o.value !== null
          // Decorative for assistive tech: in a multi-select listbox
          // aria-selected already says "in the sweep", and Space toggles it
          // (the WAI-ARIA pattern). The box is the mouse's way to do that.
          ? h("span", { class: "check", "aria-hidden": "true", "data-checked": String(isChecked) })
          : null;
        return h("div", {
          class: "option" + (o.blocked ? " blocked" : ""),
          role: "option",
          "aria-selected": String(isChecked),
          "aria-disabled": o.blocked ? "true" : null,
          "aria-label": o.label,
          tabindex: "-1",
          title: o.blocked || null,
          "data-fk": `opt:${key}:${o.value}`,
          onmouseenter: () => this.showInfo(this.infoFor(key, o, label)),
          onfocus: () => this.showInfo(this.infoFor(key, o, label)),
          onclick: (event) => {
            if (o.blocked) return;
            if (box && event.composedPath().includes(box)) {
              this.toggle(key, o.value);
              this.focusKey(`opt:${key}:${o.value}`);
            } else {
              this.openSelect = null;
              this.choose(key, [o.value]);
              this.focusKey(`trigger:${key}`);
            }
          },
          onkeydown: (event) => this.optionKey(event, key, o, box),
        },
        box,
        h("span", { class: "option-label", text: o.label }),
        o.value !== null && o.value !== o.label ? h("code", { class: "option-key", text: String(o.value) }) : null);
      });
      const search = searchable
        ? h("input", {
          type: "search", class: "search", placeholder: `Search ${options.length} options`,
          "aria-label": `Search ${label}`, "aria-controls": listId, "data-fk": `search:${key}`,
          value: this.query[key] || "",
          oninput: (event) => { this.query[key] = event.target.value; this.render(); },
          onkeydown: (event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              const first = this.shadowRoot.querySelector(`#${CSS.escape(listId)} [role=option]`);
              if (first) first.focus();
            } else if (event.key === "Enter") {
              event.preventDefault();
              if (visible[0]) { this.openSelect = null; this.choose(key, [visible[0].value]); this.focusKey(`trigger:${key}`); }
            }
          },
        })
        : null;
      return h("div", { class: "select", "data-open": "" },
        trigger,
        h("div", { class: "popover" },
          search,
          h("div", {
            class: "listbox", role: "listbox", id: listId, "aria-label": label,
            "aria-multiselectable": varies ? "true" : null,
          }, rows.length ? rows : h("p", { class: "empty", text: "No match." })),
          h("p", { class: "popover-hint" },
            varies ? "Click a row to choose it · tick boxes to sweep · Esc closes" : "Click a row to choose it · Esc closes")));
    }

    optionKey(event, key, option, box) {
      const rows = [...this.shadowRoot.querySelectorAll(`.select[data-open] [role=option]`)];
      const i = rows.indexOf(event.currentTarget);
      const move = { ArrowDown: 1, ArrowUp: -1 }[event.key];
      if (move) {
        event.preventDefault();
        if (i + move < 0) this.focusKey(`search:${key}`);
        else if (rows[i + move]) rows[i + move].focus();
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        rows[event.key === "Home" ? 0 : rows.length - 1].focus();
      } else if (event.key === " " && box && !option.blocked) {
        event.preventDefault();
        this.toggle(key, option.value);
        this.focusKey(`opt:${key}:${option.value}`);
      } else if ((event.key === "Enter" || event.key === " ") && !option.blocked) {
        event.preventDefault();
        this.openSelect = null;
        this.choose(key, [option.value]);
        this.focusKey(`trigger:${key}`);
      }
    }

    scalarField(key) {
      const scalar = this.catalog.scalars[key];
      const value = this.current(key)[0];
      let control;
      if (scalar.type === "bool") {
        control = h("button", {
          type: "button", role: "switch", class: "switch", "aria-checked": String(Boolean(value)),
          "aria-labelledby": `label-${key}`, "data-fk": `switch:${key}`, title: scalar.summary,
          onclick: () => this.choose(key, [!value]),
        }, h("span", { class: "switch-knob", "aria-hidden": "true" }), h("span", { text: value ? "On" : "Off" }));
      } else {
        const explicit = has(this.raw.choices, key);
        control = h("input", {
          type: scalar.type === "int" ? "number" : "text", class: "text-input",
          inputmode: scalar.type === "int" ? "numeric" : null, min: scalar.type === "int" ? "0" : null,
          "aria-labelledby": `label-${key}`, "data-fk": `input:${key}`, title: scalar.summary,
          placeholder: value === null || value === undefined ? "" : String(value),
          value: explicit ? String(value) : "",
          oninput: (event) => {
            const raw = event.target.value.trim();
            if (!raw) return this.choose(key, null);
            try { this.choose(key, [parseScalar(raw, scalar.type)]); } catch (e) { /* mid-edit */ }
          },
        });
      }
      return this.field(key, scalar.label, control);
    }

    countField(name, label, summary) {
      return this.field(name, label, h("input", {
        type: "number", class: "text-input", inputmode: "numeric", min: "1",
        "aria-labelledby": `label-${name}`, "data-fk": `input:${name}`, title: summary,
        // MAX_PARALLEL: conduct.sh and conduct_slurm.sh both default to 4.
        placeholder: name === "count" ? "agents" : "4",
        value: this.sel[name] === null ? "" : String(this.sel[name]),
        oninput: (event) => {
          const raw = event.target.value.trim();
          this.update({ ...this.raw, [name]: /^\d+$/.test(raw) ? Number(raw) : null });
        },
      }));
    }

    extrasField() {
      const chip = (kind, field, token) => h("span", { class: "chip" },
        h("span", { class: "chip-kind", text: kind }),
        h("code", { text: token }),
        h("button", {
          type: "button", class: "chip-x", "data-fk": `chip:${field}:${token}`,
          "aria-label": `Remove ${kind} ${token}`,
          onclick: () => this.update({ ...this.raw, [field]: this.raw[field].filter((t) => t !== token) }),
        }, "×"));
      const chips = [
        ...this.sel.extra.map((t) => chip("extra override", "extra", t)),
        ...this.sel.env.map((t) => chip("env", "env", t)),
        ...this.sel.sbatch_args.map((t) => chip("sbatch", "sbatch_args", t)),
      ];
      const input = h("input", {
        type: "text", class: "text-input mono", placeholder: "agent.hostname=node-001",
        "aria-label": "Add an extra override", "data-fk": "add-extra",
        onkeydown: (event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          const token = event.target.value.trim();
          if (!OVERRIDE.test(token)) {
            this.extraError = "An override looks like key=value, +key=value or ++key=value.";
            this.render();
            return;
          }
          this.extraError = "";
          this.update({ ...this.raw, extra: [...this.raw.extra, token] });
        },
      });
      return h("div", { class: "field wide extras" },
        h("div", { class: "field-head" }, h("span", { class: "label", text: "Extra overrides" })),
        h("div", { class: "chips", "data-part": "extras" }, chips),
        input,
        this.extraError ? h("p", { class: "inline-error", role: "alert", text: this.extraError }) : null);
    }

    pasteField() {
      const area = h("textarea", {
        id: "paste", class: "text-input mono", rows: "2", spellcheck: "false", "data-fk": "paste",
        placeholder: "uv run launch_agent.py agent=dummy device=phone",
        onkeydown: (event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); apply(); }
        },
      });
      const apply = () => {
        try {
          const sel = parseCommand(area.value, this.catalog);
          this.pasteError = "";
          this.update(sel);
        } catch (e) {
          if (!(e instanceof CommandError)) throw e;
          this.pasteError = e.message;
          const keep = area.value;
          this.render();
          const again = this.shadowRoot.querySelector("#paste");
          if (again) again.value = keep;
        }
      };
      return h("div", { class: "field wide paste" },
        h("div", { class: "field-head" }, h("label", { class: "label", for: "paste", text: "Paste a command" })),
        h("div", { class: "paste-row" }, area,
          h("button", { type: "button", class: "button", "data-fk": "paste-apply", onclick: apply }, "Apply")),
        this.pasteError ? h("p", { class: "inline-error", role: "alert", "data-part": "paste-error", text: this.pasteError }) : null);
    }

    renderOutput() {
      const out = this.shadowRoot.querySelector("[data-part=output]");
      if (!out) return;
      const catalog = this.catalog;
      let error = null;
      let count = null;
      this.command = null;
      try {
        this.command = renderSel(this.sel, catalog);
        count = runCountSel(this.sel, catalog);
      } catch (e) {
        if (!(e instanceof CommandError)) throw e;
        error = e.message;
      }
      const findings = evaluateSel(this.sel, catalog);
      this.findingIds = new Set(findings.map((f) => f.rule_id));
      const target = this.target();
      const auto = !this.raw.target;

      const copy = h("button", {
        type: "button", class: "copy", "aria-keyshortcuts": "c", "data-fk": "copy",
        disabled: !this.command, onclick: () => this.copy(),
      }, "Copy command");
      const bar = h("div", { class: "bar", "data-state": error ? "error" : "ok" },
        h("span", { class: "prompt", "aria-hidden": "true", text: "$" }),
        h("code", { class: "cmd", "data-part": "command", "aria-label": "Launch command" },
          this.command || "# this selection cannot be one command (see below)"),
        copy);
      const meta = h("div", { class: "meta" },
        h("span", { "data-part": "meta" },
          count === null ? "—" : `${count} run${count === 1 ? "" : "s"}`,
          " · ", catalog.targets[target].label, auto ? " (auto)" : ""),
        this.copyStatus ? h("span", { class: "copy-status", role: "status", "data-part": "copy-status", text: this.copyStatus }) : null,
        h("span", { class: "spacer" }),
        h("button", {
          type: "button", class: "link", "data-fk": "reset",
          onclick: () => { this.multi = {}; this.update(fromDict({})); },
        }, "Reset"));
      const list = h("ul", { class: "findings", "data-part": "findings" },
        findings.map((f) => h("li", { class: `finding finding-${f.then}` },
          h("span", { class: "finding-tag", text: THEN_LABELS[f.then] || f.then }),
          h("span", { class: "finding-reason", text: f.reason }),
          f.fix ? h("button", {
            type: "button", class: "fix", "data-fk": `fix:${f.rule_id}`,
            onclick: () => this.update(withFix(this.raw, f.fix, catalog)),
          }, "Fix: set ", Object.entries(f.fix).map(([k, v]) => `${k}=${formatValue(v)}`).join(" ")) : null)));
      out.replaceChildren(...[
        bar,
        meta,
        error ? h("p", { class: "inline-error", role: "alert", "data-part": "error" }, "Can't build this command: ", error) : null,
        list,
        catalog.capabilities === "local" ? this.localActions() : null,
      ].filter(Boolean)); // replaceChildren would print a null as "null"
    }

    localActions() {
      const slurm = this.current("mode").includes("slurm_cluster");
      const busy = Boolean(this.busy);
      const result = this.localResult;
      return h("div", { class: "local", "data-part": "local" },
        h("div", { class: "local-buttons" },
          h("span", { class: "label", text: "Local" }),
          LOCAL_ACTIONS.map((name) => h("button", {
            type: "button", class: "button", "data-fk": `local:${name}`,
            disabled: !this.command || busy || (name === "Run" && slurm),
            title: name === "Run" && slurm ? "The browser never launches cluster jobs; copy the command instead." : null,
            onclick: () => this.runLocal(name),
          }, name)),
          h("label", { class: "tick", title: "Record with the selected agent instead of agent=dummy (it may call a paid API)." },
            h("input", {
              type: "checkbox", "data-fk": "local:real-agent", checked: this.realAgent,
              onchange: (event) => { this.realAgent = event.target.checked; },
            }),
            " record with the selected agent")),
        result ? this.localResultView(result) : null);
    }

    /** The outcome of the last local action: text, a config diff, or a log. */
    localResultView(result) {
      const common = { class: `local-result local-${result.kind}`, "data-part": "local-result", role: "status" };
      if (result.kind === "diff") {
        return h("div", common,
          h("p", { text: result.diff.length
            ? `Valid. ${result.diff.length} value${result.diff.length === 1 ? "" : "s"} differ from ${result.config}.yaml's defaults:`
            : `Valid. Nothing differs from ${result.config}.yaml's defaults.` }),
          result.diff.length ? h("dl", { class: "diff" }, result.diff.map((d) => [
            h("dt", { text: d.key }),
            h("dd", {}, h("s", { text: d.default }), " → ", h("b", { text: d.value })),
          ])) : null);
      }
      if (result.kind === "refused") {
        return h("div", common, h("p", { text: result.text }), h("code", { class: "refused-cmd", text: result.command }));
      }
      return h("pre", { ...common, text: result.text });
    }

    /** An endpoint URL; the `endpoint` attribute may be relative ("/"). */
    endpoint(path) {
      return new URL(path, new URL(this.getAttribute("endpoint") || "/", document.baseURI));
    }

    /** POST JSON with the page's token; -> {status, data}. */
    async post(path, payload) {
      const response = await fetch(this.endpoint(path), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Composer-Token": this.getAttribute("token") || "" },
        body: JSON.stringify(payload || {}),
      });
      let data;
      try { data = await response.json(); } catch (e) { data = { error: `HTTP ${response.status}` }; }
      return { status: response.status, data };
    }

    setResult(result) {
      this.localResult = result;
      this.renderOutput();
    }

    async runLocal(name) {
      const action = name.toLowerCase();
      const selection = toDict(this.sel);
      this.busy = action;
      this.setResult({ kind: "text", text: {
        validate: "Composing with Hydra…",
        preview: "Starting the apps (about 20 s)…",
        record: "Starting an episode…",
        run: "Starting…",
      }[action] });
      try {
        const payload = { selection };
        if (action === "record") payload.real_agent = this.realAgent;
        const { status, data } = await this.post(action, payload);
        if (action === "validate") this.showValidation(status, data);
        else if (action === "preview") this.showPreview(status, data);
        else if (status === 409 && data.refused) {
          this.setResult({ kind: "refused", text: data.error, command: data.command });
        } else if (data.ok && data.events) {
          this.busy = null;
          this.stream(data, action);
          return;
        } else this.setResult({ kind: "error", text: `${name} failed: ${data.error || `HTTP ${status}`}` });
      } catch (e) {
        this.setResult({ kind: "error", text: `${name} failed: ${e.message}` });
      }
      this.busy = null;
      this.renderOutput();
    }

    showValidation(status, data) {
      if (data.ok) this.setResult({ kind: "diff", diff: data.diff || [], config: data.config || "config" });
      else if (data.override) {
        this.setResult({ kind: "error", text: `Hydra rejected ${data.override}:\n${data.error}` });
      } else this.setResult({ kind: "error", text: data.error || `HTTP ${status}` });
    }

    showPreview(status, data) {
      if (!data.ok) {
        this.setResult({ kind: "error", text: `Preview failed: ${data.error || `HTTP ${status}`}${data.log ? "\n\n" + data.log : ""}` });
        return;
      }
      this.live = { kind: "preview", url: data.url, viewport: data.viewport || [1280, 800], device: data.device };
      this.setResult({ kind: "text", text: `Live preview at ${data.url}` });
      this.startHeartbeat();
      this.renderPreview();
    }

    /** Follow a job's log over SSE; a recording ends by playing its video. */
    stream(job, action) {
      if (this.events) this.events.close();
      const lines = [`$ ${job.command}`];
      const result = { kind: "log", text: lines.join("\n") };
      this.setResult(result);
      const events = (this.events = new EventSource(this.endpoint(job.events)));
      const flush = () => {
        result.text = lines.slice(-LOG_LINES).join("\n");
        const pre = this.shadowRoot.querySelector("[data-part=local-result]");
        if (pre && this.localResult === result) {
          pre.textContent = result.text;
          pre.scrollTop = pre.scrollHeight;
        }
      };
      events.onmessage = (event) => {
        lines.push(JSON.parse(event.data).line);
        flush();
      };
      events.addEventListener("done", (event) => {
        events.close();
        const done = JSON.parse(event.data);
        lines.push(done.returncode === 0 ? "— finished" : `— exited with ${done.returncode}`);
        flush();
        if (action === "record" && done.video) {
          this.live = { kind: "video", src: this.endpoint(done.video).href };
          this.renderPreview();
        } else if (action === "record") {
          lines.push("No video was written; see the log above.");
          flush();
        }
      });
      events.onerror = () => {
        if (events.readyState === EventSource.CLOSED) return;
        events.close();
        lines.push("— lost the connection to compose.py");
        flush();
      };
    }

    startHeartbeat() {
      clearInterval(this.heartbeat);
      window.addEventListener("pagehide", this.onPageHide);
      this.heartbeat = setInterval(async () => {
        try {
          const { data } = await this.post("preview/heartbeat");
          if (data.ok && !data.alive && this.live && this.live.kind === "preview") this.stopPreview(false);
        } catch (e) { /* the next beat retries; the server's TTL is the backstop */ }
      }, HEARTBEAT_MS);
    }

    /** Closing the tab: a beacon, since a fetch may not outlive the page. */
    onPageHide() {
      if (!this.live || this.live.kind !== "preview") return;
      clearInterval(this.heartbeat);
      const token = encodeURIComponent(this.getAttribute("token") || "");
      try { navigator.sendBeacon(this.endpoint(`preview/stop?token=${token}`)); } catch (e) { /* TTL */ }
    }

    async stopPreview(tellServer = true) {
      clearInterval(this.heartbeat);
      window.removeEventListener("pagehide", this.onPageHide);
      this.live = null;
      this.renderPreview();
      if (tellServer) {
        try { await this.post("preview/stop"); } catch (e) { /* TTL */ }
      }
    }

    renderPreview() {
      const slot = this.shadowRoot.querySelector("[data-part=preview]");
      if (!slot) return;
      if (this.live) {
        slot.replaceChildren(this.liveView());
        this.fitLive();
        return;
      }
      const device = this.current("device")[0] || "desktop";
      const theme = (has(this.catalog.groups, "apps/theme") && this.current("apps/theme")[0]) || "default";
      const deviceLabel = this.optionLabel("device", device);
      const themeLabel = this.optionLabel("apps/theme", theme);
      const clip = this.manifest && this.manifest.clips && this.manifest.clips[`${device}/${theme}`];
      const inset = this.inset();

      let screen;
      let caption;
      if (clip) {
        // No fixed aspect ratio: the clip was recorded at this device's own.
        screen = h("div", { class: "screen", "data-state": "media" },
          h("video", {
            src: new URL(clip, this.manifestURL).href, autoplay: true, muted: true, loop: true, playsinline: true,
            "aria-label": `${deviceLabel}, ${themeLabel} theme`,
            onerror: () => {
              screen.dataset.state = "error";
              screen.style.aspectRatio = DEVICE_RATIO[device] || "16 / 10";
              screen.replaceChildren(h("p", { class: "screen-note", text: `Couldn't load preview media (${clip}).` }));
            },
          }));
        caption = "Approximate — run locally for an exact preview.";
      } else {
        const [bg, surface, ink, accent] = THEME_SWATCH[theme] || THEME_SWATCH.default;
        screen = h("div", {
          class: "screen", "data-state": "placeholder", role: "img",
          "aria-label": `Placeholder preview: ${deviceLabel}, ${themeLabel} theme`,
          style: `aspect-ratio:${DEVICE_RATIO[device] || "16 / 10"};--p-bg:${bg};--p-surface:${surface};--p-ink:${ink};--p-accent:${accent}`,
        },
        h("div", { class: "mock" },
          h("div", { class: "mock-bar" }, h("i"), h("i"), h("i")),
          h("div", { class: "mock-body" },
            h("div", { class: "mock-side" }),
            h("div", { class: "mock-main" }, h("b"), h("span"), h("span"), h("span"), h("em")))),
        h("p", { class: "screen-tag" }, h("strong", { text: deviceLabel }), " · ", themeLabel));
        caption = inset
          ? "Approximate — run locally for an exact preview."
          : "Preview media isn't rendered yet; this sketch shows the device and theme you picked.";
      }
      slot.replaceChildren(
        h("figure", { class: "preview" },
          // The inset sits on the frame, not the screen: on a phone the screen
          // is too narrow to hold a desktop-shaped still without hiding it.
          h("div", { class: "frame", "data-device": device }, screen, inset),
          h("figcaption", { text: caption })));
    }

    optionLabel(key, value) {
      const group = this.catalog.groups[key];
      const option = group && group.options.find((o) => o.name === value);
      if (option) return option.label;
      return value === null && group && group.null_label ? group.null_label : String(value);
    }

    /** The still of the last app axis touched, if the manifest has one. */
    inset() {
      const stills = this.manifest && this.manifest.stills;
      if (!stills || !this.touched) return null;
      const [, app, axis] = STILL_KEY.exec(this.touched);
      const value = this.current(this.touched)[0];
      const src = stills[`${app}/${axis}/${value}`];
      if (!src) return null;
      const appGroup = this.catalog.groups[`apps/${app}`];
      const appLabel = appGroup ? appGroup.label : app;
      const label = `${appLabel} · ${humanAxis(axis)}: ${this.optionLabel(this.touched, value)}`;
      return h("figure", { class: "inset", "data-part": "inset" },
        h("img", {
          src: new URL(src, this.manifestURL).href, alt: label, loading: "lazy",
          onerror: (event) => event.target.closest("figure").remove(),
        }),
        h("figcaption", { text: label }));
    }

    /** Local mode: the live apps in a frame, or the episode just recorded. */
    liveView() {
      const live = this.live;
      const controls = h("div", { class: "live-controls" },
        live.kind === "preview" ? h("a", { class: "link", href: live.url, target: "_blank", rel: "noopener", text: "Open in a tab" }) : null,
        h("button", {
          type: "button", class: "link", "data-fk": "live:close",
          onclick: () => (live.kind === "preview" ? this.stopPreview() : (this.live = null, this.renderPreview())),
        }, live.kind === "preview" ? "Stop preview" : "Close"));
      if (live.kind === "video") {
        return h("figure", { class: "preview" },
          h("div", { class: "frame" },
            h("div", { class: "screen", "data-state": "recorded" },
              h("video", { src: live.src, controls: true, autoplay: true, muted: true, playsinline: true, "data-part": "recording" }))),
          h("figcaption", {}, "Recorded episode. ", controls));
      }
      const [width, height] = live.viewport;
      return h("figure", { class: "preview" },
        h("div", { class: "frame", "data-device": live.device },
          h("div", { class: "screen", "data-state": "live", style: `aspect-ratio:${width} / ${height}` },
            h("iframe", {
              src: live.url, title: "Live preview of the apps", "data-part": "live",
              style: `width:${width}px;height:${height}px`,
            }))),
        h("figcaption", {}, `Live — the apps at ${width}×${height}, as an agent sees them. `, controls));
    }

    /** Scale the iframe's device-sized viewport down to the panel. */
    fitLive() {
      const screen = this.shadowRoot.querySelector(".screen[data-state=live]");
      if (!screen) return;
      const frame = screen.querySelector("iframe");
      const fit = () => { frame.style.transform = `scale(${screen.clientWidth / this.live.viewport[0]})`; };
      if (this.liveObserver) this.liveObserver.disconnect();
      this.liveObserver = new ResizeObserver(fit);
      this.liveObserver.observe(screen);
      fit();
    }

    about() {
      const info = this.info;
      if (!info) {
        return h("div", { class: "about", "data-part": "about" },
          h("p", { class: "label", text: "About an option" }),
          h("p", { class: "about-empty", text: "Hover or focus any option to read its description, straight from the YAML header comment." }));
      }
      return h("div", { class: "about", "data-part": "about", "aria-live": "polite" },
        h("p", { class: "label", text: info.group || "About" }),
        h("p", { class: "about-title", text: info.title }),
        h("code", { class: "about-token", text: info.token }),
        info.summary ? h("p", { class: "about-summary", text: info.summary }) : null,
        info.details ? h("div", { class: "about-details" }, detailBlocks(info.details)) : null);
    }
  }

  /**
   * A YAML header comment as readable blocks: prose paragraphs are reflowed
   * (the comment is hard-wrapped at ~80 columns), while paragraphs holding
   * indented lines, lists or commands keep their line breaks.
   */
  function detailBlocks(text) {
    return text.split(/\n\s*\n/).map((para) => {
      const lines = para.split("\n");
      const literal = lines.some((l) => /^(\s|[-*>$]|uv |python |\.\/)/.test(l));
      return literal
        ? h("pre", { text: para })
        : h("p", { text: lines.map((l) => l.trim()).join(" ") });
    });
  }

  /** "Worker pool on SLURM" -> "worker pool on SLURM": acronyms survive. */
  const lowerFirst = (text) => text.charAt(0).toLowerCase() + text.slice(1);

  function humanAxis(text) {
    return text.charAt(0).toUpperCase() + text.slice(1).replace(/_/g, " ");
  }

  if (!customElements.get("openapps-composer")) {
    customElements.define("openapps-composer", OpenAppsComposerElement);
  }
})();
