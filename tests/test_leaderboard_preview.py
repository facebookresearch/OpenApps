"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

``open_apps.leaderboard.preview``: a local leaderboard straight from logs
dirs. Over the tiny suite's fixture sweep it must show the numbers ``build``
shows for the packed bundle of the same episodes, one row per model, with
re-runs counted once; and the page it writes must open from disk.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from open_apps.leaderboard import preview as P
from open_apps.leaderboard.build import COLUMNS, ranked_row
from open_apps.leaderboard.validate import validate_bundle
from tests._leaderboard_helpers import (
    EXAMPLE_BUNDLE,
    JOBS,
    SUITE_NAME,
    TINY_SUITE,
    write_logs_tree,
)

WHEN = "2026-10-16T00:00:00Z"


def _preview(paths, **kw):
    return P.preview(paths, n_boot=50, when=WHEN, suite=TINY_SUITE, **kw)


def _rows(result, suite=SUITE_NAME):
    return result.doc["suites"][suite]["rows"]


def _relabel(root: Path, agent: str) -> Path:
    """Every episode.json under ``root`` as if ``agent`` had run it."""
    for path in root.rglob("episode.json"):
        record = json.loads(path.read_text())
        record["identity"]["agent_name"] = agent
        path.write_text(json.dumps(record))
    return root


@pytest.fixture
def logs(tmp_path):
    return write_logs_tree(tmp_path / "log_outputs" / "2026-10-16_12-00-00-me")


def test_one_row_per_model_with_the_bundles_numbers(logs):
    """Same episodes, same scorer, same aggregation as the published table
    (rates; CIs are seeded by the row's name, which differs)."""
    result = _preview([logs.parent])
    (row,) = _rows(result)
    assert row["agent"] == "dummy" and row["rank"] == 1
    assert row["n_episodes"] == row["covered"] == row["n_cells"] == len(JOBS)
    assert row["logs"] == [str(logs)]
    assert row["n_tasks"] == len({task for _, _, task, _ in JOBS}) > 1
    bundle = ranked_row(validate_bundle(EXAMPLE_BUNDLE, TINY_SUITE), n_boot=0)
    for column, *_ in COLUMNS:
        assert row[column]["rate"] == pytest.approx(bundle[column]["rate"]), column
        assert row[column]["n"] == bundle[column]["n"], column
    assert row["worst_variation"] == bundle["worst_variation"]
    assert row["by_axis"] == bundle["by_axis"]


def test_a_rerun_sweep_counts_each_cell_once(logs):
    rerun = write_logs_tree(logs.parent / "2026-10-17_09-00-00-me", rebuilt_job=None)
    result = _preview([logs.parent])
    (row,) = _rows(result)
    assert row["n_episodes"] == len(JOBS)
    assert row["duplicates_dropped"] == result.duplicates == len(JOBS)
    assert row["logs"] == sorted([str(logs), str(rerun)])


def test_models_are_ranked_side_by_side_and_filterable(logs):
    _relabel(write_logs_tree(logs.parent / "other", rebuilt_job=None), "other-model")
    rows = _rows(_preview([logs.parent]))
    assert sorted(r["agent"] for r in rows) == ["dummy", "other-model"]
    assert [r["rank"] for r in rows] == [1, 1]  # identical episodes tie

    (only,) = _rows(_preview([logs.parent], agents=["other-*"]))
    assert only["agent"] == "other-model"

    (todo,) = _rows(_preview([logs.parent], agents=["dummy"], tasks=["add_*"]))
    n_add = sum(1 for _, _, task, _ in JOBS if task.startswith("add_"))
    assert 0 < n_add < len(JOBS) and todo["n_episodes"] == n_add


def test_an_unknown_suite_is_its_own_tab_without_coverage(logs):
    result = P.preview(
        [logs], n_boot=0, when=WHEN
    )  # tiny_v1 is not in config/benchmark
    (row,) = _rows(result)
    assert row["n_cells"] == row["n_episodes"] == len(JOBS)
    assert row["breakdown_block"] is None


