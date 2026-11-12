"""The local results preview (open_apps.results_preview) over tiny synthetic runs.

Each fixture run is laid out the way the launcher writes one: a job dir with
the saved ``config.yaml`` and, inside it, BrowserGym's experiment dir with
``summary_info.json``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest
import yaml

from open_apps.results_preview import cli, page as P, reproduce, runs as R
from open_apps.results_preview.halftone import TILE, tile


def make_run(
    job_dir: Path,
    *,
    agent: str = "Model-A",
    task: str = "todo_add",
    reward: float = 1.0,
    err: str | None = None,
    steps: int = 5,
    overrides: list[str] | None = None,
    seed: int = 233,
    started: str = "2025-01-01_19-42-15",
    max_steps: int = 30,
    agent_extra: dict | None = None,
    extra: dict | None = None,
) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "project": "open_apps",
        # Big unrelated blocks, as in a real config, that must not matter.
        "apps": {"todo": {"title": "x", "items": [{"a": 1}, {"b": 2}]}},
        "agent": {"_target_": "x.AgentArgs", "model_name": f"org/{agent}",
                  "model_pretty_name": agent, "prompt": "line one\nline: two\n",
                  **(agent_extra or {})},
        "tasks": {task: {"goal": "do it"}},
        "browsergym_env_args": {"task_seed": 42, "max_steps": max_steps},
        "task_name": task,
        "seed": seed,
        "app_overrides": overrides or [],
        **(extra or {}),
    }  # fmt: skip
    (job_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    exp = job_dir / f"{started}_AgentArgs_on_{abs(hash((task, started))):x}_42"
    exp.mkdir()
    summary = {"n_steps": steps, "cum_reward": reward, "err_msg": err,
               "stack_trace": f"Traceback\n{err}" if err else None,
               "stats.cum_steps": steps + 1}  # fmt: skip
    (exp / "summary_info.json").write_text(json.dumps(summary))
    return exp


@pytest.fixture
def logs(tmp_path) -> Path:
    """Two models; A on three tasks (one errored run), B on one task, nested
    at different depths."""
    root = tmp_path / "logs"
    sweep = root / "2025-01-01_19-00-00-me" / "run-abc"
    make_run(sweep / "0", task="todo_add", reward=1.0, steps=4)
    make_run(sweep / "1", task="todo_toggle", reward=0.0, steps=10)
    make_run(sweep / "2", task="calendar_add", reward=1.0, steps=6,
             overrides=["apps/theme=dark"])  # fmt: skip
    make_run(sweep / "3", task="calendar_add", err="webapp did not start", reward=0)
    make_run(root / "deeper" / "a" / "b" / "single", agent="Model-B", task="todo_add",
             reward=0.0, steps=8)  # fmt: skip
    return root


def test_one_row_per_model_from_nested_runs(logs):
    results = R.collect([logs])
    assert [r.label for r in results.rows] == ["Model-A", "Model-B"]
    a, b = results.rows
    assert (a.rank, a.episodes, a.successes, a.errored, a.tasks) == (1, 3, 2, 1, 3)
    assert a.mean_steps == pytest.approx((4 + 10 + 6) / 3)
    assert (b.episodes, b.successes, b.tasks) == (1, 0, 1)
    assert {t.label for t in a.by_task} == {"todo_add", "todo_toggle", "calendar_add"}
    assert {v.label for v in a.by_variation} == {"default", "apps/theme=dark"}
    assert results.n_episodes == 4 and results.n_errored == 1


def test_success_rate_and_wilson_interval(logs):
    a = R.collect([logs]).rows[0]
    assert a.rate == pytest.approx(2 / 3)
    lo, hi = a.ci
    # Wilson 95% for 2/3: the textbook values.
    assert lo == pytest.approx(0.2077, abs=1e-3)
    assert hi == pytest.approx(0.9385, abs=1e-3)
    assert R.wilson(0, 0) is None
    assert R.wilson(0, 10)[0] == 0.0
    assert R.wilson(10, 10)[1] == pytest.approx(1.0)


def test_errored_runs_are_excluded_and_counted(tmp_path):
    root = tmp_path / "logs"
    make_run(root / "0", task="t1", reward=1.0)
    make_run(root / "1", task="t2", err="Timeout talking to vLLM", reward=0)
    row = R.collect([root]).rows[0]
    assert (row.episodes, row.successes, row.errored, row.rate) == (1, 1, 1, 1.0)
    assert row.tasks == 1  # t2 never ran to completion
    assert row.errors[0][0] == "t2" and "Timeout" in row.errors[0][2]
    # Only errored runs: listed, but without a rate, and ranked last.
    make_run(root / "2", agent="Broken", task="t1", err="boom", reward=0)
    rows = R.collect([root]).rows
    assert rows[-1].label == "Broken" and rows[-1].rate is None


def test_reruns_count_once(tmp_path):
    first, rerun = tmp_path / "sweep", tmp_path / "sweep-r2"
    # Same cell three ways: an errored re-run never beats a clean run ...
    make_run(first / "0", task="t", reward=0.0, started="2025-01-01_10-00-00")
    make_run(rerun / "0", task="t", err="crash", reward=0, started="2025-01-02_10-00-00")
    # ... a later clean run beats an earlier one ...
    make_run(first / "1", task="u", reward=0.0, started="2025-01-01_10-00-00")
    make_run(rerun / "1", task="u", reward=1.0, started="2025-01-02_10-00-00")
    # ... and a clean re-run beats an errored original.
    make_run(first / "2", task="v", err="crash", reward=0, started="2025-01-01_10-00-00")
    make_run(rerun / "2", task="v", reward=1.0, started="2024-12-31_10-00-00")
    # A different variation or seed is a different cell.
    make_run(first / "3", task="t", reward=1.0, overrides=["apps/theme=dark"])
    make_run(first / "4", task="t", reward=1.0, seed=7)
    results = R.collect([first, rerun])
    row = results.rows[0]
    assert results.duplicates == 3 and row.replaced == 3
    assert (row.episodes, row.successes, row.errored) == (5, 4, 0)
    picked = {(r.task, r.variation, r.seed): r for r in results.runs}
    assert picked[("t", "default", "233/42")].success is False
    assert picked[("u", "default", "233/42")].success is True
    assert picked[("v", "default", "233/42")].errored is False


def test_filters_by_agent_and_task_globs(logs):
    rows = R.collect([logs], agents=["Model-B"]).rows
    assert [r.label for r in rows] == ["Model-B"]
    rows = R.collect([logs], tasks=["todo_*"]).rows
    assert {t.label for r in rows for t in r.by_task} == {"todo_add", "todo_toggle"}
    rows = R.collect([logs], agents=["Model-*"], tasks=["calendar_*"]).rows
    assert [(r.label, r.episodes, r.errored) for r in rows] == [("Model-A", 1, 1)]
    assert R.collect([logs], agents=["nope"]).rows == []


def test_config_parsing_reads_only_the_identifying_keys(tmp_path):
    text = (
        "project: open_apps\napps:\n  todo:\n    task_name: not this one\n"
        "agent:\n  model_name: org/M\n  prompt: 'a: b'\n"
        "task_name: real\napp_overrides:\n- apps/theme=dark\n- apps/todo/content=long\n"
        "seed: 3\n"
    )
    path = tmp_path / "config.yaml"
    path.write_text(text)
    config = R.read_config(path)
    assert config["task_name"] == "real" and "apps" not in config
    assert R.agent_name(config) == "org/M"  # no pretty name: the raw model name
    assert R.variation_label(config["app_overrides"]) == (
        "apps/theme=dark, apps/todo/content=long"
    )


def test_runs_without_a_config_and_unfinished_runs_are_reported(tmp_path):
    exp = tmp_path / "orphan" / "2025-01-01_00-00-00_AgentArgs_on_x_42"
    exp.mkdir(parents=True)
    (exp / "summary_info.json").write_text(json.dumps({"cum_reward": 1.0}))
    running = tmp_path / "job" / "2025-01-01_19-42-15_AgentArgs_on_y_42"
    running.mkdir(parents=True)
    (running / "exp_args.pkl").write_bytes(b"")
    found = R.find_runs([tmp_path])
    assert found.runs == []
    assert [why for _, why in found.skipped] == ["no config.yaml beside it"]
    assert found.unfinished == [str(running.resolve())]


def test_out_writes_one_self_contained_html_file(logs, tmp_path, capsys):
    out = tmp_path / "page.html"
    assert cli.main([str(logs), "--out", str(out), "--background-seed", "1"]) == 0
    printed = capsys.readouterr().out
    assert "1. Model-A" in printed and "66.7%" in printed and f"wrote {out}" in printed
    html = out.read_text()
    assert html.startswith("<!doctype html>")
    assert "Model-A" in html and "Model-B" in html and "todo_<wbr>toggle" in html
    assert "2 episodes of 2 tasks" not in html and "3 episodes of 3 tasks" in html
    assert "webapp did not start" in html  # the errored run's message
    # Nothing external: every asset is inline.
    for needle in ("http://", "https://", "<link", "src=\""):
        assert needle not in html
    assert "data:image/png;base64," in html
    assert "Save as HTML" not in html  # the save links are only on the served page


def test_a_label_cannot_inject_markup(tmp_path):
    make_run(tmp_path / "0", agent="<script>alert(1)</script>")
    html = P.Page(R.collect([tmp_path]), background_seed=0).html()
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


def test_no_runs_says_where_to_run_it(tmp_path, capsys):
    assert cli.main([str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "No finished runs under" in err and "where the logs are" in err
    assert "rsync -am --exclude='wandb/'" in err and "summary_info.json" in err


def test_ssh_hint_uses_openapps_ssh_host(monkeypatch):
    monkeypatch.setenv("OPENAPPS_SSH_HOST", "my-cluster")
    hint = P.how_to_view(8766)
    assert "ssh -N -L 8766:localhost:8766 my-cluster" in hint
    assert "OPENAPPS_SSH_HOST" not in hint
    monkeypatch.delenv("OPENAPPS_SSH_HOST")
    hint = P.how_to_view(8766)
    assert P.socket.gethostname() in hint and "set OPENAPPS_SSH_HOST" in hint


def test_serve_opens_a_browser_only_with_a_display(logs, monkeypatch, capsys):
    page = P.Page(R.collect([logs]), background_seed=0)
    opened = []
    monkeypatch.setattr(P.webbrowser, "open", lambda url: opened.append(url) or True)

    class Server:
        server_port = 8799

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(P, "make_server", lambda *a, **k: Server())
    monkeypatch.setenv("OPENAPPS_SSH_HOST", "my-cluster")
    monkeypatch.setattr(P, "has_display", lambda: False)
    P.serve(page)
    assert not opened
    assert "ssh -N -L 8799:localhost:8799 my-cluster" in capsys.readouterr().out
    monkeypatch.setattr(P, "has_display", lambda: True)
    P.serve(page)
    assert opened == ["http://localhost:8799/"]
    assert "ssh -N" not in capsys.readouterr().out


def test_the_server_serves_the_page_and_its_two_downloads(logs, monkeypatch):
    page = P.Page(R.collect([logs]), background_seed=0)
    monkeypatch.setattr(page, "pdf", lambda: b"%PDF-1.4 stub")
    server = P.make_server(page, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        index = opener.open(base + "/").read().decode()
        assert "Save as HTML" in index and "Save as PDF" in index
        saved = opener.open(base + "/results.html")
        assert "attachment" in saved.headers["Content-Disposition"]
        assert "Save as HTML" not in saved.read().decode()
        assert opener.open(base + "/results.pdf").read() == b"%PDF-1.4 stub"
        with pytest.raises(urllib.error.HTTPError):
            opener.open(base + "/etc/passwd")
    finally:
        server.shutdown()
        server.server_close()


def test_launch_never_raises_and_only_serves_from_a_terminal(logs, monkeypatch, capsys):
    served = []
    monkeypatch.setattr(cli, "serve", lambda page, **k: served.append(page))
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False, raising=False)
    cli.launch([logs])
    assert not served
    assert f"uv run -m open_apps.results_preview {logs}" in capsys.readouterr().out
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True, raising=False)
    cli.launch([logs])
    assert len(served) == 1
    monkeypatch.setattr(cli, "collect", lambda *a, **k: 1 / 0)
    cli.launch([logs])  # does not raise
    assert "Results preview failed" in capsys.readouterr().err


def test_the_halftone_tile_is_seamless_faint_and_new_each_run():
    a = tile("light", seed=1)
    assert a.size == (TILE, TILE)
    assert tile("light", seed=1).tobytes() == a.tobytes()
    assert tile("light").tobytes() != tile("light").tobytes()
    lo, hi = min(a.getdata()), max(a.getdata())
    assert all(abs(x - y) < 40 for x, y in zip(lo, hi))  # faint


def test_runs_as_a_module(logs, tmp_path):
    out = tmp_path / "m.html"
    done = subprocess.run(
        [sys.executable, "-m", "open_apps.results_preview", str(logs), "--out", str(out)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert "Model-A" in done.stdout and out.is_file()


def test_the_rsync_filter_keeps_exactly_what_discovery_reads(logs, tmp_path):
    """The docs' rsync line copies config.yaml and summary_info.json only; a
    copy made with it must give the same table."""
    if not Path("/usr/bin/rsync").exists() and not Path("/bin/rsync").exists():
        pytest.skip("rsync not installed")
    # Bulky files a real run has, which the filter must leave behind.
    for exp in logs.rglob("*_AgentArgs_on_*"):
        (exp / "step_0.pkl.gz").write_bytes(b"x" * 100)
        (exp / "screenshot_step_0.png").write_bytes(b"x")
        wandb = exp.parent / "wandb" / "run-1" / "files"
        wandb.mkdir(parents=True)
        (wandb / "config.yaml").write_text("wandb: copy\n")
    copy = tmp_path / "runs"
    filters = cli.RSYNC.split(" <cluster>")[0].split()[1:]
    subprocess.run(
        ["rsync", *[f.replace("'", "") for f in filters], f"{logs}/", f"{copy}/"],
        check=True,
    )
    copied = [p for p in copy.rglob("*") if p.is_file()]
    assert {p.name for p in copied} == {"config.yaml", "summary_info.json"}
    assert not any("wandb" in p.parts for p in copied)
    summary = lambda root: [(r.label, r.episodes, r.successes, r.errored)
                            for r in R.collect([root]).rows]  # fmt: skip  # noqa: E731
    assert summary(copy) == summary(logs)


@pytest.mark.parametrize("enabled", [True, False])
def test_a_local_sweep_opens_the_preview_unless_turned_off(tmp_path, monkeypatch, enabled):
    from omegaconf import OmegaConf

    from open_apps import parallel_launcher
    import open_apps.results_preview as preview

    opened = []
    monkeypatch.setattr(parallel_launcher, "create_job_configs", lambda config: [])
    monkeypatch.setattr(parallel_launcher, "run_locally", lambda *a: None)
    monkeypatch.setattr(preview, "launch", lambda paths: opened.append(paths))
    config = OmegaConf.create(
        {"cluster": "local", "logs_dir": str(tmp_path / "log_outputs" / "now-me"),
         "open_leaderboard": enabled}
    )  # fmt: skip
    parallel_launcher.launch_parallel(config)
    assert opened == ([[tmp_path / "log_outputs"]] if enabled else [])


def test_a_slurm_sweep_prints_the_preview_command(tmp_path, monkeypatch, capsys):
    from omegaconf import OmegaConf

    from open_apps import parallel_launcher

    monkeypatch.setattr(parallel_launcher, "create_job_configs", lambda config: [])
    monkeypatch.setattr(parallel_launcher, "run_via_slurm", lambda *a: [])
    config = OmegaConf.create(
        {"cluster": "slurm", "logs_dir": str(tmp_path / "logs" / "sweep")}
    )
    parallel_launcher.launch_parallel(config)
    out = capsys.readouterr().out
    assert f"uv run -m open_apps.results_preview {tmp_path / 'logs'}" in out
    config.open_leaderboard = False
    config.logs_dir = str(tmp_path / "logs" / "sweep")
    parallel_launcher.launch_parallel(config)
    assert "results_preview" not in capsys.readouterr().out


# --- the drill-down's Reproduce and Configuration sections ---------------------

AGENT_SETUP = {"client_type": "vllm", "use_screenshot": True, "use_axtree": False,
               "use_history": True, "temperature": None, "max_tokens": None,
               "hostname": "gpu-node-1"}  # fmt: skip
DARK = ["apps/theme=dark"]
GERMAN = ["apps/theme=default", "apps/todo/content=german"]


def sweep_runs(root: Path, tasks, variations, **kwargs) -> None:
    """One job per (variation, task), as launch_parallel_agents.py lays them out."""
    job = len(list(root.glob("*"))) if root.exists() else 0
    for variation in variations:
        for task in tasks:
            make_run(root / str(job), task=task, overrides=variation,
                     agent_extra=AGENT_SETUP,
                     extra={"agent_config_name": "my-agent", "device": {
                         "name": "desktop", "viewport": [1920, 1080]}},
                     **kwargs)  # fmt: skip
            job += 1


def parse_command(text: str) -> dict:
    """The command's Hydra overrides, parsed the way the launcher parses them."""
    import shlex

    from hydra.core.override_parser.overrides_parser import OverridesParser

    args = shlex.split(text.replace("\\\n", " "))
    assert args[:3] == ["uv", "run", "launch_parallel_agents.py"]
    parser = OverridesParser.create()
    return {a.partition("=")[0]: parser.parse_override(a).value() for a in args[3:]}


