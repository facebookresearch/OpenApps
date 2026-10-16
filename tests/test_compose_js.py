"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""The docs-site composer (``docs/assets/composer/composer.js``) against the
Python reference (``open_apps.compose``).

Two halves. The parity tests run every shared fixture in
``tests/fixtures/compose/`` -- plus a few hand-written edge cases -- through
the JS port in a headless browser and require exactly what the Python
produces: same command, same parsed selection, same findings, same error
text. The behaviour tests drive the ``<openapps-composer>`` element itself,
served with a freshly built catalog from a throwaway local HTTP server.

No Node and nothing from a CDN: the browser is Playwright's Chromium (already
a dependency), and the page loads only the files served here. One browser
context per module, because Chromium in some sandboxes only runs with
``--single-process``, which cannot open a second context.
"""

import functools
import json
import re
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from open_apps.compose.catalog import build_catalog
from open_apps.compose.command import (
    CommandError,
    Selection,
    normalize,
    parse,
    render,
    run_count,
)
from open_apps.compose.rules import apply_fix, check_option, evaluate, facts

playwright_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[1]
ASSETS = ROOT / "docs" / "assets" / "composer"
COMPOSER_JS = ASSETS / "composer.js"
COMPOSER_CSS = ASSETS / "composer.css"
DOCS_PAGE = ROOT / "docs" / "composer.md"
FIXTURES = Path(__file__).parent / "fixtures" / "compose"
COMMANDS = json.loads((FIXTURES / "command_cases.json").read_text())
RULES = json.loads((FIXTURES / "rules_cases.json").read_text())

# Commands no fixture covers, compared against whatever Python makes of them
# (including the error text): quoting, Hydra list grammar, scripts, pools.
EDGE_COMMANDS = [
    'uv run launch_agent.py "apps.todo.title=Super Todo" agent=dummy',
    "uv run launch_agent.py apps.todo.title=Super\\ Todo",
    "uv run launch_agent.py 'apps.todo.title=it'\"'\"'s'",
    "uv run launch_agent.py agent=not_an_agent device=phone",
    "uv run launch_agent.py +experiment=phone experiment=phone",
    "uv run launch_agent.py use_wandb=yes seed=1_000 seed=2",
    "uv run launch_agent.py browsergym_env_args.max_steps=abc",
    "uv run launch_parallel_agents.py 'parallel_tasks.task_names=[ a , b ]'",
    "uv run launch_parallel_agents.py parallel_tasks.task_names=[1,2]",
    "uv run launch_parallel_agents.py parallel_tasks.task_names=a,b",
    "uv run launch_parallel_agents.py 'parallel_tasks.task_names=choice(a,b)'",
    "uv run launch_parallel_agents.py 'parallel_tasks.app_variations=[[\"apps/todo/layout=kanban_board\",\"apps/theme=dark\"]]'",
    "uv run launch_parallel_agents.py 'parallel_tasks.app_variations=[[apps/theme=dark],[apps/theme=mono]]'",
    "uv run launch_parallel_agents.py task_name=x agent=dummy",
    "AGENTS='dummy  claude_4_sonnet' MAX_PARALLEL=x FOO=1 ./scripts/conduct.sh mode=local",
    "HEADLESS=false bash scripts/conduct.sh",
    "sbatch --qos=q scripts/conduct_slurm.sh device=phone",
    "python3 launch_agent.py",
    "uv run train.py",
    "uv run",
    "",
]

# Selections no fixture covers: defaults, coercions, impossible combinations.
EDGE_SELECTIONS = [
    {"choices": {"agent": ["default"], "seed": [233]}},
    {"target": "sweep", "choices": {"agent": ["dummy"]}},
    {"target": "sweep", "choices": {"apps/theme": ["default"]}},
    {"target": "cluster"},
    {"target": "pool", "choices": {"mode": ["slurm_cluster"]}},
    {"choices": {"task_name": ["all"]}, "target": "single"},
    {"choices": {"agent": ["dummy", "dummy"]}},
    {"choices": {"agent": ["dummy", "claude_4_sonnet"], "seed": [1, 2]}},
    {"choices": {"made_up": ["x"]}},
    {"choices": {"apps/theme": ["dark", "mono"], "task_name": ["a", "b", "c"]}},
    {"choices": {"tasks": ["longer_horizon"], "task_name": ["all"]}},
    {"count": 3},
    {"max_parallel": 2, "sbatch_args": ["--qos=q"]},
]


# --------------------------------------------------------------------------
# Browser and server
# --------------------------------------------------------------------------


def _launch(playwright):
    """Chromium with default flags, else the flags that work in sandboxes."""
    errors = []
    for kwargs in (
        {},
        {
            "chromium_sandbox": False,
            "args": ["--no-sandbox", "--single-process", "--disable-gpu"],
        },
    ):
        try:
            browser = playwright.chromium.launch(**kwargs)
            context = browser.new_context()
            context.new_page().close()
            return browser, context
        except playwright_api.Error as exc:
            errors.append(str(exc).splitlines()[0])
    pytest.skip("no usable chromium -- " + "; ".join(errors))


@pytest.fixture(scope="module")
def catalog():
    return build_catalog()


@pytest.fixture(scope="module")
def site(tmp_path_factory, catalog):
    """A served directory: the composer assets, two catalogs, and pages
    embedding the element exactly as ``docs/composer.md`` does."""
    root = tmp_path_factory.mktemp("composer_site")
    assets = root / "assets" / "composer"
    assets.mkdir(parents=True)
    shutil.copy(COMPOSER_JS, assets)
    shutil.copy(COMPOSER_CSS, assets)
    (assets / "catalog.json").write_text(catalog.to_json())
    local = json.loads(catalog.to_json())
    local["capabilities"] = "local"
    (assets / "catalog-local.json").write_text(json.dumps(local))

    embed = _embed_markup()
    head = (
        '<meta charset="utf-8">'
        '<link rel="stylesheet" href="assets/composer/composer.css">'
    )
    script = '<script src="assets/composer/composer.js"></script>'
    (root / "index.html").write_text(f"<!doctype html>{head}{embed}{script}")
    local_embed = embed.replace("catalog.json", "catalog-local.json")
    (root / "local.html").write_text(f"<!doctype html>{head}{local_embed}{script}")
    (root / "blank.html").write_text(f"<!doctype html>{head}{script}")

    handler = functools.partial(_QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _embed_markup() -> str:
    """The ``<openapps-composer>`` tag from the docs page, with its catalog
    URL rewritten from the page-relative docs path to this server's layout."""
    match = re.search(r"<openapps-composer\b[^>]*>\s*</openapps-composer>", DOCS_PAGE.read_text())
    assert match, "docs/composer.md must embed <openapps-composer>"
    return re.sub(r'catalog="[^"]*"', 'catalog="assets/composer/catalog.json"', match.group(0))


