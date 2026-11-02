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


def test_the_page_is_one_self_contained_file(logs, tmp_path, monkeypatch, capsys):
    out = tmp_path / "page.html"
    assert P.main([str(logs.parent), "--suite", str(TINY_SUITE), "--out", str(out),
                   "--n-boot", "0"]) == 0  # fmt: skip
    html = out.read_text()
    assert "<openapps-leaderboard" in html
    # Nothing to fetch: the script, stylesheet and data are data: URLs.
    assert 'src="data:application/json;base64,' in html
    assert 'stylesheet="data:text/css;base64,' in html
    assert 'src="data:text/javascript;base64,' in html
    assert "http://" not in html and "https://" not in html.split("base64,")[0]
    printed = capsys.readouterr().out
    assert "dummy" in printed and "scp " in printed


def test_nothing_to_show_is_an_error(tmp_path):
    (tmp_path / "empty").mkdir()
    assert P.main([str(tmp_path / "empty"), "--out", str(tmp_path / "x.html")]) == 1
    assert not (tmp_path / "x.html").exists()


def test_open_preview_never_raises_and_opens_only_with_a_display(logs, monkeypatch):
    opened = []
    monkeypatch.setattr(P.webbrowser, "open", lambda url: opened.append(url) or True)
    monkeypatch.setattr(P, "_has_display", lambda: False)
    out = P.open_preview([logs.parent], out=logs.parent / "lb.html")
    assert out and out.is_file() and opened == []

    monkeypatch.setattr(P, "_has_display", lambda: True)
    P.open_preview([logs.parent], out=logs.parent / "lb.html")
    assert opened == [(logs.parent / "lb.html").resolve().as_uri()]

    monkeypatch.setattr(P, "preview", lambda *a, **k: 1 / 0)
    assert P.open_preview([logs.parent]) is None


@pytest.mark.slow
def test_the_page_renders_from_disk(logs, tmp_path):
    """file://, no server: the shipped element reads the inlined data."""
    sync_api = pytest.importorskip("playwright.sync_api")
    out = P.write_html(_preview([logs.parent]).doc, tmp_path / "page.html")
    errors = []
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(out.resolve().as_uri())
        page.wait_for_selector("openapps-leaderboard >> table.board")
        assert (
            "local preview"
            in page.locator("openapps-leaderboard >> .eyebrow").inner_text().lower()
        )
        assert (
            page.locator("openapps-leaderboard >> .agent-name").inner_text() == "dummy"
        )
        assert (
            "6/6 cells"
            in page.locator("openapps-leaderboard >> .agent-sub").inner_text()
        )
        page.click("openapps-leaderboard >> button.expand")
        links = page.locator("openapps-leaderboard >> .links").inner_text()
        assert str(logs) in links and "not validated" in links
        browser.close()
    assert errors == []
