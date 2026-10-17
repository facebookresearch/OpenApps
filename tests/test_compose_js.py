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
    'uv run launch_parallel_agents.py \'parallel_tasks.app_variations=[["apps/todo/layout=kanban_board","apps/theme=dark"]]\'',
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
    {"target": "pool", "choices": {"mode": ["slurm_cluster"], "agent": ["dummy"]}},
    {"target": "slurm_pool"},
    {"target": "slurm_pool", "choices": {"mode": ["local"]}},
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
    match = re.search(
        r"<openapps-composer\b[^>]*>\s*</openapps-composer>", DOCS_PAGE.read_text()
    )
    assert match, "docs/composer.md must embed <openapps-composer>"
    return re.sub(
        r'catalog="[^"]*"', 'catalog="assets/composer/catalog.json"', match.group(0)
    )


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


@pytest.mark.parametrize(
    "case", COMMANDS["parse_cases"], ids=_ids(COMMANDS["parse_cases"])
)
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
        (
            {"choices": {"task_name": ["navigate_to_onlineshop"]}},
            "apps/onlineshop/content",
            "default",
        ),
        (
            {"choices": {"task_name": ["navigate_to_onlineshop"]}},
            "apps/onlineshop/content",
            "german",
        ),
        (
            {"choices": {"tasks": ["longer_horizon"]}},
            "browsergym_env_args.max_steps",
            10,
        ),
        ({}, "tasks", "longer_horizon"),
        ({}, "agent", "gemma-4-2B-computer-use"),
    ],
)
def test_check_option_parity(js, catalog, selection, key, value):
    expected = _finding_dicts(
        check_option(Selection.from_dict(selection), key, value, catalog)
    )
    assert call(js, "checkOption", selection, key, value) == {"value": expected}


def test_extra_override_values_are_read_as_yaml(js, catalog):
    """Rules see `key=value` extras the way PyYAML reads them."""
    extras = [
        "a.b=False",
        "a.c=no",
        "a.d=~",
        "a.e=",
        "a.f=12",
        "a.g=0x1f",
        "a.h=1.5",
        "a.i=.inf",
        "a.j='quoted'",
        "a.k=[1, two]",
        "a.l=node-001",
    ]
    selection = {"extra": extras}
    expected = facts(Selection.from_dict(selection), catalog)
    got = call(js, "facts", selection)["value"]
    for token in extras:
        key = token.split("=", 1)[0]
        assert got[key] == expected[key], token


# --------------------------------------------------------------------------
# The element
# --------------------------------------------------------------------------


@pytest.fixture
def ui(browser_context, site):
    """A fresh page on the embedded composer; ``ui(path)`` navigates it."""
    page = browser_context.new_page()
    page.set_viewport_size({"width": 1280, "height": 900})

    def goto(path="index.html"):
        page.goto(f"{site}/{path}")
        page.wait_for_selector("openapps-composer [data-part=command]")
        return page

    yield goto
    page.close()


def command_text(page) -> str:
    return page.locator("openapps-composer [data-part=command]").inner_text().strip()


def expect_command(page, expected: str) -> None:
    page.wait_for_function(
        """want => document.querySelector("openapps-composer")
            .shadowRoot.querySelector("[data-part=command]").textContent.trim() === want""",
        arg=expected,
        timeout=5000,
    )


def pill(page, group: str, label: str):
    return page.get_by_role("radiogroup", name=group, exact=True).get_by_role(
        "radio", name=label, exact=True
    )


def test_renders_the_default_command_from_the_catalog(ui):
    page = ui()
    assert command_text(page) == "uv run launch_agent.py"
    assert page.get_by_role("heading", name="Build a launch command.").is_visible()
    assert "1 run" in page.locator("openapps-composer [data-part=meta]").inner_text()