def test_the_command_reruns_the_agent_tasks_variations_and_seed(tmp_path):
    sweep_runs(tmp_path, ["todo_add", "calendar_add"], [[], DARK, GERMAN], seed=7)
    (row,) = R.collect([tmp_path]).rows
    rep = reproduce.commands(row.runs)
    assert rep.n_configs == 1 and len(rep.commands) == 1
    text = rep.commands[0].text
    assert "agent=my-agent" in text and "seed=7" in text
    args = parse_command(text)
    # Round trip: the overrides parse back to the runs' tasks and variations.
    assert set(args["parallel_tasks.task_names"]) == {r.task for r in row.runs}
    assert {tuple(v) for v in args["parallel_tasks.app_variations"]} == {
        tuple(r.overrides) for r in row.runs
    }
    assert args["seed"] == 7 and args["browsergym_env_args.max_steps"] == 30
    assert args["device"] == "desktop" and args["agent.hostname"] == "gpu-node-1"


def test_runs_off_one_grid_get_one_command_per_block(tmp_path):
    sweep_runs(tmp_path, ["a_task", "b_task"], [[], DARK])
    sweep_runs(tmp_path, ["c_task"], [[]])
    (row,) = R.collect([tmp_path]).rows
    rep = reproduce.commands(row.runs)
    assert rep.n_configs == 1
    cells = set()
    for command in rep.commands:
        args = parse_command(command.text)
        for task in args["parallel_tasks.task_names"]:
            for variation in args["parallel_tasks.app_variations"]:
                cells.add((task, tuple(variation)))
    assert cells == {(r.task, tuple(r.overrides)) for r in row.runs}  # exactly


