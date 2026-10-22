/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * Offline syntax highlighting for the code editor's plain <textarea>.
 *
 * Why not CodeMirror (the `highlight` option): it is CDN-only, and the eval
 * nodes have no outbound network; and it *replaces* the #editor textarea with
 * its own DOM, which changes the accessibility tree and breaks Playwright's
 * fill() on the editor. Here the textarea stays the input element. Its text
 * is made transparent and a highlighted <pre> sits behind it, laid out with
 * the same font, padding and tab size, so the colours line up glyph for glyph
 * under the caret. The <pre> is aria-hidden: an agent reading the tree sees
 * exactly the textarea it saw before.
 *
 * The tokenizer is deliberately small: a regex scanner per language that
 * knows comments, strings, numbers, keywords, literals, function and type
 * names -- enough to read like an editor, not a parser. Unknown languages
 * render as escaped plain text, never as an error.
 *
 *   OpenAppsHighlight.highlight(text, 'python')   -> HTML string
 *   OpenAppsHighlight.attach(textarea, code, lang) -> {setLanguage, refresh}
 */
(function () {
    'use strict';

    function escapeHtml(text) {
        return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function words(list) {
        var out = Object.create(null);
        list.split(/\s+/).forEach(function (w) { if (w) { out[w] = true; } });
        return out;
    }

    // Collects output, merging adjacent plain text so the DOM stays small.
    function Emitter() {
        this.html = '';
    }
    Emitter.prototype.push = function (cls, text) {
        if (!text) { return; }
        this.html += cls
            ? '<span class="tok-' + cls + '">' + escapeHtml(text) + '</span>'
            : escapeHtml(text);
    };

    var IDENT = /[A-Za-z_$][\w$]*/y;
    var SPACE = /[ \t]+/y;

    function prevChar(src, i) {
        for (var j = i - 1; j >= 0; j--) {
            var ch = src.charAt(j);
            if (ch !== ' ' && ch !== '\t') { return ch; }
        }
        return '';
    }

    function nextChar(src, i) {
        for (var j = i; j < src.length; j++) {
            var ch = src.charAt(j);
            if (ch !== ' ' && ch !== '\t') { return ch; }
        }
        return '';
    }

    /*
     * The shared scanner for every brace/indent language. `spec.rules` are
     * tried in order at each position; an identifier that no rule claimed is
     * classified by the word lists. `declares` maps a keyword to the class of
     * the name that follows it (`def foo` -> function, `class Foo` -> type),
     * which is what makes a definition stand out the way it does in VS Code.
     */
    function scanner(spec) {
        return function (src, out) {
            var i = 0;
            var n = src.length;
            var lastWord = '';
            while (i < n) {
                var matched = false;
                for (var r = 0; r < spec.rules.length; r++) {
                    var rule = spec.rules[r];
                    rule[1].lastIndex = i;
                    var m = rule[1].exec(src);
                    if (m && m[0].length) {
                        out.push(rule[0], m[0]);
                        i += m[0].length;
                        if (rule[0] !== 'comment') { lastWord = ''; }
                        matched = true;
                        break;
                    }
                }
                if (matched) { continue; }

                IDENT.lastIndex = i;
                var id = IDENT.exec(src);
                if (id) {
                    var w = id[0];
                    out.push(classify(spec, src, i, w, lastWord), w);
                    i += w.length;
                    lastWord = w;
                    continue;
                }

                SPACE.lastIndex = i;
                var sp = SPACE.exec(src);
                if (sp) {
                    out.push(null, sp[0]);
                    i += sp[0].length;
                    continue;
                }
                var ch = src.charAt(i);
                // Punctuation ends a declaration: `class Foo(Base)` must not
                // colour `Base` as the declared name.
                if (ch !== '\n') { lastWord = ''; }
                out.push(null, ch);
                i += 1;
            }
        };
    }

    function classify(spec, src, i, w, lastWord) {
        if (spec.declares && spec.declares[lastWord] && !spec.keywords[w]) {
            return spec.declares[lastWord];
        }
        if (spec.control[w]) { return 'control'; }
        if (spec.keywords[w]) { return 'keyword'; }
        if (spec.constants[w]) { return 'constant'; }
        if (spec.types && spec.types[w]) { return 'type'; }
        var after = nextChar(src, i + w.length);
        if (prevChar(src, i) === '.') {
            return after === '(' ? 'function' : 'property';
        }
        if (after === '(') { return 'function'; }
        if (spec.capitalizedTypes && /^[A-Z]/.test(w) && !/^[A-Z0-9_]+$/.test(w)) {
            return 'type';
        }
        return null;
    }

    var NUMBER = /(?:0[xX][\da-fA-F_]+|0[bBoO][0-7_]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d+)?)[a-zA-Z]*/y;
    var DQ = /"(?:\\.|[^"\\\n])*"?/y;
    var SQ = /'(?:\\.|[^'\\\n])*'?/y;
    var BLOCK_COMMENT = /\/\*[\s\S]*?(?:\*\/|$)/y;
    var LINE_COMMENT_SLASH = /\/\/.*/y;
    var LINE_COMMENT_HASH = /#.*/y;

    var LANGS = Object.create(null);

    LANGS.python = scanner({
        rules: [
            ['comment', LINE_COMMENT_HASH],
            ['string', /[rRbBuUfF]{0,2}(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$))/y],
            ['string', /[rRbBuUfF]{0,2}(?:"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?)/y],
            ['function', /@[\w.]+/y],
            ['number', NUMBER],
        ],
        keywords: words('def class lambda global nonlocal'),
        control: words('if elif else for while try except finally return import from as with yield raise break continue pass in is not and or await async del assert match case'),
        constants: words('True False None self cls'),
        declares: { def: 'function', class: 'type' },
    });

    LANGS.javascript = scanner({
        rules: [
            ['comment', LINE_COMMENT_SLASH],
            ['comment', BLOCK_COMMENT],
            ['string', DQ],
            ['string', SQ],
            ['string', /`(?:\\[\s\S]|[^`\\])*`?/y],
            ['number', NUMBER],
        ],
        keywords: words('var let const function class extends new typeof instanceof void delete get set static interface type enum implements'),
        control: words('if else for while do return import export from default switch case break continue try catch finally throw await async yield of in as'),
        constants: words('true false null undefined NaN Infinity this super'),
        declares: { function: 'function', class: 'type', interface: 'type', type: 'type', extends: 'type' },
        capitalizedTypes: true,
    });

    LANGS.clike = scanner({
        rules: [
            ['comment', LINE_COMMENT_SLASH],
            ['comment', BLOCK_COMMENT],
            ['control', /#\s*\w+/y],
            ['string', /<[\w./]+\.h(?:pp)?>/y],
            ['string', DQ],
            ['string', SQ],
            ['number', NUMBER],
        ],
        keywords: words('int char float double void long short unsigned signed struct union enum typedef static const extern auto register volatile inline bool class public private protected virtual template typename namespace using final abstract interface extends implements package import fn let mut impl pub use mod trait where self Self'),
        control: words('if else for while do return switch case default break continue goto try catch finally throw new delete match loop sizeof'),
        constants: words('true false NULL nullptr null this'),
        declares: { struct: 'type', class: 'type', enum: 'type', union: 'type', fn: 'function', trait: 'type', impl: 'type', interface: 'type' },
        capitalizedTypes: true,
    });

    LANGS.go = scanner({
        rules: [
            ['comment', LINE_COMMENT_SLASH],
            ['comment', BLOCK_COMMENT],
            ['string', DQ],
            ['string', SQ],
            ['string', /`[^`]*`?/y],
            ['number', NUMBER],
        ],
        keywords: words('func var const type struct interface map chan'),
        control: words('package import if else for range return switch case default break continue go defer select goto fallthrough'),
        constants: words('true false nil iota'),
        types: words('int int8 int16 int32 int64 uint uint8 uint16 uint32 uint64 uintptr float32 float64 complex64 complex128 string bool byte rune error any'),
        declares: { func: 'function', type: 'type' },
        capitalizedTypes: false,
    });

    LANGS.shell = scanner({
        rules: [
            ['comment', /(?:^|(?<=\s))#.*/y],
            ['string', /"(?:\\.|[^"\\])*"?/y],
            ['string', /'[^']*'?/y],
            ['property', /\$(?:\{[^}\n]*\}?|[\w@#?$!*-]+)/y],
            ['number', /\b\d+\b/y],
        ],
        keywords: words('function local export readonly declare alias unset'),
        control: words('if then else elif fi for while until do done case esac in return exit break continue select'),
        constants: words('true false'),
        types: Object.create(null),
    });

    LANGS.json = scanner({
        rules: [
            ['property', /"(?:\\.|[^"\\\n])*"(?=\s*:)/y],
            ['string', DQ],
            ['number', /-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/y],
        ],
        keywords: Object.create(null),
        control: Object.create(null),
        constants: words('true false null'),
    });

    // CSS needs one bit of state the shared scanner does not keep: inside a
    // `{}` block a word before `:` is a property; outside, it is a selector.
    LANGS.css = function (src, out) {
        var depth = 0;
        var i = 0;
        var n = src.length;
        var rules = [
            ['comment', BLOCK_COMMENT],
            ['string', DQ],
            ['string', SQ],
            ['control', /@[\w-]+/y],
        ];
        while (i < n) {
            var done = false;
            for (var r = 0; r < rules.length; r++) {
                rules[r][1].lastIndex = i;
                var m = rules[r][1].exec(src);
                if (m && m[0].length) { out.push(rules[r][0], m[0]); i += m[0].length; done = true; break; }
            }
            if (done) { continue; }
            var ch = src.charAt(i);
            if (ch === '{') { depth++; out.push(null, ch); i++; continue; }
            if (ch === '}') { depth = Math.max(0, depth - 1); out.push(null, ch); i++; continue; }
            var re = depth > 0
                ? /(--[\w-]+|-?[a-zA-Z][\w-]*)(?=\s*:)|#[\da-fA-F]{3,8}\b|-?(?:\d+\.?\d*|\.\d+)(?:%|[a-zA-Z]+)?|[a-zA-Z-][\w-]*(?=\()|!important|[a-zA-Z-][\w-]*/y
                : /[.#][\w-]+|::?[\w-]+|[a-zA-Z][\w-]*|\*/y;
            re.lastIndex = i;
            var t = re.exec(src);
            if (t && t[0].length) {
                var tok = t[0];
                var cls;
                if (depth === 0) {
                    cls = tok.charAt(0) === ':' ? 'keyword' : 'tag';
                } else if (t[1]) {
                    cls = 'property';
                } else if (tok.charAt(0) === '#' || /^-?[\d.]/.test(tok)) {
                    cls = 'number';
                } else if (src.charAt(i + tok.length) === '(') {
                    cls = 'function';
                } else if (tok === '!important') {
                    cls = 'control';
                } else {
                    cls = 'string';
                }
                out.push(cls, tok);
                i += tok.length;
                continue;
            }
            out.push(null, ch);
            i++;
        }
    };

    // YAML is read line by line: a `key:` prefix, then a scalar. Plain
    // scalars are coloured as strings, the way VS Code's grammar does.
    LANGS.yaml = function (src, out) {
        src.split('\n').forEach(function (line, index) {
            if (index > 0) { out.push(null, '\n'); }
            var m = /^(\s*)(---|\.\.\.)(\s*)$/.exec(line);
            if (m) { out.push(null, m[1]); out.push('control', m[2]); out.push(null, m[3]); return; }
            var rest = line;
            var lead = /^(\s*(?:-\s+)*)/.exec(rest)[1];
            out.push(null, lead);
            rest = rest.slice(lead.length);
            var key = /^((?:"[^"]*"|'[^']*'|[^\s#:'"][^:#]*?))(\s*:)(?=\s|$)/.exec(rest);
            if (key) {
                out.push('property', key[1]);
                out.push(null, key[2]);
                rest = rest.slice(key[0].length);
            }
            yamlValue(rest, out);
        });
    };

    function yamlValue(rest, out) {
        var lead = /^\s*/.exec(rest)[0];
        out.push(null, lead);
        rest = rest.slice(lead.length);
        if (!rest) { return; }
        if (rest.charAt(0) === '#') { out.push('comment', rest); return; }
        var comment = '';
        var c = rest.search(/\s#/);
        // A `#` inside quotes is not a comment; only split unquoted scalars.
        if (c >= 0 && !/^["']/.test(rest)) { comment = rest.slice(c); rest = rest.slice(0, c); }
        var anchor = /^([&*][\w-]+)(\s*)/.exec(rest);
        if (anchor) { out.push('type', anchor[1]); out.push(null, anchor[2]); rest = rest.slice(anchor[0].length); }
        if (/^(?:true|false|null|yes|no|on|off|~)\s*$/i.test(rest)) {
            out.push('constant', rest);
        } else if (/^[-+]?(?:\d[\d_]*\.?\d*(?:e[-+]?\d+)?|\.inf|\.nan)\s*$/i.test(rest)) {
            out.push('number', rest);
        } else if (/^[|>][-+]?\s*$/.test(rest) || /^[\[\]{},]/.test(rest)) {
            out.push('keyword', rest);
        } else {
            out.push('string', rest);
        }
        if (comment) {
            var sp = /^\s*/.exec(comment)[0];
            out.push(null, sp);
            out.push('comment', comment.slice(sp.length));
        }
    }

    // Markdown, also line by line. A fenced block is highlighted in its own
    // language when it names one we know, the way an editor previews it.
    LANGS.markdown = function (src, out) {
        // First group the lines into prose lines and fenced blocks, then emit
        // them joined by the original newlines -- so the output always has
        // exactly as many lines as the textarea.
        var parts = [];
        var fence = null;
        src.split('\n').forEach(function (line) {
            var f = /^\s*(```|~~~)\s*([\w+-]*)/.exec(line);
            if (fence) {
                if (f && f[1] === fence.marker) {
                    parts.push(fence);
                    parts.push({ fence: line });
                    fence = null;
                } else {
                    fence.lines.push(line);
                }
                return;
            }
            if (f) {
                var lang = f[2].toLowerCase();
                parts.push({ fence: line });
                fence = { marker: f[1], lang: ALIASES[lang] || lang, lines: [] };
                return;
            }
            parts.push({ line: line });
        });
        // An unclosed fence still shows what was typed inside it.
        if (fence) { parts.push(fence); }

        var first = true;
        parts.forEach(function (part) {
            if (part.lines && !part.lines.length) { return; }
            if (!first) { out.push(null, '\n'); }
            first = false;
            if (part.fence !== undefined) { out.push('comment', part.fence); return; }
            if (part.lines) {
                var body = part.lines.join('\n');
                if (LANGS[part.lang]) { LANGS[part.lang](body, out); } else { out.push('string', body); }
                return;
            }
            markdownLine(part.line, out);
        });
    };

    function markdownLine(line, out) {
        if (/^\s{0,3}#{1,6}(\s|$)/.test(line)) { out.push('tag', line); return; }
        if (/^\s*>/.test(line)) { out.push('comment', line); return; }
        var li = /^(\s*)([-*+]|\d+[.)])(\s+)/.exec(line);
        if (li) {
            out.push(null, li[1]); out.push('keyword', li[2]); out.push(null, li[3]);
            line = line.slice(li[0].length);
        }
        markdownInline(line, out);
    }

    function markdownInline(line, out) {
        var re = /(`[^`]*`)|(\*\*[^*]+\*\*|__[^_]+__)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(\[[^\]]*\])(\([^)]*\))?/g;
        var last = 0;
        var m;
        while ((m = re.exec(line))) {
            out.push(null, line.slice(last, m.index));
            if (m[1]) { out.push('string', m[1]); }
            else if (m[2]) { out.push('constant', m[2]); }
            else if (m[3]) { out.push('type', m[3]); }
            else { out.push('string', m[4]); if (m[5]) { out.push('property', m[5]); } }
            last = m.index + m[0].length;
        }
        out.push(null, line.slice(last));
    }

    LANGS.html = function (src, out) {
        var i = 0;
        var n = src.length;
        while (i < n) {
            var rest = src.slice(i);
            var m = /^<!--[\s\S]*?(?:-->|$)/.exec(rest);
            if (m) { out.push('comment', m[0]); i += m[0].length; continue; }
            m = /^(<\/?)([\w-]+)/.exec(rest);
            if (m) {
                out.push(null, m[1]);
                out.push('tag', m[2]);
                i += m[0].length;
                // Attributes until the tag closes.
                var attr = /\s+|([\w:-]+)|(=)|("[^"]*"?|'[^']*'?)|(\/?>)/y;
                while (i < n) {
                    attr.lastIndex = i;
                    var a = attr.exec(src);
                    if (!a) { break; }
                    if (a[1]) { out.push('property', a[1]); }
                    else if (a[3]) { out.push('string', a[3]); }
                    else { out.push(null, a[0]); }
                    i += a[0].length;
                    if (a[4]) { break; }
                }
                continue;
            }
            m = /^&[#\w]+;/.exec(rest);
            if (m) { out.push('constant', m[0]); i += m[0].length; continue; }
            var next = rest.search(/[<&]/);
            var text = next <= 0 ? rest.charAt(0) : rest.slice(0, next);
            out.push(null, text);
            i += text.length;
        }
    };

    // Names a markdown fence (or a caller) may use for the same scanner.
    var ALIASES = {
        py: 'python', js: 'javascript', ts: 'javascript', typescript: 'javascript',
        jsx: 'javascript', tsx: 'javascript', sh: 'shell', bash: 'shell', zsh: 'shell',
        yml: 'yaml', md: 'markdown', c: 'clike', cpp: 'clike', 'c++': 'clike',
        java: 'clike', rust: 'clike', rs: 'clike', golang: 'go', htm: 'html',
    };

    function highlight(text, lang) {
        var key = ALIASES[lang] || lang;
        var out = new Emitter();
        if (LANGS[key]) {
            LANGS[key](text, out);
        } else {
            out.push(null, text);
        }
        return out.html;
    }

    /*
     * Wire a textarea to its highlight layer. The layer re-renders on every
     * `input` event -- which Playwright's fill() and type() both fire -- and
     * follows the textarea's scroll position. The textarea only turns
     * transparent once this has run (`is-highlighted` on the wrapper), so a
     * page whose script failed still shows plain, readable text.
     */
    function attach(textarea, code, lang) {
        var pre = code.parentNode;
        var wrap = textarea.parentNode;
        var state = { lang: lang };
        function sync() {
            pre.scrollTop = textarea.scrollTop;
            pre.scrollLeft = textarea.scrollLeft;
        }
        function render() {
            // The trailing newline gives a final empty line a line box, so the
            // caret on a new last line still has colour-layer text beside it.
            code.innerHTML = highlight(textarea.value, state.lang) + '\n';
            sync();
        }
        textarea.addEventListener('input', render);
        textarea.addEventListener('scroll', sync);
        wrap.classList.add('is-highlighted');
        render();
        return {
            setLanguage: function (next) {
                state.lang = next;
                wrap.setAttribute('data-language', next);
                render();
            },
            refresh: render,
        };
    }

    window.OpenAppsHighlight = {
        highlight: highlight,
        attach: attach,
        languages: Object.keys(LANGS),
    };
})();
