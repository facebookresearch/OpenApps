"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

``open_apps.leaderboard.import_wandb`` against a fake W&B Public API (no
network): runs are filtered by group and tag, runs without the
``episode_record`` artifact are skipped with a reason, re-runs are resolved
by pack's rule, the agent config is redacted, W&B summary scores are never
used, rate limits are retried, and the bundle validates on the tiny suite.
"""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest
import requests
import yaml

from open_apps.leaderboard import bundle as B
from open_apps.leaderboard import import_wandb as W
from open_apps.leaderboard.validate import OK, validate_bundle
from tests._leaderboard_helpers import (
    DUMMY_AGENT,
    FIXED_DATE,
    JOBS,
    TINY_SUITE,
    tiny_record,
)

# A placeholder, like everything W&B-shaped in this repo.
PROJECT = "entity/project"
GROUP = "tiny-sweep"
SECRET_KEY = "wandb-key-must-never-appear"


# --- the fake Public API -------------------------------------------------------------


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status}", response=response)


class FakeArtifact:
    def __init__(
        self, record: dict, type: str = W.ARTIFACT_TYPE, name: str = "episode"
    ):
        self.record, self.type, self.name = record, type, name
        self.downloads = 0

    def download(self, root=None, **_):
        self.downloads += 1
        path = Path(root)
        path.mkdir(parents=True, exist_ok=True)
        (path / "episode.json").write_text(json.dumps(self.record))
        return str(path)


class FakeRun:
    def __init__(
        self,
        run_id: str,
        record: dict | None,
        group: str = GROUP,
        tags: tuple = ("align",),
        heartbeat_at: str = "2026-10-16T12:00:00",
        state: str = "finished",
        agent: dict | None = None,
        artifact_type: str = W.ARTIFACT_TYPE,
    ):
        self.id, self.group, self.tags, self.state = run_id, group, list(tags), state
        self.created_at = heartbeat_at
        self.heartbeat_at = heartbeat_at
        self.config = {
            "agent": copy.deepcopy(agent if agent is not None else DUMMY_AGENT),
            "task_name": record["identity"]["task_name"] if record else None,
        }
        # A tampered summary: must never reach the bundle.
        self.summary = {"align/aligned": 1, "align/success": 1, "success": 1}
        self.artifacts = (
            [FakeArtifact(record, type=artifact_type, name=f"episode-{run_id}")]
            if record is not None
            else []
        )

    def logged_artifacts(self, per_page: int = 100):
        return iter(self.artifacts)


class FakeApi:
    """``runs()`` pages like the real paginator (lazily, ``per_page`` at a
    time) and can fail one page fetch with a 429; it ignores ``filters`` and
    records them, so the client-side filter is what is tested."""

    def __init__(self, runs: list[FakeRun], fail_page: int | None = None):
        self._runs = runs
        self.fail_page = fail_page
        self.calls: list[dict] = []

    def runs(self, path, filters=None, order="+created_at", per_page=50):
        self.calls.append({"path": path, "filters": filters, "per_page": per_page})

        def pages():
            for page, start in enumerate(range(0, len(self._runs), per_page)):
                if page == self.fail_page:
                    self.fail_page = None  # fail once
                    raise _http_error(429)
                yield from self._runs[start : start + per_page]

        return pages()

    def run(self, path):
        run_id = path.rsplit("/", 1)[-1]
        for run in self._runs:
            if run.id == run_id:
                return run
        raise _http_error(404)


def _job_runs(**kwargs) -> list[FakeRun]:
    return [
        FakeRun(f"run{job}", tiny_record(name, task, variation), **kwargs)
        for job, name, task, variation in JOBS
    ]


def _lines(path):
    with gzip.open(path / B.EPISODES_FILE, "rt") as fh:
        return [json.loads(line) for line in fh]


def _import(api, tmp_path, **kwargs):
    kwargs.setdefault("group", GROUP)
    return W.import_wandb(
        PROJECT,
        org="example",
        agent="dummy",
        out=tmp_path / "example__dummy__tiny_v1",
        date=FIXED_DATE,
        api=api,
        sleep=lambda s: None,
        **kwargs,
    )


# --- tests ---------------------------------------------------------------------------


def test_import_writes_a_bundle_that_validates(tmp_path):
    result = _import(FakeApi(_job_runs()), tmp_path)
    assert result.n_episodes == len(JOBS) and result.n_runs == len(JOBS)
    assert result.skipped_runs == [] and result.duplicates == []
    report = validate_bundle(result.out, suite=TINY_SUITE)
    assert report.errors == [], report.format()
    assert report.status == OK


def test_import_matches_pack_byte_for_byte(tmp_path):
    """Same records, same code path: the bundle is what pack would write."""
    from open_apps.leaderboard.pack import pack
    from tests._leaderboard_helpers import write_logs_tree

    # Key order as the fixture's config.yaml has it (yaml.safe_dump sorts):
    # agent.yaml keeps the source's order, which is all that differs.
    runs = _job_runs(agent=dict(sorted(DUMMY_AGENT.items())))
    imported = _import(FakeApi(runs), tmp_path / "w").out
    packed = pack(
        [write_logs_tree(tmp_path / "logs", rebuilt_job=None)],
        org="example",
        agent="dummy",
        out=tmp_path / "p" / imported.name,
        date=FIXED_DATE,
    ).out
    for name in (B.META_FILE, B.AGENT_FILE, B.EPISODES_FILE):
        assert (imported / name).read_bytes() == (packed / name).read_bytes(), name


def test_summary_scores_are_ignored(tmp_path):
    """Every run's W&B summary claims aligned=1; the bundle carries the
    scorer's verdicts instead."""
    from open_apps.alignment.record import EpisodeRecord
    from open_apps.alignment.score import score

    out = _import(FakeApi(_job_runs()), tmp_path).out
    lines = _lines(out)
    assert not all(line["scores"]["aligned"] for line in lines)
    for line in lines:
        record = EpisodeRecord.from_dict(
            {k: v for k, v in line.items() if k != "scores"}
        )
        assert line["scores"] == score(record).summary()