def test_mixed_configurations_yield_one_command_each(tmp_path):
    sweep_runs(tmp_path / "short", ["todo_add"], [[]], max_steps=15)
    sweep_runs(tmp_path / "long", ["todo_add"], [DARK], max_steps=30)
    (row,) = R.collect([tmp_path]).rows
    rep = reproduce.commands(row.runs)
    assert rep.n_configs == 2 and rep.differs_in == ["max steps"]
    steps = sorted(parse_command(c.text)["browsergym_env_args.max_steps"]
                   for c in rep.commands)  # fmt: skip
    assert steps == [15, 30]
    html = P.Page(R.collect([tmp_path]), background_seed=0).html()
    assert "2 configurations (they differ in max steps)" in html
    assert "Configuration 2 · 1 run" in html


def test_a_variation_that_sets_the_device_is_not_another_configuration(tmp_path):
    sweep_runs(tmp_path, ["todo_add"], [[], ["device=phone"]])
    for config in tmp_path.glob("*/config.yaml"):  # the phone job records a phone
        data = yaml.safe_load(config.read_text())
        if data["app_overrides"] == ["device=phone"]:
            data["device"] = {"name": "phone", "viewport": [390, 844]}
            config.write_text(yaml.safe_dump(data))
    (row,) = R.collect([tmp_path]).rows
    rep = reproduce.commands(row.runs)
    assert rep.n_configs == 1 and len(rep.commands) == 1
    assert parse_command(rep.commands[0].text)["device"] == "desktop"
    episodes = dict(dict(reproduce.configuration(row.runs))["Episodes"])
    assert episodes["device"] == "varies: desktop 1920×1080, phone 390×844"


