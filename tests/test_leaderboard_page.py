"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The docs-site leaderboard (``docs/assets/leaderboard/leaderboard.js``),
driven in a headless browser over leaderboard.json files that the real
``build`` produced from fixture bundles.

Same approach as the composer's browser tests: no Node, nothing from a CDN,
Playwright's Chromium served by a throwaway local HTTP server, and one
browser context per module (Chromium in some sandboxes only runs with
``--single-process``, which cannot open a second context).

Screenshots of the populated and empty states, desktop and narrow, are
written only when ``LEADERBOARD_SHOTS_DIR`` is set (never into the repo):

    LEADERBOARD_SHOTS_DIR=/tmp/leaderboard-shots uv run -m pytest tests/test_leaderboard_page.py
"""

import functools
import json
import os
import re
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from open_apps.leaderboard import bundle as B
from open_apps.leaderboard.build import build
from open_apps.leaderboard.validate import validate_bundle
from tests._leaderboard_helpers import EXAMPLE_BUNDLE, TINY_SUITE

playwright_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[1]
ASSETS = ROOT / "docs" / "assets" / "leaderboard"
DOCS_PAGE = ROOT / "docs" / "leaderboard.md"
WHEN = "2026-10-16T00:00:00Z"
EL = "openapps-leaderboard"


# --------------------------------------------------------------------------
# Fixture leaderboards, built by the real build
# --------------------------------------------------------------------------


def _lines(path):
    import gzip

    with gzip.open(path / B.EPISODES_FILE, "rt") as fh:
        return [json.loads(line) for line in fh]


def _clone(root, org, agent="dummy", **meta_changes):
    dst = root / B.bundle_name(org, agent, "tiny_v1")
    shutil.copytree(EXAMPLE_BUNDLE, dst)
    meta = yaml.safe_load((dst / B.META_FILE).read_text())
    meta.update(org=org, agent=agent, **meta_changes)
    (dst / B.META_FILE).write_text(yaml.safe_dump(meta, sort_keys=False))
    return dst


def _make_worse(bundle):
    """Undo the aligned delete in the dark cell (a real outcome change), and
    re-score so the submitted scores stay honest."""
    from open_apps.alignment.record import EpisodeRecord
    from open_apps.alignment.score import score

    lines = _lines(bundle)
    for line in lines:
        ident = line["identity"]
        if ident["task_name"] == "delete_call_aunt_lisa" and ident["variation"] == [
            "apps/theme=dark"
        ]:
            line["final_state"] = json.loads(json.dumps(line["initial_state"]))
        record = EpisodeRecord.from_dict(
            {k: v for k, v in line.items() if k != "scores"}
        )
        line["scores"] = score(record).summary()
    B.write_episodes(lines, bundle / B.EPISODES_FILE)


def _verification(bundle, **shown) -> dict:
    """A verifications.yaml entry pinned to ``bundle`` as it is now."""
    from open_apps.leaderboard import verifications as VF

    meta = yaml.safe_load((bundle / B.META_FILE).read_text())
    return {
        "bundle": bundle.name,
        "episodes_sha256": VF.episodes_sha256(bundle),
        "agent_config_hash": meta["agent_config_hash"],
        **shown,
    }


def _populated(root: Path) -> dict:
    """Bundles as the docs build sees them: the example, a twin under an
    agent name with no config (a tie), a worse copy, a partial one and one
    that no longer validates. The page shows the ranked three only."""
    root.mkdir()
    example = _clone(root, "example")
    twin = _clone(root, "twin", agent="twin-agent")
    entries = [
        _verification(example, date="2026-10-20", seeds_hash="abc123", aligned=0.48, delta=-0.02),
        _verification(twin, date="2026-10-21", seeds_hash="def456", aligned=0.25, delta=-0.25),
    ]  # fmt: skip
    _make_worse(_clone(root, "worse"))
    partial = _clone(root, "partial")
    B.write_episodes(_lines(partial)[:2], partial / B.EPISODES_FILE)
    meta = yaml.safe_load((partial / B.META_FILE).read_text())
    meta["n_episodes"] = 2
    (partial / B.META_FILE).write_text(yaml.safe_dump(meta, sort_keys=False))
    stale = _clone(root, "stale")
    lines = _lines(stale)
    lines[0]["scores"]["aligned"] = not lines[0]["scores"]["aligned"]
    B.write_episodes(lines, stale / B.EPISODES_FILE)
    reports = [validate_bundle(p, TINY_SUITE) for p in B.find_bundles([root])]
    return build(reports, when=WHEN, lenient=True, verifications=entries)


XSS = '<img src=x onerror="window.__pwned=1">'
JS_URL = "javascript:window.__pwned=1"


def _hostile(full: dict) -> dict:
    """The populated document with markup in every string the page shows
    -- row, config, episodes -- and ``javascript:`` URLs where links once
    were. The page must show them as text and link none of them."""
    doc = json.loads(json.dumps(full))
    tab = doc["suites"]["tiny_v1"]
    row = tab["rows"][0]
    row.update(
        agent=XSS, org="</td><script>window.__pwned=1</script>", model=XSS, date=XSS
    )
    details = row["details"]
    details["command"] = XSS
    details["config"][0]["items"].append([XSS, XSS])
    details["breakdowns"]["theme"][0]["value"] = XSS
    for e in details["episodes"]:
        e.update(task=XSS, goal=XSS, message=XSS, final_action=XSS, command=JS_URL)
        e["changes"] = [XSS]
        for step in e["trace"]:
            step.update(action=XSS, think=XSS)
    tab["suite"] = XSS
    return doc


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """A served directory laid out like the built docs site: the page at
    /leaderboard/, the assets beside it."""
    root = tmp_path_factory.mktemp("leaderboard_site")
    assets = root / "assets" / "leaderboard"
    assets.mkdir(parents=True)
    shutil.copy(ASSETS / "leaderboard.js", assets)
    shutil.copy(ASSETS / "leaderboard.css", assets)
    data = root / "data"
    data.mkdir()
    full = _populated(tmp_path_factory.mktemp("subs") / "submissions")
    (data / "full.json").write_text(json.dumps(full))
    (data / "empty.json").write_text(json.dumps(build([], when=WHEN)))
    (data / "hostile.json").write_text(json.dumps(_hostile(full)))

    embed = _embed_markup()
    head = '<meta charset="utf-8"><link rel="stylesheet" href="../assets/leaderboard/leaderboard.css">'
    script = '<script src="../assets/leaderboard/leaderboard.js"></script>'
    pages = {
        "full": "../data/full.json",
        "empty": "../data/empty.json",
        "missing": "../data/nope.json",
        "hostile": "../data/hostile.json",
    }
    for name, src in pages.items():
        page_dir = root / name
        page_dir.mkdir()
        body = embed.replace('src="__SRC__"', f'src="{src}"')
        anchor = '<h2 id="submit">Submit</h2><p>steps</p>'
        (page_dir / "index.html").write_text(
            f"<!doctype html>{head}<article>{body}{anchor}</article>{script}"
        )

    handler = functools.partial(_QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{server.server_address[1]}", "full": full}
    server.shutdown()


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _embed_markup() -> str:
    """The element tag from docs/leaderboard.md, its src made a placeholder."""
    match = re.search(rf"<{EL}\b[^>]*>\s*</{EL}>", DOCS_PAGE.read_text())
    assert match, f"docs/leaderboard.md must embed <{EL}>"
    assert 'src="../assets/leaderboard/leaderboard.json"' in match.group(0)
    return re.sub(r'src="[^"]*"', 'src="__SRC__"', match.group(0))


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
def browser_context(site):
    with playwright_api.sync_playwright() as playwright:
        browser, context = _launch(playwright)
        yield context
        browser.close()


@pytest.fixture
def page(browser_context, site):
    page = browser_context.new_page()
    page.set_viewport_size({"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    def goto(name="full", hash_=""):
        page.goto(f"{site['url']}/{name}/{hash_}")
        page.wait_for_selector(
            f"{EL} .olb > :not(.head):not(.status-line), {EL} [role=alert]"
        )
        return page

    page.goto_board = goto
    yield page
    page.close()
    assert errors == [], errors


def agents(page) -> list[str]:
    return page.locator(f"{EL} table.board tbody tr.row .agent-name").all_inner_texts()


def header(page, key):
    return page.locator(f'{EL} th:has(button[data-sort="{key}"])')


def row(page, agent):
    return page.locator(
        f"{EL} tr.row",
        has=page.locator(".agent-name", has_text=re.compile(f"^{agent}$")),
    )


def open_model(page, agent="dummy", nth=0):
    row(page, agent).nth(nth).locator("a.model-link").click()
    page.wait_for_selector(f"{EL} .model")
    return page.locator(f"{EL} .model")


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------


def test_renders_the_fixture_leaderboard(page):
    page.goto_board()
    # The suite with rows opens first; align_v1 (no rows) is the other tab.
    tabs = page.locator(f"{EL} [role=tablist] [role=tab]")
    assert tabs.evaluate_all("els => els.map(e => e.dataset.suite)") == [
        "align_v1",
        "tiny_v1",
    ]
    assert tabs.locator(".tab-count").all_inner_texts() == ["0", "3"]
    # Ranked by success, then fewer steps: the tie shares rank 1, the worse
    # copy (its dark-cell delete undone) is 3rd.
    assert agents(page) == ["dummy", "twin-agent", "dummy"]
    ranks = page.locator(f"{EL} tbody tr.row td.col-rank").all_inner_texts()
    assert ranks == ["1", "1", "3"]
    # Rank, model, success, avg steps, total steps, tasks, tokens in / out.
    keys = page.locator(f"{EL} thead button[data-sort]").evaluate_all(
        "els => els.map(e => e.dataset.sort)"
    )
    assert keys == [
        "rank",
        "agent",
        "success",
        "steps",
        "total_steps",
        "tasks",
        "tokens_in",
        "tokens_out",
    ]
    assert header(page, "success").get_attribute("aria-sort") == "descending"
    cells = row(page, "twin-agent").locator("td").all_inner_texts()
    # Success is the task's own check (4/6); steps 18 over 6 episodes.
    assert cells[1].startswith("66.7%")
    assert cells[2:6] == ["3.0", "18", "6", "201"]
    assert "95% CI" in row(page, "twin-agent").locator("td").nth(1).get_attribute(
        "title"
    )
    assert "18 tasks" in page.locator(f"{EL} .meta-line").inner_text()


def test_no_alignment_columns_and_no_submission_chrome(page):
    page.goto_board()
    text = page.locator(f"{EL} .olb").inner_text()
    for gone in (
        "Aligned",
        "Fooled",
        "Harm",
        "Honest",
        "Ask@ambiguous",
        "Worst variation",
        "Partial submissions",
        "Not currently valid",
        "verified",
        "submi",
    ):
        assert gone not in text, gone
    # The heading: logomark, OpenApps, Leaderboard, Alignment score; no eyebrow.
    head = page.locator(f"{EL} .head")
    assert head.locator("svg.logomark").count() == 1
    assert head.locator(".wordmark").inner_text() == "OpenApps"
    assert head.locator(".headline").inner_text() == "Leaderboard"
    assert head.locator(".subtitle").inner_text() == "Alignment score"
    assert page.locator(f"{EL} .eyebrow").count() == 0


def test_sorting_by_column(page):
    page.goto_board()
    page.locator(f'{EL} button[data-sort="agent"]').click()
    assert agents(page) == ["dummy", "dummy", "twin-agent"]
    assert header(page, "agent").get_attribute("aria-sort") == "ascending"
    assert header(page, "success").get_attribute("aria-sort") is None
    # Success: first click is best-first (descending), second flips it.
    page.locator(f'{EL} button[data-sort="success"]').click()
    assert header(page, "success").get_attribute("aria-sort") == "descending"
    page.locator(f'{EL} button[data-sort="success"]').click()
    assert header(page, "success").get_attribute("aria-sort") == "ascending"
    assert row(page, "dummy").first.locator("td.col-rank").inner_text() == "3"
    # Fewer steps first; the state is in the URL hash.
    page.locator(f'{EL} button[data-sort="total_steps"]').click()
    assert header(page, "total_steps").get_attribute("aria-sort") == "ascending"
    assert "sort=total_steps" in page.evaluate("location.hash")


def test_headers_sort_from_the_keyboard(page):
    page.goto_board()
    button = page.locator(f'{EL} button[data-sort="steps"]')
    button.focus()
    page.keyboard.press("Enter")
    assert header(page, "steps").get_attribute("aria-sort") == "ascending"
    # Focus stays on the header across the re-render.
    assert (
        page.evaluate(
            f"document.querySelector('{EL}').shadowRoot.activeElement.dataset.sort"
        )
        == "steps"
    )
    page.keyboard.press("Space")
    assert header(page, "steps").get_attribute("aria-sort") == "descending"


def test_sort_state_is_read_from_the_hash(page):
    page.goto_board(hash_="#sort=agent&dir=desc")
    assert header(page, "agent").get_attribute("aria-sort") == "descending"
    assert agents(page) == ["twin-agent", "dummy", "dummy"]


def test_success_toggles_between_percent_and_fraction(page):
    page.goto_board()
    success = (
        lambda: row(page, "twin-agent").locator("td.col-success .val").inner_text()
    )  # noqa: E731
    assert success() == "66.7%"
    page.locator(f'{EL} button[data-fmt="float"]').click()
    assert success() == "0.667"
    assert (
        page.locator(f'{EL} button[data-fmt="float"]').get_attribute("aria-pressed")
        == "true"
    )
    assert "fmt=float" in page.evaluate("location.hash")
    # The model page follows the same format.
    open_model(page, "twin-agent")
    assert page.locator(f"{EL} .tile.good .tile-value").inner_text() == "0.667"


def test_token_columns_toggle(page):
    page.goto_board()
    assert page.locator(f"{EL} thead th").count() == 8
    toggle = page.locator(f'{EL} button[data-toggle="tokens"]')
    toggle.click()
    assert page.locator(f"{EL} thead th").count() == 6
    assert header(page, "tokens_in").count() == 0
    assert "tokens=off" in page.evaluate("location.hash")
    page.goto_board(hash_="#tokens=off")
    assert page.locator(f"{EL} thead th").count() == 6


def test_empty_state(page):
    page.goto_board("empty")
    empty = page.locator(f"{EL} .empty")
    assert "No models yet." in empty.inner_text()
    assert "open_apps.leaderboard.preview" in empty.inner_text()
    assert empty.locator("a").count() == 0
    assert page.locator(f"{EL} table").count() == 0


def test_tab_switch_shows_the_empty_suite(page):
    page.goto_board()
    page.locator(f'{EL} [role=tab][data-suite="align_v1"]').click()
    assert "No models yet" in page.locator(f"{EL} .empty").inner_text()
    assert "suite=align_v1" in page.evaluate("location.hash")
    page.locator(f'{EL} [role=tab][data-suite="align_v1"]').focus()
    page.keyboard.press("ArrowRight")
    assert (
        page.locator(f"{EL} [role=tab][aria-selected=true]").get_attribute("data-suite")
        == "tiny_v1"
    )


# --------------------------------------------------------------------------
# The model page
# --------------------------------------------------------------------------


def test_model_page_numbers(page):
    page.goto_board()
    model = open_model(page)
    assert "model=example__dummy__tiny_v1" in page.evaluate("location.hash")
    assert model.locator(".model-name").inner_text().startswith("dummy")
    assert "Rank #1" in model.locator(".rank-badge").inner_text()
    assert model.locator(".model-where").inner_text() == "6 tasks · 2 apps"
    # Focus moves to the model's name for keyboard and screen-reader users.
    assert (
        page.evaluate(f"document.querySelector('{EL}').shadowRoot.activeElement.id")
        == "model-name"
    )
    tiles = model.locator(".tile")
    assert tiles.locator(".tile-label").all_inner_texts() == [
        "Success",
        "Mistakes",
        "Steps",
        "Tokens in / out",
    ]
    assert tiles.nth(0).locator(".tile-value").inner_text() == "66.7%"
    assert "4 successful / 6 tasks" in tiles.nth(0).inner_text()
    assert tiles.nth(1).locator(".tile-value").inner_text() == "2"
    assert tiles.nth(2).locator(".tile-value").inner_text() == "18"
    assert "avg 3.0 · median 2.0 · avg on successes 3.8" in tiles.nth(2).inner_text()
    assert tiles.nth(3).locator(".tile-value").inner_text() == "201 / 20"


def test_model_page_config_is_the_redacted_agent_yaml(page):
    page.goto_board()
    config = open_model(page).locator(".config")
    assert "from the bundle's agent.yaml" in config.inner_text()
    pairs = dict(
        zip(
            config.locator(".kv th").all_inner_texts(),
            config.locator(".kv td").all_inner_texts(),
        )
    )
    assert pairs["client_type"] == "dummy"
    assert pairs["api_key"] == "<redacted>" and pairs["hostname"] == "<redacted>"


def test_model_page_breakdowns(page):
    page.goto_board()
    breakdown = open_model(page).locator(".breakdown")
    names = breakdown.locator("tbody th").all_inner_texts()
    # App rows, each followed by its layouts.
    assert names[0] == "Todo" and "default" in names[1]
    assert "Other" in names
    breakdown.locator('[data-by="theme"]').click()
    breakdown = page.locator(f"{EL} .breakdown")
    rows = [r.split("\t") for r in breakdown.locator("tbody tr").all_inner_texts()]
    assert [r[0] for r in rows] == ["default", "dark"]
    assert "by=theme" in page.evaluate("location.hash")
    breakdown.locator('[data-by="device"]').click()
    assert "desktop · 1920×1080" in page.locator(f"{EL} .breakdown tbody").inner_text()


def test_model_page_examples(page):
    page.goto_board()
    examples = open_model(page).locator(".examples")
    # Mistakes first: the two add_call_mom episodes, with why they ended.
    assert examples.locator("tr.ep").count() == 2
    tags = examples.locator("tr.ep .tag").all_inner_texts()
    assert sorted(tags) == ["Claimed done", "Refused"]
    detail = examples.locator("article.episode")
    assert (
        "Add 'Call Mom' to my todo list." in detail.locator(".quote").first.inner_text()
    )
    assert detail.locator(".trace li").count() >= 1
    # Successes, and picking one.
    examples.locator('[data-ex="successes"]').click()
    examples = page.locator(f"{EL} .examples")
    assert examples.locator("tr.ep").count() == 4
    pick = examples.locator("tr.ep", has_text="delete_call_aunt_lisa").first.locator(
        "button.ep-pick"
    )
    pick.click()
    detail = page.locator(f"{EL} article.episode")
    assert "todo · items · removed · Call Aunt Lisa" in detail.inner_text()
    assert "ex=successes" in page.evaluate("location.hash") and "ep=" in page.evaluate(
        "location.hash"
    )
    # A bundle carries no screenshots: no media, and no dead links.
    assert detail.locator(".media, img, video").count() == 0


def test_model_page_from_a_link_and_back(page):
    page.goto_board(hash_="#model=twin__twin-agent__tiny_v1&ex=successes")
    assert page.locator(f"{EL} .model-name").inner_text().startswith("twin-agent")
    assert (
        page.locator(f'{EL} [data-ex="successes"]').get_attribute("aria-selected")
        == "true"
    )
    # The breadcrumb returns to the table, focused on the model's link.
    page.locator(f"{EL} .crumbs a").click()
    page.wait_for_selector(f"{EL} table.board")
    assert (
        page.evaluate(
            f"document.querySelector('{EL}').shadowRoot.activeElement.dataset.model"
        )
        == "twin__twin-agent__tiny_v1"
    )
    # Opening a model is a history entry: Back returns to the table.
    open_model(page)
    page.go_back()
    page.wait_for_selector(f"{EL} table.board")
    assert page.locator(f"{EL} .model").count() == 0


def test_an_unknown_model_falls_back_to_the_table(page):
    page.goto_board(hash_="#model=nope")
    assert page.locator(f"{EL} table.board").count() == 1


def test_ci_method_is_surfaced(page, site):
    page.goto_board()
    footnote = page.locator(f"{EL} .footnote")
    assert "task-clustered bootstrap" in footnote.inner_text()
    method = footnote.locator("details.ci-method")
    method.locator("summary").click()
    assert site["full"]["bootstrap"]["ci_method"] in method.inner_text()


def test_hostile_strings_render_as_text_and_link_nothing(page):
    page.goto_board("hostile")
    root = f"document.querySelector('{EL}').shadowRoot"
    for hash_ in (
        "",
        "#model=example__dummy__tiny_v1",
        "#model=example__dummy__tiny_v1&ex=successes&by=theme",
    ):
        page.goto_board("hostile", hash_)
        assert page.evaluate("window.__pwned") is None
        assert (
            page.evaluate(f"{root}.querySelectorAll('img, script, video').length") == 0
        )
        hrefs = page.evaluate(
            f"[...{root}.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))"
        )
        # Only in-page links: hash state, never a script URL.
        assert hrefs and all(h.startswith("#") for h in hrefs), hrefs
        assert not any("javascript" in h.lower() for h in hrefs), hrefs
    # The strings are there, as text.
    assert XSS in page.locator(f"{EL} .olb").inner_text()


def test_missing_file_explains_how_to_build_it(page):
    page.goto_board("missing")
    alert = page.locator(f"{EL} [role=alert]")
    assert "open_apps.leaderboard.build" in alert.inner_text()


def test_reduced_motion_is_respected():
    css = (ASSETS / "leaderboard.css").read_text()
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_follows_the_docs_dark_toggle(page):
    page.goto_board()
    assert page.locator(EL).get_attribute("data-scheme") == "light"
    page.evaluate("document.documentElement.classList.add('dark')")
    page.wait_for_function(f"document.querySelector('{EL}').dataset.scheme === 'dark'")


# --------------------------------------------------------------------------
# Screenshots (opt-in)
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("LEADERBOARD_SHOTS_DIR"),
    reason="set LEADERBOARD_SHOTS_DIR to write screenshots",
)
def test_screenshots(page):
    out = Path(os.environ["LEADERBOARD_SHOTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    for width, height, label in ((1280, 900, "desktop"), (390, 844, "narrow")):
        page.set_viewport_size({"width": width, "height": height})
        page.goto_board("full")
        page.locator(EL).screenshot(path=str(out / f"populated-{label}.png"))
        page.evaluate("document.documentElement.classList.add('dark')")
        page.wait_for_function(
            f"document.querySelector('{EL}').dataset.scheme === 'dark'"
        )
        page.locator(EL).screenshot(path=str(out / f"populated-{label}-dark.png"))
        page.evaluate("document.documentElement.classList.remove('dark')")
        page.goto_board("full", "#model=worse__dummy__tiny_v1")
        page.locator(EL).screenshot(path=str(out / f"model-{label}.png"))
        page.goto_board("empty")
        page.locator(EL).screenshot(path=str(out / f"empty-{label}.png"))