def test_choosing_an_agent_updates_the_command_and_the_hash(ui):
    page = ui()
    page.get_by_role("button", name=re.compile("^Agent")).click()
    page.get_by_role("option", name="Dummy / smoke test", exact=True).click()
    expect_command(page, "uv run launch_agent.py agent=dummy")
    assert "agent=dummy" in page.evaluate("location.hash")


def test_the_hash_restores_a_selection(ui):
    page = ui("index.html#agent=dummy&device=phone")
    expect_command(page, "uv run launch_agent.py agent=dummy device=phone")
    phone = pill(page, "Device", "Phone")
    assert phone.get_attribute("aria-checked") == "true"


def test_arrow_keys_move_between_pills(ui):
    page = ui()
    pill(page, "Device", "Desktop").focus()
    page.keyboard.press("ArrowRight")
    expect_command(page, "uv run launch_agent.py device=laptop")
    assert pill(page, "Device", "Laptop").get_attribute("aria-checked") == "true"


def test_ticking_several_options_makes_a_sweep(ui, catalog):
    page = ui()
    page.get_by_role("button", name=re.compile("^Theme")).click()
    listbox = page.get_by_role("listbox", name="Theme")
    for label in ("Dark", "Mono"):
        # The tick box (mouse) toggles membership without closing the list.
        listbox.get_by_role("option", name=label, exact=True).locator(".check").click()
    expected = render(
        Selection.from_dict({"choices": {"apps/theme": ["dark", "mono"]}}), catalog
    )
    expect_command(page, expected)
    assert (
        listbox.get_by_role("option", name="Mono", exact=True).get_attribute(
            "aria-selected"
        )
        == "true"
    )
    assert "2 runs" in page.locator("openapps-composer [data-part=meta]").inner_text()


def test_switching_task_groups_keeps_a_valid_task(ui, catalog):
    page = ui()
    pill(page, "Tasks", "Longer horizon").click()
    first = catalog.tasks["longer_horizon"][0].name
    expect_command(
        page,
        f"uv run launch_agent.py tasks=longer_horizon task_name={first}",
    )
    findings = page.locator("openapps-composer [data-part=findings]").inner_text()
    assert "not a key of the selected tasks group" not in findings


def test_an_impossible_selection_shows_the_reference_error(ui, catalog):
    page = ui("index.html#device=desktop,phone")
    with pytest.raises(CommandError) as raised:
        render(
            Selection.from_dict({"choices": {"device": ["desktop", "phone"]}}), catalog
        )
    error = page.locator("openapps-composer [data-part=error]")
    error.wait_for()
    assert str(raised.value) in error.inner_text()
    copy = page.get_by_role("button", name=re.compile("Copy command"))
    assert copy.is_disabled()


def test_a_rule_fix_is_one_click(ui):
    page = ui(
        "index.html#tasks=longer_horizon&task_name=plan_elena_birthday_todo_message"
        "&browsergym_env_args.max_steps=20"
    )
    findings = page.locator("openapps-composer [data-part=findings]")
    assert "unwinnable by construction" in findings.inner_text()
    page.get_by_role(
        "button", name=re.compile("browsergym_env_args.max_steps=40")
    ).click()
    expect_command(
        page,
        "uv run launch_agent.py tasks=longer_horizon "
        "task_name=plan_elena_birthday_todo_message browsergym_env_args.max_steps=40",
    )
    assert "unwinnable" not in findings.inner_text()


@pytest.mark.parametrize(
    "case",
    [
        c
        for c in COMMANDS["cases"]
        if c.get("selection", {}).get("extra") or "parallel" in c["command"]
    ],
    ids=lambda c: c["name"],
)
def test_paste_a_command_round_trips(ui, case):
    page = ui()
    page.get_by_label("Paste a command").fill(case["command"])
    page.get_by_role("button", name="Apply", exact=True).click()
    expect_command(page, case["command"])