def test_an_aggregate_table_is_read_from_its_rows(logs, tmp_path):
    from open_apps.alignment.aggregate import main as aggregate_main

    table = tmp_path / "alignment_aggregate.json"
    assert aggregate_main([str(logs), "--out", str(table), "--n-boot", "0"]) == 0
    result = P.preview([table], n_boot=0, when=WHEN)
    (row,) = result.doc["suites"][P.LOCAL_SUITE]["rows"]
    n_json = sum(1 for _ in logs.rglob("episode.json"))  # the rebuilt job has none
    assert row["agent"] == "dummy" and row["n_episodes"] == n_json


def test_out_writes_one_self_contained_file(logs, tmp_path, capsys):
    out = tmp_path / "page.html"
    assert P.main([str(logs.parent), "--suite", str(TINY_SUITE), "--out", str(out),
                   "--n-boot", "0", "--background-seed", "1"]) == 0  # fmt: skip
    html = out.read_text()
    assert "<openapps-leaderboard" in html
    # Nothing to fetch: script, stylesheet, data and texture are data: URLs.
    assert 'src="data:application/json;base64,' in html
    assert 'stylesheet="data:text/css;base64,' in html
    assert 'src="data:text/javascript;base64,' in html
    assert html.count('url("data:image/png;base64,') == 2  # light and dark
    assert "http://" not in html and "https://" not in html.split("base64,")[0]
    # A saved file has no server behind it, so no save links.
    assert "Save as PDF" not in html
    assert "dummy" in capsys.readouterr().out


def test_nothing_to_show_is_an_error(tmp_path):
    (tmp_path / "empty").mkdir()
    assert P.main([str(tmp_path / "empty"), "--out", str(tmp_path / "x.html")]) == 1
    assert not (tmp_path / "x.html").exists()


def test_the_halftone_tile_is_seamless_faint_and_new_each_run():
    from open_apps.leaderboard import halftone as H

    rng = np.random.default_rng(0)
    field = H.periodic_noise(64, rng)
    # Wrapping: the step across the seam is no larger than any step inside.
    inner = np.abs(np.diff(field, axis=1)).max()
    assert np.abs(field[:, 0] - field[:, -1]).max() <= inner + 1e-9
    for scheme, (paper, _) in H.SCHEMES.items():
        img = np.asarray(H.tile(scheme, seed=0), dtype=int)
        assert img.shape == (H.TILE, H.TILE, 3)
        assert np.abs(img - np.array(paper)).max() <= 24, scheme  # subtle
        assert (img != np.array(paper)).any(axis=2).mean() > 0.01, scheme  # but there
    assert H.tile_png("light", 3) == H.tile_png("light", 3)
    assert H.tile_png("light") != H.tile_png("light")


