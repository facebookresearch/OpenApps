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
    """Four bundles: the example (verified, small delta), a tie under an agent
    name with no config (verified, large delta), a worse one, a partial one --
    plus one that no longer validates. Verification comes from
    verifications.yaml entries, never from the bundles."""
    root.mkdir()
    example = _clone(
        root, "example",
        wandb_report="https://wandb.ai/example/report", videos=["https://example.org/v1.mp4"],
    )  # fmt: skip
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
    # Merged once, now failing (a tampered score stands in for drift): the
    # lenient docs build lists it apart instead of failing the deploy.
    stale = _clone(root, "stale")
    lines = _lines(stale)
    lines[0]["scores"]["aligned"] = not lines[0]["scores"]["aligned"]
    B.write_episodes(lines, stale / B.EPISODES_FILE)
    reports = [validate_bundle(p, TINY_SUITE) for p in B.find_bundles([root])]
    return _with_per_app_axes(
        build(reports, when=WHEN, lenient=True, verifications=entries)
    )


def _with_per_app_axes(doc: dict) -> dict:
    """The tiny suite only varies the theme, so give the worse row the
    breakdown a content cell produces (``aggregate.axes_from_variation``):
    per-app ``<app>.content`` keys plus the app-less combination, and the
    partial row an unscorable episode."""

    def cell(aligned, success):
        return {
            "aligned": {"n": 1, "rate": aligned},
            "task_success": {"n": 1, "rate": success},
        }

    tab = doc["suites"]["tiny_v1"]
    worse = next(r for r in tab["rows"] if r["id"] == "worse__dummy__tiny_v1")
    worse["by_axis"].update(
        {
            "todo.content": {"german": cell(0.0, 1.0)},
            "calendar.content": {"german": cell(1.0, 1.0)},
            "content": {"calendar:german+todo:german": cell(0.5, 1.0)},
        }
    )
    tab["partial"][0]["excluded_unscorable"] = 1
    return doc


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """A served directory laid out like the built docs site: the page at
    /leaderboard/, the assets beside it, the composer at /composer/."""
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
    (root / "composer").mkdir()
    (root / "composer" / "index.html").write_text(
        "<!doctype html><title>composer</title>"
    )

    embed = _embed_markup()
    head = '<meta charset="utf-8"><link rel="stylesheet" href="../assets/leaderboard/leaderboard.css">'
    script = '<script src="../assets/leaderboard/leaderboard.js"></script>'
    pages = {
        "full": "../data/full.json",
        "empty": "../data/empty.json",
        "missing": "../data/nope.json",
        # The site once the composer page is merged: docs/leaderboard.md
        # then sets composer-href, and Reproduce appears.
        "with-composer": "../data/full.json",
    }
    for name, src in pages.items():
        page_dir = root / name
        page_dir.mkdir()
        body = embed.replace('src="__SRC__"', f'src="{src}"')
        if name == "with-composer":
            body = body.replace(f"<{EL} ", f'<{EL} composer-href="../composer/" ', 1)
        submit = '<h2 id="submit">Submit</h2><p>steps</p>'
        (page_dir / "index.html").write_text(
            f"<!doctype html>{head}<article>{body}{submit}</article>{script}"
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


def test_reproduce_is_only_offered_when_the_site_has_a_composer():
    """docs/leaderboard.md names the composer page only once it exists on
    this branch; until then a Reproduce link would 404."""
    tag = _embed_markup()
    if (ROOT / "docs" / "composer.md").is_file():
        assert 'composer-href="../composer/"' in tag
    else:
        assert "composer-href" not in tag


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
    return page.locator(
        f"{EL} table.board:not(.partial-table) tbody tr.row .agent-name"
    ).all_inner_texts()


def header(page, key):
    return page.locator(f'{EL} th:has(button[data-sort="{key}"])')


def row(page, agent):
    return page.locator(
        f"{EL} tr.row",
        has=page.locator(".agent-name", has_text=re.compile(f"^{agent}$")),
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def test_renders_the_fixture_leaderboard(page, site):
    page.goto_board()
    # The suite with rows opens first; align_v1 (no rows) is the other tab.
    tabs = page.locator(f"{EL} [role=tablist] [role=tab]")
    assert tabs.evaluate_all("els => els.map(e => e.dataset.suite)") == [
        "align_v1",
        "tiny_v1",
    ]
    assert tabs.locator(".tab-count").all_inner_texts() == ["0", "3"]
    assert (
        page.locator(f"{EL} [role=tab][aria-selected=true]").get_attribute("data-suite")
        == "tiny_v1"
    )
    # Ranked by aligned: the tie shares rank 1, the worse copy is 3rd.
    assert agents(page) == ["dummy", "twin-agent", "dummy"]
    ranks = page.locator(f"{EL} tbody tr.row td.col-rank").all_inner_texts()
    assert ranks == ["1", "1", "3"]
    # A real <table>: header cells with scope, the default sort announced.
    assert (
        page.locator(
            f"{EL} table.board:not(.partial-table) thead th[scope=col]"
        ).count()
        == 11
    )
    assert header(page, "aligned").get_attribute("aria-sort") == "descending"
    assert header(page, "success").get_attribute("aria-sort") is None
    first = row(page, "twin-agent")
    cells = first.locator("td").all_inner_texts()
    # rank, aligned, success, worst, fooled, harm, honest, ask, steps, tokens
    # Success is the task's own check: the untouched-state refuse task the
    # agent never refused counts there (4/6), not in Aligned (3/6).
    assert cells[1].startswith("50.0%") and cells[2].startswith("66.7%")
    assert cells[4].startswith("—")  # fooled: nothing adversarial shown
    assert cells[7].startswith("50.0%")  # ask@ambiguous: 1 of the 2 ambiguity episodes
    assert "n=2" in first.locator("td").nth(7).get_attribute("title")
    assert "apps/theme=dark" in cells[3]
    # CI whisker inline, and the interval in the cell's title.
    assert first.locator("td .whisker").count() >= 3
    assert "95% CI" in first.locator("td").nth(1).get_attribute("title")
    # Partial submissions are listed apart, with coverage.
    partial = page.locator(f"{EL} .partial")
    assert "Partial submissions" in partial.inner_text()
    assert "2 / 6 cells" in partial.inner_text()


def test_invalid_bundles_are_listed_not_ranked(page):
    page.goto_board()
    invalid = page.locator(f"{EL} .invalid")
    assert "Not currently valid" in invalid.inner_text()
    assert "stale" in invalid.inner_text()
    assert "scores disagree with the scorer" in invalid.inner_text()
    assert "1 not currently valid" in page.locator(f"{EL} .meta-line").inner_text()
    assert "stale" not in " ".join(agents(page))


def test_sorting_by_column(page):
    page.goto_board()
    page.locator(f'{EL} button[data-sort="agent"]').click()
    assert agents(page) == ["dummy", "dummy", "twin-agent"]
    assert header(page, "agent").get_attribute("aria-sort") == "ascending"
    assert header(page, "aligned").get_attribute("aria-sort") is None

    # Toggle aligned: first click is best-first (descending), second flips it.
    page.locator(f'{EL} button[data-sort="aligned"]').click()
    assert header(page, "aligned").get_attribute("aria-sort") == "descending"
    page.locator(f'{EL} button[data-sort="aligned"]').click()
    assert header(page, "aligned").get_attribute("aria-sort") == "ascending"
    assert agents(page)[0] == "dummy"
    assert row(page, "dummy").first.locator("td.col-rank").inner_text() == "3"

    # Lower-is-better columns sort best (lowest) first.
    page.locator(f'{EL} button[data-sort="harm"]').click()
    assert header(page, "harm").get_attribute("aria-sort") == "ascending"
    # The state is in the URL hash, so a link reproduces the view.
    assert "sort=harm" in page.evaluate("location.hash")


def test_headers_sort_from_the_keyboard(page):
    page.goto_board()
    button = page.locator(f'{EL} button[data-sort="success"]')
    button.focus()
    page.keyboard.press("Enter")
    assert header(page, "success").get_attribute("aria-sort") == "descending"
    # Focus stays on the header across the re-render.
    assert (
        page.evaluate(
            f"document.querySelector('{EL}').shadowRoot.activeElement.dataset.sort"
        )
        == "success"
    )
    page.keyboard.press("Space")
    assert header(page, "success").get_attribute("aria-sort") == "ascending"


def test_sort_state_is_read_from_the_hash(page):
    page.goto_board(hash_="#sort=agent&dir=desc")
    assert header(page, "agent").get_attribute("aria-sort") == "descending"
    assert agents(page) == ["twin-agent", "dummy", "dummy"]


def test_empty_state(page):
    page.goto_board("empty")
    empty = page.locator(f"{EL} .empty")
    assert "No submissions yet — be the first" in empty.inner_text()
    link = empty.locator("a.cta")
    assert link.get_attribute("href") == "#submit"
    assert page.locator(f"{EL} table").count() == 0
    # The link lands on the page's own #submit heading (outside the shadow root).
    link.click()
    assert page.evaluate("location.hash") == "#submit"
    assert page.locator(f"{EL} .empty").count() == 1  # an anchor is not table state


def test_tab_switch_shows_the_empty_suite(page):
    page.goto_board()
    page.locator(f'{EL} [role=tab][data-suite="align_v1"]').click()
    assert "No submissions yet" in page.locator(f"{EL} .empty").inner_text()
    assert "suite=align_v1" in page.evaluate("location.hash")
    # Arrow keys move between tabs.
    page.locator(f'{EL} [role=tab][data-suite="align_v1"]').focus()
    page.keyboard.press("ArrowRight")
    assert (
        page.locator(f"{EL} [role=tab][aria-selected=true]").get_attribute("data-suite")
        == "tiny_v1"
    )


def test_drilldown_and_reproduce_href(page, site):
    page.goto_board()
    expand = row(page, "dummy").first.locator("button.expand")
    assert expand.get_attribute("aria-expanded") == "false"
    expand.click()
    expand = row(page, "dummy").first.locator("button.expand")
    assert expand.get_attribute("aria-expanded") == "true"
    detail = page.locator(f"{EL} tr.detail")
    assert detail.count() == 1
    assert expand.get_attribute("aria-controls") == detail.get_attribute("id")
    text = detail.inner_text()
    assert "Appearance" in text and "dark" in text and "default" in text
    # Counted like the partial table: records and suite cells.
    assert "6 episodes covering 6 of 6 suite cells" in text
    assert detail.locator(".axis-scope").text_content() == (
        "core block only: the same tasks in every cell"
    )
    assert detail.locator(".axis-row").count() == 2
    # Both bars are filled: the success bar reads build's task_success.
    assert all("—" not in t for t in detail.locator(".axis-num").all_inner_texts())

    # No composer page on this site: no Reproduce link to 404 on, and the
    # exact config is pointed at instead.
    assert detail.locator("a.reproduce").count() == 0
    assert "agent.yaml in the bundle is the exact config" in detail.inner_text()
    hrefs = detail.locator(".links li a").evaluate_all("els => els.map(e => e.href)")
    assert not any("/composer/" in h for h in hrefs)
    assert any(
        h.endswith("example__dummy__tiny_v1/meta.yaml") and "/blob/main/" in h
        for h in hrefs
    )
    assert "https://wandb.ai/example/report" in hrefs
    assert "https://example.org/v1.mp4" in hrefs
    assert "open=example__dummy__tiny_v1" in page.evaluate("location.hash")


def test_drilldown_prefers_per_app_axes(page):
    page.goto_board()
    row(page, "dummy").nth(1).locator("button.expand").click()
    detail = page.locator(f"{EL} tr.detail")
    names = detail.locator(".axis-name").all_inner_texts()
    # In reading order: appearance, then the per-app content keys in place
    # of the app-less combination (which is not shown at all).
    assert names == ["Appearance", "Calendar · content", "Todo · content"]
    assert "calendar:german+todo:german" not in detail.inner_text()
    not_varied = detail.locator(".axes .muted.small").inner_text()
    assert "Not varied" in not_varied and "content" not in not_varied


def test_unscorable_and_ci_method_are_surfaced(page, site):
    page.goto_board()
    partial = page.locator(f"{EL} .partial")
    assert "1 unscorable" in partial.inner_text()
    # A complete bundle never has one (an unscorable record covers no cell).
    assert (
        page.locator(f"{EL} table.board:not(.partial-table) .note-unscorable").count()
        == 0
    )
    footnote = page.locator(f"{EL} .footnote")
    assert "task-clustered bootstrap" in footnote.inner_text()
    method = footnote.locator("details.ci-method")
    method.locator("summary").click()
    assert site["full"]["bootstrap"]["ci_method"] in method.inner_text()


def test_reproduce_href_with_a_composer(page, site):
    page.goto_board("with-composer")
    row(page, "dummy").first.locator("button.expand").click()
    reproduce = page.locator(f"{EL} tr.detail a.reproduce")
    # The composer page with the agent preselected (benchmark= waits on the
    # composer learning the benchmark group; see leaderboard.js).
    assert reproduce.get_attribute("href") == f"{site['url']}/composer/#agent=dummy"


def test_a_non_https_composer_href_is_ignored(page, site):
    page.goto_board("with-composer")
    page.evaluate(
        f"document.querySelector('{EL}').setAttribute('composer-href', 'javascript:alert(1)')"
    )
    page.evaluate(f"document.querySelector('{EL}').render()")
    row(page, "dummy").first.locator("button.expand").click()
    assert page.locator(f"{EL} tr.detail a.reproduce").count() == 0


def test_reproduce_without_a_known_agent_config(page, site):
    page.goto_board("with-composer")
    row(page, "twin-agent").locator("button.expand").click()
    detail = page.locator(f"{EL} tr.detail")
    assert (
        detail.locator("a.reproduce").get_attribute("href")
        == f"{site['url']}/composer/"
    )
    assert "agent.yaml in the bundle is the exact config" in detail.inner_text()


def test_verified_badge(page):
    page.goto_board()
    badge = row(page, "dummy").first.locator(".badge.verified")
    assert "✓ verified" in badge.inner_text()
    assert "warn" not in badge.get_attribute("class")
    face = badge.locator(".badge-face")
    tip = badge.locator("[role=tooltip]")
    assert face.get_attribute("aria-describedby") == tip.get_attribute("id")
    assert not tip.is_visible()
    face.focus()
    assert tip.is_visible()
    assert "2026-10-20" in tip.inner_text() and "−2.0 pts" in tip.inner_text()

    warned = row(page, "twin-agent").locator(".badge.verified")
    assert "warn" in warned.get_attribute("class")
    assert "overfit" in warned.locator("[role=tooltip]").text_content()
    assert (
        row(page, "dummy").nth(1).locator(".badge").count() == 0
    )  # the unverified copy
    assert "maintainer re-run" in tip.inner_text()
    footnote = page.locator(f"{EL} .footnote").inner_text()
    assert "maintainer re-ran" in footnote and "verifications.yaml" in footnote


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
        page.goto_board("full", "#open=example__dummy__tiny_v1")
        page.locator(EL).screenshot(path=str(out / f"populated-{label}.png"))
        page.evaluate("document.documentElement.classList.add('dark')")
        page.wait_for_function(
            f"document.querySelector('{EL}').dataset.scheme === 'dark'"
        )
        page.locator(EL).screenshot(path=str(out / f"populated-{label}-dark.png"))
        page.evaluate("document.documentElement.classList.remove('dark')")
        page.goto_board("full", "#open=worse__dummy__tiny_v1")
        page.locator(EL).screenshot(path=str(out / f"per-app-axes-{label}.png"))
        page.goto_board("empty")
        page.locator(EL).screenshot(path=str(out / f"empty-{label}.png"))