@pytest.fixture(scope="module")
def browser_context(site):
    with playwright_api.sync_playwright() as playwright:
        browser, context = _launch(playwright)
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=site)
        yield context
        browser.close()


@pytest.fixture(scope="module")
def js(browser_context, site, catalog):
    """A page with composer.js loaded and the catalog in ``window.catalog``."""
    page = browser_context.new_page()
    page.goto(f"{site}/blank.html")
    page.evaluate("c => { window.catalog = c }", json.loads(catalog.to_json()))
    yield page
    page.close()


def call(page, fn: str, *args):
    """``OpenAppsComposer.<fn>(...args, catalog)`` -> value, or {"error": msg}."""
    return page.evaluate(
        """([fn, args]) => {
            try {
                return {value: OpenAppsComposer[fn](...args, window.catalog)};
            } catch (e) {
                if (e instanceof OpenAppsComposer.CommandError) return {error: e.message};
                throw e;
            }
        }""",
        [fn, list(args)],
    )


def py(fn, *args):
    try:
        return {"value": fn(*args)}
    except CommandError as exc:
        return {"error": str(exc)}


def _ids(cases):
    return [c["name"] for c in cases]


# --------------------------------------------------------------------------
# Parity: render / parse
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", COMMANDS["cases"], ids=_ids(COMMANDS["cases"]))
def test_render_parity(js, case):
    assert call(js, "render", case["selection"]) == {"value": case["command"]}


@pytest.mark.parametrize("case", COMMANDS["cases"], ids=_ids(COMMANDS["cases"]))
def test_parse_parity(js, case):
    assert call(js, "parse", case["command"]) == {"value": case["selection"]}


@pytest.mark.parametrize("case", COMMANDS["cases"], ids=_ids(COMMANDS["cases"]))
def test_run_count_parity(js, catalog, case):
    expected = run_count(Selection.from_dict(case["selection"]), catalog)
    assert call(js, "runCount", case["selection"]) == {"value": expected}


