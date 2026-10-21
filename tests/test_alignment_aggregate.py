"""Tests for ``open_apps.alignment.aggregate`` (per-agent rates, CIs, worst case)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from open_apps.alignment.aggregate import (
    aggregate,
    axes_from_variation,
    format_table,
    row_from_record,
)

FIXTURES = Path(__file__).parent / "fixtures" / "alignment"


def _row(agent, cell, success, harm=False, honest=None, steps=5, axes=None):
    return {
        "agent": agent,
        "cell": cell,
        "axes": axes or {},
        "success": success,
        "harm": harm,
        "honest": honest,
        "steps": steps,
    }


def _rows():
    rows = []
    # Agent A: perfect on the default cell, 1/4 under the dark theme.
    rows += [_row("A", "default", True, axes={"theme": "default"}) for _ in range(4)]
    rows += [
        _row("A", "apps/theme=dark", s, axes={"theme": "dark"})
        for s in (True, False, False, False)
    ]
    # Harm only shows up in one cell.
    rows[-1]["harm"] = True
    rows += [_row("B", "default", s, honest=s) for s in (True, False)]
    return rows


def test_rates_and_counts():
    table = aggregate(_rows(), n_boot=200, seed=0)
    a = table["agents"]["A"]
    assert a["n"] == 8
    assert a["metrics"]["success"]["rate"] == pytest.approx(5 / 8)
    assert a["metrics"]["harm"]["rate"] == pytest.approx(1 / 8)
    assert a["metrics"]["steps"]["rate"] == pytest.approx(5.0)


def test_ci_is_deterministic_for_a_seed_and_brackets_the_rate():
    rows = _rows()
    t1 = aggregate(rows, n_boot=500, seed=123)
    t2 = aggregate(rows, n_boot=500, seed=123)
    assert t1 == t2
    m = t1["agents"]["A"]["metrics"]["success"]
    assert m["ci_low"] <= m["rate"] <= m["ci_high"]
    assert 0.0 <= m["ci_low"] < m["ci_high"] <= 1.0
    # A different seed is allowed to move the CI (and almost surely does).
    t3 = aggregate(rows, n_boot=500, seed=7)
    assert t3["agents"]["A"]["metrics"]["success"]["rate"] == m["rate"]


def test_worst_case_respects_metric_direction():
    a = aggregate(_rows(), n_boot=50, seed=0)["agents"]["A"]["metrics"]
    # Higher-is-better: worst = lowest cell.
    assert a["success"]["worst"] == pytest.approx(0.25)
    assert a["success"]["worst_cell"] == "apps/theme=dark"
    # Lower-is-better: worst = highest cell.
    assert a["harm"]["worst"] == pytest.approx(0.25)
    assert a["harm"]["worst_cell"] == "apps/theme=dark"
    # Variance across the two cell rates (1.0 and 0.25), population variance.
    assert a["success"]["cell_variance"] == pytest.approx(
        ((1 - 0.625) ** 2 + (0.25 - 0.625) ** 2) / 2
    )


def test_none_is_excluded_from_the_denominator():
    table = aggregate(_rows(), n_boot=50, seed=0)
    assert table["agents"]["A"]["metrics"]["honest"]["n"] == 0
    assert table["agents"]["A"]["metrics"]["honest"]["rate"] is None
    b = table["agents"]["B"]["metrics"]["honest"]
    assert (b["n"], b["rate"]) == (2, 0.5)


def test_per_axis_breakdown():
    a = aggregate(_rows(), n_boot=50, seed=0)["agents"]["A"]
    by_theme = a["by_axis"]["theme"]
    assert by_theme["dark"]["success"]["rate"] == pytest.approx(0.25)
    assert by_theme["default"]["success"]["n"] == 4


def test_axes_from_variation():
    axes = axes_from_variation(
        [
            "apps/theme=dark",
            "apps/todo/layout=kanban_board",
            "apps/calendar/content=german",
            "device=phone",
            "apps/pop_ups=adversarial_descriptions",
        ],
        goal_style="casual",
        event_categories=["financial"],
    )
    assert axes == {
        "theme": "dark",
        "todo.layout": "kanban_board",
        "calendar.content": "german",
        # The app-less keys, kept for consumers that read them.
        "layout": "todo:kanban_board",
        "content": "calendar:german",
        "device": "phone",
        "pop_ups": "adversarial_descriptions",
        "phrasing": "casual",
        "event": "financial",
    }


def test_per_app_axes_never_collide():
    """Two apps overriding the same axis are two cells, not one overwriting
    the other (the German calendar must not vanish behind a German todo)."""
    axes = axes_from_variation(
        ["apps/calendar/content=german", "apps/todo/content=mandarin", "apps/events=x"]
    )
    assert axes["calendar.content"] == "german" and axes["todo.content"] == "mandarin"
    # The legacy key carries the combination instead of the last one written.
    assert axes["content"] == "calendar:german+todo:mandarin"
    assert axes["events"] == "x"


def test_per_app_axis_breakdown_keeps_every_cell():
    rows = [
        _row("A", "c1", True, axes=axes_from_variation(["apps/calendar/content=german"])),
        _row(
            "A",
            "c2",
            False,
            axes=axes_from_variation(["apps/calendar/content=german", "apps/todo/content=german"]),
        ),
    ]
    by_axis = aggregate(rows, n_boot=10)["agents"]["A"]["by_axis"]
    assert by_axis["calendar.content"]["german"]["success"]["n"] == 2
    assert by_axis["todo.content"]["german"]["success"]["n"] == 1


def test_row_from_record_and_cli(tmp_path):
    from open_apps.alignment.record import EpisodeRecord, save_record

    for i, name in enumerate(
        ["aligned_conjunction", "premature_stop_after_popup", "auto_reply_not_harm"]
    ):
        raw = json.loads((FIXTURES / f"{name}.json").read_text())["record"]
        record = EpisodeRecord.from_dict(raw)
        save_record(record, tmp_path / f"run{i}")

    row = row_from_record(
        EpisodeRecord.from_dict(
            json.loads((FIXTURES / "aligned_conjunction.json").read_text())["record"]
        )
    )
    assert row["agent"] == "fixture-agent"
    assert row["cell"] == "apps/theme=dark|apps/todo/layout=kanban_board"
    assert row["axes"]["layout"] == "todo:kanban_board"
    assert row["aligned"] is True

    out = tmp_path / "table.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "open_apps.alignment.aggregate",
            str(tmp_path),
            "--out",
            str(out),
            "--n-boot",
            "100",
            "--seed",
            "0",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "fixture-agent" in proc.stdout
    table = json.loads(out.read_text())
    # Two of the fixtures are on_agent_stop episodes, one on_success: the
    # modes are reported apart, never pooled.
    stop = table["agents"]["fixture-agent@on_agent_stop"]
    assert stop["n"] == 2
    assert stop["metrics"]["aligned"]["rate"] == pytest.approx(1 / 2)
    assert table["agents"]["fixture-agent@on_success"]["n"] == 1


# --- termination modes never pool -------------------------------------------------


def test_rows_carry_their_termination_mode():
    from open_apps.alignment.record import EpisodeRecord

    raw = json.loads((FIXTURES / "aligned_conjunction.json").read_text())["record"]
    row = row_from_record(EpisodeRecord.from_dict(raw))
    assert row["task_termination"] == "on_agent_stop"


def test_single_mode_agents_keep_their_key_and_say_which_mode():
    rows = [dict(r, task_termination="on_agent_stop") for r in _rows()]
    table = aggregate(rows, n_boot=20, seed=0)
    assert sorted(table["agents"]) == ["A", "B"]
    assert table["agents"]["A"]["agent"] == "A"
    assert table["agents"]["A"]["task_termination"] == "on_agent_stop"


def test_rows_without_a_mode_are_on_success():
    table = aggregate(_rows(), n_boot=20, seed=0)
    assert table["agents"]["A"]["task_termination"] == "on_success"


def test_an_agent_run_in_both_modes_is_split_not_pooled():
    rows = [_row("A", "default", True, honest=None) for _ in range(3)]
    rows += [dict(_row("A", "default", False, honest=False), task_termination="on_agent_stop")]
    table = aggregate(rows, n_boot=20, seed=0)
    assert sorted(table["agents"]) == ["A@on_agent_stop", "A@on_success"]
    stop = table["agents"]["A@on_agent_stop"]
    assert (stop["agent"], stop["task_termination"], stop["n"]) == ("A", "on_agent_stop", 1)
    assert stop["metrics"]["success"]["rate"] == 0.0
    assert table["agents"]["A@on_success"]["metrics"]["success"]["rate"] == 1.0


def test_unscorable_episodes_are_left_out_and_counted():
    from open_apps.alignment.record import EpisodeRecord

    raw = json.loads((FIXTURES / "out_of_scope_delete_is_harm.json").read_text())["record"]
    good = EpisodeRecord.from_dict(raw)
    bad = EpisodeRecord.from_dict(raw)
    bad.unscorable = ["todo"]
    rows = [row_from_record(good), row_from_record(bad)]
    assert rows[1]["unscorable"] is True and rows[0]["unscorable"] is False
    table = aggregate(rows, n_boot=10)
    (entry,) = table["agents"].values()
    assert entry["n"] == 1 and entry["excluded_unscorable"] == 1
    assert entry["metrics"]["harm"]["n"] == 1
    assert table["excluded_unscorable"] == 1


# --- confidence intervals -------------------------------------------------------------


def _task_rows(agent, per_task: dict[str, list[bool]]):
    return [
        {"agent": agent, "task_name": task, "cell": "default", "success": s}
        for task, outcomes in per_task.items()
        for s in outcomes
    ]


def test_a_groups_ci_does_not_depend_on_other_rows_or_metrics():
    a = _task_rows("A", {"t1": [True, False, True], "t2": [False, False, True], "t3": [True] * 3})
    alone = aggregate(a, n_boot=300, seed=1)["agents"]["A"]["metrics"]["success"]
    # "0" sorts before "A", and harm is summarised before success here: with
    # one shared generator both would shift A's draws.
    b = _task_rows("0", {"t1": [False, True], "t9": [True]})
    with_b = aggregate(b + a, n_boot=300, seed=1)["agents"]["A"]["metrics"]["success"]
    assert (alone["ci_low"], alone["ci_high"]) == (with_b["ci_low"], with_b["ci_high"])
    more = [dict(r, harm=r["task_name"] == "t2") for r in a]
    with_metric = aggregate(
        more, metrics={"harm": False, "success": True}, n_boot=300, seed=1
    )["agents"]["A"]["metrics"]["success"]
    assert (alone["ci_low"], alone["ci_high"]) == (with_metric["ci_low"], with_metric["ci_high"])


def test_ci_resamples_tasks_not_episodes():
    """Fifty episodes each of a task the agent always solves and one it never
    does: the uncertainty is about which tasks are in the suite, and
    resampling episodes independently would pretend it is tiny."""
    rows = _task_rows("A", {"easy": [True] * 50, "hard": [False] * 50})
    m = aggregate(rows, n_boot=500, seed=0)["agents"]["A"]["metrics"]["success"]
    assert m["rate"] == 0.5
    assert (m["ci_low"], m["ci_high"]) == (0.0, 1.0)
    assert m["ci_method"] == "bootstrap-task-clustered"


@pytest.mark.parametrize("outcome,lo,hi", [(True, 0.7225, 1.0), (False, 0.0, 0.2775)])
def test_a_degenerate_binary_metric_gets_a_wilson_interval(outcome, lo, hi):
    rows = _task_rows("A", {f"t{i}": [outcome] for i in range(10)})
    m = aggregate(rows, n_boot=200, seed=0)["agents"]["A"]["metrics"]["success"]
    assert m["ci_method"] == "wilson"
    assert m["ci_low"] == pytest.approx(lo, abs=1e-3)
    assert m["ci_high"] == pytest.approx(hi, abs=1e-3)


def test_a_constant_count_metric_is_not_a_proportion():
    rows = [dict(r, steps=4) for r in _task_rows("A", {"t1": [True, False], "t2": [True]})]
    m = aggregate(rows, n_boot=50, seed=0)["agents"]["A"]["metrics"]["steps"]
    assert (m["ci_low"], m["ci_high"]) == (4.0, 4.0)


def test_the_ci_method_is_documented_in_the_output():
    table = aggregate(_rows(), n_boot=20, seed=0)
    assert "task-clustered" in table["ci_method"] and "Wilson" in table["ci_method"]
    assert table["agents"]["A"]["metrics"]["success"]["ci_method"] in (
        "bootstrap-task-clustered",
        "bootstrap-episodes",
        "wilson",
        "point",
    )


# --- provenance ------------------------------------------------------------------------


def test_rows_and_table_carry_provenance():
    from open_apps.alignment.record import EpisodeRecord

    raw = json.loads((FIXTURES / "aligned_conjunction.json").read_text())["record"]
    ident = raw["identity"]
    rows = []
    for sha, dirty in (("a" * 40, False), ("a" * 40, True), ("b" * 40, False)):
        raw["identity"] = ident | {
            "git_commit": sha,
            "git_dirty": dirty,
            "command": "uv run launch_agent.py task_name=x",
        }
        rows.append(row_from_record(EpisodeRecord.from_dict(raw)))
    assert rows[0]["task_name"] == ident["task_name"]
    assert rows[0]["git_commit"] == "a" * 40
    assert rows[0]["command"] == "uv run launch_agent.py task_name=x"

    one = aggregate(rows[:2], n_boot=10)["agents"]["fixture-agent"]
    assert (one["git_commits"], one["dirty"]) == (["a" * 40], 1)
    assert "aaaaaaa*" in format_table({"metrics": [], "agents": {"x": one}})

    both = aggregate(rows, n_boot=10)["agents"]["fixture-agent"]
    assert both["git_commits"] == ["a" * 40, "b" * 40]
    assert "mixed(2)*" in format_table({"metrics": [], "agents": {"x": both}})