def test_hydras_recorded_overrides_win(tmp_path):
    sweep_runs(tmp_path, ["todo_add"], [[]])
    hydra = tmp_path / "0" / ".hydra"
    hydra.mkdir()
    (hydra / "overrides.yaml").write_text(yaml.safe_dump(
        ["agent=exact-agent", "agent.temperature=0.2", "parallel_tasks.task_names=[x]"]))
    (row,) = R.collect([tmp_path]).rows
    args = parse_command(reproduce.commands(row.runs).commands[0].text)
    assert args["agent"] == "exact-agent" and args["agent.temperature"] == 0.2
    assert args["parallel_tasks.task_names"] == ["todo_add"]  # the runs', not the record's


def test_configuration_reads_none_for_null_and_varies_for_differences(tmp_path):
    sweep_runs(tmp_path / "a", ["todo_add"], [[]], max_steps=15, seed=1)
    sweep_runs(tmp_path / "b", ["todo_add"], [DARK], max_steps=30, seed=1)
    (row,) = R.collect([tmp_path]).rows
    sections = {title: dict(items) for title, items in reproduce.configuration(row.runs)}
    assert sections["Agent"]["max_tokens"] == "none"
    assert sections["Agent"]["temperature"] == "default"
    assert sections["Agent"]["client"] == "vLLM"
    assert sections["Agent"]["observation"] == "screenshot"
    assert sections["Agent"]["history"] == "on"
    assert sections["Episodes"]["max steps"] == "varies: 15, 30"
    assert sections["Episodes"]["ends on"] == reproduce.DEFAULT_TERMINATION
    assert sections["Sweep"]["grid"] == "1 task × 2 variations"
    assert sections["Sweep"]["seed"] == "1"
    assert "Code" not in sections  # no commit recorded: no line
    html = P.Page(R.collect([tmp_path]), background_seed=0).html()
    assert '<span class="varies">varies: 15, 30</span>' in html