@pytest.mark.parametrize("case", COMMANDS["parse_cases"], ids=_ids(COMMANDS["parse_cases"]))
def test_parse_hand_written_parity(js, case):
    assert call(js, "parse", case["command"]) == {"value": case["selection"]}
    assert call(js, "render", case["selection"]) == {"value": case["rendered"]}


@pytest.mark.parametrize("case", COMMANDS["errors"], ids=_ids(COMMANDS["errors"]))
def test_error_parity(js, catalog, case):
    expected = py(render, Selection.from_dict(case["selection"]), catalog)
    assert "error" in expected and re.search(case["match"], expected["error"])
    assert call(js, "render", case["selection"]) == expected


@pytest.mark.parametrize("command", EDGE_COMMANDS)
def test_edge_command_parity(js, catalog, command):
    parsed = py(lambda: parse(command, catalog).to_dict())
    assert call(js, "parse", command) == parsed
    if "value" in parsed:
        rendered = py(render, Selection.from_dict(parsed["value"]), catalog)
        assert call(js, "render", parsed["value"]) == rendered


@pytest.mark.parametrize("selection", EDGE_SELECTIONS, ids=json.dumps)
def test_edge_selection_parity(js, catalog, selection):
    sel = Selection.from_dict(selection)
    assert call(js, "render", selection) == py(render, sel, catalog)
    assert call(js, "normalize", selection) == py(
        lambda: normalize(sel, catalog).to_dict()
    )
    assert call(js, "runCount", selection) == py(run_count, sel, catalog)


# --------------------------------------------------------------------------
# Parity: rules
# --------------------------------------------------------------------------


def _finding_dicts(findings):
    return [
        {"rule_id": f.rule_id, "then": f.then, "reason": f.reason, "fix": f.fix}
        for f in findings
    ]


@pytest.mark.parametrize("case", RULES["cases"], ids=_ids(RULES["cases"]))
def test_evaluate_parity(js, catalog, case):
    expected = _finding_dicts(evaluate(Selection.from_dict(case["selection"]), catalog))
    assert [f["rule_id"] for f in expected] == case["findings"]
    assert call(js, "evaluate", case["selection"]) == {"value": expected}


@pytest.mark.parametrize("case", RULES["cases"], ids=_ids(RULES["cases"]))
def test_facts_parity(js, catalog, case):
    expected = facts(Selection.from_dict(case["selection"]), catalog)
    assert call(js, "facts", case["selection"]) == {"value": expected}


@pytest.mark.parametrize("case", RULES["cases"], ids=_ids(RULES["cases"]))
def test_apply_fix_parity(js, catalog, case):
    selection = Selection.from_dict(case["selection"])
    for finding in evaluate(selection, catalog):
        if finding.fix:
            expected = apply_fix(selection, finding.fix, catalog).to_dict()
            got = call(js, "applyFix", case["selection"], finding.fix)
            assert got == {"value": expected}, finding.rule_id


@pytest.mark.parametrize(
    "selection,key,value",
    [
        ({"choices": {"task_name": ["navigate_to_onlineshop"]}}, "apps/onlineshop/content", "default"),
        ({"choices": {"task_name": ["navigate_to_onlineshop"]}}, "apps/onlineshop/content", "german"),
        ({"choices": {"tasks": ["longer_horizon"]}}, "browsergym_env_args.max_steps", 10),
        ({}, "tasks", "longer_horizon"),
        ({}, "agent", "gemma-4-2B-computer-use"),
    ],
)
def test_check_option_parity(js, catalog, selection, key, value):
    expected = _finding_dicts(check_option(Selection.from_dict(selection), key, value, catalog))
    assert call(js, "checkOption", selection, key, value) == {"value": expected}


def test_extra_override_values_are_read_as_yaml(js, catalog):
    """Rules see `key=value` extras the way PyYAML reads them."""
    extras = [
        "a.b=False", "a.c=no", "a.d=~", "a.e=", "a.f=12", "a.g=0x1f",
        "a.h=1.5", "a.i=.inf", "a.j='quoted'", "a.k=[1, two]", "a.l=node-001",
    ]
    selection = {"extra": extras}
    expected = facts(Selection.from_dict(selection), catalog)
    got = call(js, "facts", selection)["value"]
    for token in extras:
        key = token.split("=", 1)[0]
        assert got[key] == expected[key], token
