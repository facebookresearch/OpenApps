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
    if (sel.target) {
      if (!has(targets, sel.target)) {
        throw new CommandError(
          `unknown target ${pyRepr(sel.target)}; one of ${Object.keys(targets).join(", ")}`
        );
      }
      return sel.target;
    }
    const choices = sel.choices;
    const slurm = (choices.mode || []).includes("slurm_cluster");
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
    const target = resolveTarget(sel, catalog);
    if (target === "slurm_pool" && !has(choices, "mode")) choices.mode = ["slurm_cluster"];
    if (!POOL_TARGETS.includes(target)) sel = { ...sel, count: null, max_parallel: null, sbatch_args: [] };
    if (target !== "slurm_pool") sel = { ...sel, sbatch_args: [] };

    for (const key of Object.keys(choices)) {
      const values = choices[key];
      if (key === "mode" && POOL_TARGETS.includes(target)) {
        if (listEq(values, ["local"])) delete choices[key];
        continue;
      }
      if (values.length === 1 && pyEq(values[0], defaultFor(key, target, catalog))) delete choices[key];
    }
    if (sel.target && resolveTarget({ ...sel, target: null }, catalog) === sel.target) {
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
      if (sel.count !== null) env.push(`COUNT=${sel.count}`);
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
      const agents = (sel.choices.agent || []).length || 1;
      return target === "pool" ? agents : 1;
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
    if (target === "slurm_pool") choices.mode = ["slurm_cluster"];

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
    return normalizeSel({ ...selection, choices, extra }, catalog);
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
})();