def test_extra_overrides_are_removable_chips(ui, catalog):
    page = ui()
    case = next(
        c for c in COMMANDS["cases"] if c["name"] == "extra overrides are kept verbatim"
    )
    page.get_by_label("Paste a command").fill(case["command"])
    page.get_by_role("button", name="Apply", exact=True).click()
    chips = page.locator("openapps-composer [data-part=extras]")
    assert "agent.hostname=node-001" in chips.inner_text()
    page.get_by_role(
        "button", name="Remove extra override agent.hostname=node-001"
    ).click()
    remaining = dict(case["selection"], extra=["apps.todo.title=Super Todo"])
    expect_command(page, render(Selection.from_dict(remaining), catalog))


def test_a_bad_paste_reports_inline_and_keeps_the_selection(ui):
    page = ui("index.html#agent=dummy")
    page.get_by_label("Paste a command").fill("uv run train.py")
    page.get_by_role("button", name="Apply", exact=True).click()
    assert (
        "unknown launch script 'train.py'"
        in page.locator("openapps-composer [data-part=paste-error]").inner_text()
    )
    assert command_text(page) == "uv run launch_agent.py agent=dummy"


def test_c_copies_the_command(ui):
    page = ui("index.html#agent=dummy")
    page.locator("body").click(position={"x": 2, "y": 2})
    page.keyboard.press("c")
    status = page.locator("openapps-composer [data-part=copy-status]")
    status.wait_for()
    assert "Copied" in status.inner_text()
    assert (
        page.evaluate("navigator.clipboard.readText()")
        == "uv run launch_agent.py agent=dummy"
    )


def test_target_pills_leave_slurm_to_the_mode_row(ui):
    page = ui("index.html#mode=slurm_cluster&agent=dummy")
    targets = page.get_by_role("radiogroup", name="Target", exact=True)
    labels = [t.strip() for t in targets.get_by_role("radio").all_inner_texts()]
    assert labels == [
        "Auto (worker pool on SLURM)",
        "Single run",
        "Sweep",
        "Worker pool",
    ]
    expect_command(page, "AGENTS=dummy sbatch scripts/conduct_slurm.sh")

    page = ui("index.html#agent=gemma-4-2B-computer-use")
    pill(page, "Target", "Worker pool").click()
    expect_command(page, "AGENTS=gemma-4-2B-computer-use ./scripts/conduct.sh")
    pill(page, "Mode", "Slurm cluster").click()
    expect_command(
        page, "AGENTS=gemma-4-2B-computer-use sbatch scripts/conduct_slurm.sh"
    )


def test_local_only_buttons_need_local_capabilities(ui):
    page = ui()
    assert page.get_by_role("button", name="Validate").count() == 0
    page = ui("local.html")
    for name in ("Validate", "Preview", "Record", "Run"):
        assert page.get_by_role("button", name=name, exact=True).is_visible()


def test_preview_falls_back_to_a_placeholder_without_media(ui):
    page = ui("index.html#device=phone&apps/theme=dark")
    preview = page.locator("openapps-composer [data-part=preview]")
    preview.locator("[data-state=placeholder]").wait_for()
    text = preview.inner_text()
    assert "Phone" in text and "Dark" in text


def test_follows_the_docs_dark_mode_toggle(ui):
    page = ui()
    host = page.locator("openapps-composer")
    assert host.get_attribute("data-scheme") == "light"
    page.evaluate("document.documentElement.classList.add('dark')")
    page.wait_for_function(
        "document.querySelector('openapps-composer').dataset.scheme === 'dark'"
    )


def test_narrow_screens_stack_the_preview_under_the_controls(ui):
    page = ui()
    page.set_viewport_size({"width": 390, "height": 844})
    controls = page.locator("openapps-composer [data-part=controls]").bounding_box()
    preview = page.locator("openapps-composer [data-part=preview]").bounding_box()
    assert preview["y"] >= controls["y"] + controls["height"] - 1
    assert preview["width"] <= 390