def _get(url):
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_the_server_serves_the_page_and_its_two_downloads(logs, monkeypatch):
    import threading

    page = P.Page(_preview([logs.parent]).doc, background_seed=0)
    monkeypatch.setattr(page, "pdf", lambda: b"%PDF-1.4 stub")
    server = P.make_server(page, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, _, body = _get(base + "/")
        assert status == 200 and b"Save as HTML" in body and b"Save as PDF" in body
        status, headers, body = _get(base + "/leaderboard.html")
        assert status == 200 and b"Save as PDF" not in body
        assert (
            'filename="openapps-leaderboard-2026-10-16.html"'
            in headers["Content-Disposition"]
        )
        status, headers, body = _get(base + "/leaderboard.pdf")
        assert status == 200 and body.startswith(b"%PDF")
        assert headers["Content-Type"] == "application/pdf"
        assert _get(base + "/../.env")[0] == 404
        monkeypatch.setattr(page, "pdf", lambda: 1 / 0)
        assert _get(base + "/leaderboard.pdf")[0] == 503  # the page falls back to print
    finally:
        server.shutdown()
        server.server_close()


def test_a_busy_port_moves_to_the_next_free_one(logs):
    page = P.Page(_preview([logs.parent]).doc, background_seed=0)
    first = P.make_server(page, port=0)
    try:
        second = P.make_server(page, port=first.server_port)
        assert second.server_port != first.server_port
        second.server_close()
    finally:
        first.server_close()


def test_serve_opens_a_browser_only_with_a_display(logs, monkeypatch, capsys):
    page = P.Page(_preview([logs.parent]).doc, background_seed=0)
    opened = []
    monkeypatch.setattr(P.webbrowser, "open", lambda url: opened.append(url) or True)
    # serve_forever returns at once: the test only checks what happens around it.
    monkeypatch.setattr(
        "socketserver.BaseServer.serve_forever", lambda self, *a, **k: None
    )
    monkeypatch.setenv("OPENAPPS_SSH_HOST", "my-cluster")
    monkeypatch.setattr(P, "_has_display", lambda: False)
    P.serve(page, port=0)
    out = capsys.readouterr().out
    assert opened == [] and "ssh -N -L" in out and "my-cluster" in out

    monkeypatch.setattr(P, "_has_display", lambda: True)
    P.serve(page, port=0)
    assert len(opened) == 1 and opened[0].startswith("http://localhost:")


def test_launch_never_raises_and_only_serves_from_a_terminal(logs, monkeypatch, capsys):
    served = []
    monkeypatch.setattr(P, "serve", lambda page, **k: served.append(page))
    monkeypatch.setattr(P.sys.stdout, "isatty", lambda: False, raising=False)
    P.launch([logs.parent])
    assert served == [] and "open_apps.leaderboard.preview" in capsys.readouterr().out

    monkeypatch.setattr(P.sys.stdout, "isatty", lambda: True, raising=False)
    P.launch([logs.parent])
    assert len(served) == 1

    monkeypatch.setattr(P, "preview", lambda *a, **k: 1 / 0)
    P.launch([logs.parent])  # does not raise
    assert "Retry with" in capsys.readouterr().err


@pytest.mark.slow
def test_the_page_renders_from_disk(logs, tmp_path):
    """file://, no server: the shipped element reads the inlined data."""
    sync_api = pytest.importorskip("playwright.sync_api")
    out = P.Page(_preview([logs.parent]).doc).write(tmp_path / "page.html")
    errors = []
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(out.resolve().as_uri())
        page.wait_for_selector("openapps-leaderboard >> table.board")
        eyebrow = page.locator("openapps-leaderboard >> .eyebrow").inner_text()
        assert "local preview" in eyebrow.lower()
        assert (
            page.locator("openapps-leaderboard >> .agent-name").inner_text() == "dummy"
        )
        sub = page.locator("openapps-leaderboard >> .agent-sub").inner_text()
        assert "6/6 cells" in sub
        tasks = page.locator("openapps-leaderboard >> td.col-tasks").inner_text()
        assert tasks == str(len({task for _, _, task, _ in JOBS}))
        # The preview's palette, not the docs site's yellow.
        bg = page.locator("openapps-leaderboard >> .olb").evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        assert bg == "rgba(255, 255, 255, 0.8)"
        page.click("openapps-leaderboard >> button.expand")
        links = page.locator("openapps-leaderboard >> .links").inner_text()
        assert str(logs) in links and "not validated" in links
        browser.close()
    assert errors == []


@pytest.mark.slow
def test_the_pdf_is_rendered(logs, tmp_path):
    pytest.importorskip("playwright.sync_api")
    out = P.Page(_preview([logs.parent]).doc, 0).write(tmp_path / "board.pdf")
    assert out.read_bytes().startswith(b"%PDF") and out.stat().st_size > 10_000