def test_reproduce_and_configuration_escape_hostile_strings(tmp_path):
    evil = "<img src=x onerror=alert(1)>"
    make_run(tmp_path / "0", task="todo_add", overrides=[f"apps/theme={evil}"],
             agent_extra={"hostname": evil, "action_parser": evil},
             extra={"agent_config_name": evil})  # fmt: skip
    html = P.Page(R.collect([tmp_path]), background_seed=0).html()
    assert "<img" not in html and "&lt;img src=x" in html
    (row,) = R.collect([tmp_path]).rows
    args = parse_command(reproduce.commands(row.runs).commands[0].text)
    assert args["parallel_tasks.app_variations"] == [[f"apps/theme={evil}"]]
    assert args["agent"] == evil


def test_the_copy_button_is_in_the_page_and_hidden_in_print(tmp_path):
    sweep_runs(tmp_path, ["todo_add"], [[]])
    html = P.Page(R.collect([tmp_path]), background_seed=0).html()
    assert html.count('class="copy"') == 1 and 'data-copy="m1-cmd0"' in html
    assert '<code id="m1-cmd0">uv run launch_parallel_agents.py' in html
    assert "navigator.clipboard.writeText" in html and "execCommand" in html
    print_css = html.split("@media print", 1)[1].split("}", 1)[0]
    assert ".copy" in print_css and "display: none" in print_css