def test_filters_by_group_and_tag(tmp_path):
    runs = _job_runs(tags=("align", "v1"))
    runs[0].group = "another-sweep"
    runs[1].tags = ["align"]  # lacks v1
    api = FakeApi(runs)
    result = _import(api, tmp_path, tags=["v1"])
    assert result.n_runs == len(JOBS) - 2
    assert result.n_episodes == len(JOBS) - 2
    # The query went to the server too (it is cheaper than paging everything).
    (call,) = api.calls
    assert call["filters"] == {"$and": [{"group": GROUP}, {"tags": {"$in": ["v1"]}}]}
    assert call["path"] == "entity/project"


def test_run_ids_are_fetched_directly_and_still_filtered(tmp_path):
    runs = _job_runs()
    runs[1].group = "another-sweep"
    api = FakeApi(runs)
    result = _import(api, tmp_path, run_ids=["run0", "run1", "run2"])
    assert api.calls == []  # no listing
    assert result.n_runs == 2 and result.n_episodes == 2


def test_runs_without_the_artifact_are_skipped_with_a_reason(
    tmp_path, capsys, monkeypatch
):
    runs = _job_runs()
    runs[0].artifacts = []
    runs[1].artifacts[0].type = "model"  # not an episode record
    runs.append(FakeRun("busy", tiny_record(*JOBS[2][1:]), state="running"))
    api = FakeApi(runs)
    monkeypatch.setattr(W, "make_api", lambda base_url=None: api)
    rc = W.main(
        [
            "entity/project",
            "--group", GROUP,
            "--org", "example",
            "--agent", "dummy",
            "--out", str(tmp_path / "example__dummy__tiny_v1"),
        ]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped 2 run(s): no 'episode_record' artifact" in out
    assert "record_alignment=false" in out
    assert "skipped entity/project/busy: still running" in out
    assert f"({len(JOBS) - 2} episodes from {len(JOBS) + 1} matching run(s))" in out


def test_a_crashed_rerun_loses_otherwise_the_latest_wins(tmp_path):
    runs = _job_runs()
    _, name, task, variation = JOBS[0]
    crashed = tiny_record(name, task, variation)
    crashed["termination"] = {
        "reason": "error",
        "final_action": None,
        "err_msg": "boom",
    }
    runs.append(FakeRun("crash", crashed, heartbeat_at="2026-10-17T00:00:00"))
    result = _import(FakeApi(runs), tmp_path)
    assert result.n_episodes == len(JOBS)
    assert [loser for _, loser in result.duplicates] == ["entity/project/crash"]

    # Two clean runs of a cell: the later heartbeat wins, never the better score.
    runs = _job_runs()
    runs.append(
        FakeRun(
            "later",
            tiny_record(name, task, variation),
            heartbeat_at="2026-10-18T00:00:00",
        )
    )
    result = _import(FakeApi(runs), tmp_path / "again")
    assert [loser for _, loser in result.duplicates] == ["entity/project/run0"]


def test_agent_config_is_redacted_and_no_credential_is_written(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("WANDB_API_KEY", SECRET_KEY)
    agent = dict(DUMMY_AGENT, base_url="https://vllm-host.example:8000/v1")
    api = FakeApi(_job_runs(agent=agent))
    monkeypatch.setattr(W, "make_api", lambda base_url=None: api)
    out = tmp_path / "example__dummy__tiny_v1"
    rc = W.main(
        [
            "entity/project", "--group", GROUP, "--org", "example", "--agent", "dummy",
            "--out", str(out), "--report-url", "https://wandb.ai/<entity>/<project>/reports/x",
            "--base-url", "https://wandb.example",
        ]
    )  # fmt: skip
    printed = capsys.readouterr()
    assert rc == 0
    cfg = yaml.safe_load((out / B.AGENT_FILE).read_text())
    assert cfg["api_key"] == cfg["hostname"] == cfg["base_url"] == B.REDACTED
    meta = yaml.safe_load((out / B.META_FILE).read_text())
    assert sorted(meta["redacted"]) == ["api_key", "base_url", "hostname"]
    assert meta["wandb_report"] == "https://wandb.ai/<entity>/<project>/reports/x"
    assert meta["verified"] is False
    blob = b"".join(
        (gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes())
        for p in out.iterdir()
    ).decode()
    for leak in (SECRET_KEY, "sk-not-a-real-key", "vllm-host", "wandb.example"):
        assert leak not in blob
        assert leak not in printed.out + printed.err


def test_mixed_agent_configs_are_refused(tmp_path):
    runs = _job_runs()
    runs[0].config["agent"]["max_tokens"] = 1
    with pytest.raises(ValueError, match="different agent configs"):
        _import(FakeApi(runs), tmp_path)


def test_rate_limits_are_retried_and_pagination_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "PER_PAGE", 2)
    api = FakeApi(_job_runs(), fail_page=1)
    delays = []
    result = W.import_wandb(
        "entity/project", org="example", agent="dummy", group=GROUP,
        out=tmp_path / "b", api=api, sleep=delays.append,
    )  # fmt: skip
    assert delays == [W.BASE_DELAY_S]
    assert len(api.calls) == 2 and api.calls[0]["per_page"] == 2
    assert result.n_runs == len(JOBS) and result.n_episodes == len(JOBS)


def test_backoff_gives_up_on_non_transient_errors():
    calls = []

    def boom():
        calls.append(1)
        raise _http_error(403)

    with pytest.raises(requests.HTTPError):
        W.with_backoff(boom, "x", sleep=lambda s: None)
    assert len(calls) == 1

    def throttled():
        calls.append(1)
        raise _http_error(429)

    calls.clear()
    delays = []
    with pytest.raises(requests.HTTPError):
        W.with_backoff(throttled, "x", retries=3, sleep=delays.append)
    assert len(calls) == 4 and delays == [2.0, 4.0, 8.0]


def test_bad_arguments():
    with pytest.raises(ValueError, match="<entity>/<project>"):
        W.import_wandb("just-a-project", org="o", agent="a", group="g", api=FakeApi([]))
    with pytest.raises(ValueError, match="pick the runs"):
        W.import_wandb("e/p", org="o", agent="a", api=FakeApi([]))
    with pytest.raises(FileNotFoundError, match="no runs"):
        W.import_wandb("e/p", org="o", agent="a", group="g", api=FakeApi([]))
    runs = _job_runs()
    for run in runs:
        run.artifacts = []
    with pytest.raises(FileNotFoundError, match="record_alignment"):
        W.import_wandb("e/p", org="o", agent="a", group=GROUP, api=FakeApi(runs))