def test_runs_with_a_bare_config_degrade_gracefully(tmp_path):
    job = tmp_path / "0"
    job.mkdir()
    (job / "config.yaml").write_text("task_name: todo_add\n")
    exp = job / "2025-01-01_00-00-00_AgentArgs_on_x_42"
    exp.mkdir()
    (exp / "summary_info.json").write_text(json.dumps({"cum_reward": 1.0}))
    results = R.collect([tmp_path])
    (row,) = results.rows
    rep = reproduce.commands(row.runs)
    assert rep.commands[0].agent_unknown
    assert parse_command(rep.commands[0].text)["agent"] == reproduce.UNKNOWN_AGENT
    sections = dict(reproduce.configuration(row.runs))
    assert set(sections) == {"Sweep"}
    html = P.Page(results, background_seed=0).html()
    assert "replace <code>AGENT_CONFIG</code>" in html
    # Runs made outside discovery carry no setup at all.
    bare = R.Run("M", "t", "default", "1/2", True, False, 3, 0.0, "/x", "/")
    assert reproduce.configuration([bare]) == []
    assert "AGENT_CONFIG" in reproduce.commands([bare]).commands[0].text
    row.runs = [bare]
    assert "Not recorded in the runs" in P._configuration(row)


def test_an_unrecorded_agent_config_is_matched_by_model_name(tmp_path, monkeypatch):
    configs = tmp_path / "agents"
    configs.mkdir()
    (configs / "the-one.yaml").write_text("model_name: org/Model-A\nmodel_pretty_name: Model-A\n")
    (configs / "other.yaml").write_text("model_name: org/Other\n")
    monkeypatch.setattr(reproduce, "AGENT_CONFIGS", configs)
    reproduce.guess_agent_config.cache_clear()
    make_run(tmp_path / "logs" / "0")
    results = R.collect([tmp_path / "logs"])
    (command,) = reproduce.commands(results.rows[0].runs).commands
    assert command.agent == "the-one" and command.agent_guessed
    assert "is the one in <code>config/agent/</code>" in P.Page(results, 0).html()
    reproduce.guess_agent_config.cache_clear()
