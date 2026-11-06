"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Local sweeps split across processes (``shard_index``/``num_shards``), with
``continue_on_error`` and ``wait_for_url`` -- what scripts/benchmark_one.sbatch
runs against one vLLM server.
"""

import http.server
import threading

import pytest
from omegaconf import OmegaConf

from open_apps import parallel_launcher as pl


def _jobs(n):
    return [OmegaConf.create({"job_id": i}) for i in range(n)]


def _run(monkeypatch, config, jobs, fail=()):
    ran = []

    def fake_run_task(job, launcher):
        if job.job_id in fail:
            raise RuntimeError(f"job {job.job_id} broke")
        ran.append(job.job_id)

    monkeypatch.setattr(pl, "run_task", fake_run_task)
    pl.run_locally(OmegaConf.create(config), jobs, len(jobs))
    return ran


def test_shards_partition_the_sweep(monkeypatch):
    jobs = _jobs(10)
    seen = []
    for k in range(3):
        seen += _run(monkeypatch, {"shard_index": k, "num_shards": 3}, jobs)
    assert sorted(seen) == list(range(10))
    assert pl.shard_indices(10, 1, 3) == [1, 4, 7]
    with pytest.raises(ValueError):
        pl.shard_indices(10, 3, 3)


def test_unsharded_by_default(monkeypatch):
    assert _run(monkeypatch, {}, _jobs(4)) == [0, 1, 2, 3]


def test_continue_on_error_runs_the_rest_then_fails(monkeypatch):
    with pytest.raises(RuntimeError):
        _run(monkeypatch, {}, _jobs(3), fail={1})
    ran = []
    monkeypatch.setattr(
        pl, "run_task",
        lambda job, launcher: ran.append(job.job_id) if job.job_id != 1 else 1 / 0,
    )
    with pytest.raises(SystemExit):
        pl.run_locally(OmegaConf.create({"continue_on_error": True}), _jobs(3), 3)
    assert ran == [0, 2]


def test_wait_for_url_blocks_until_the_server_answers(monkeypatch):
    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/health"
        pl.wait_for_url(url, timeout_s=5, poll_s=0.1)
        assert _run(monkeypatch, {"wait_for_url": url}, _jobs(2)) == [0, 1]
    finally:
        server.shutdown()
    with pytest.raises(TimeoutError):
        pl.wait_for_url(url, timeout_s=0.3, poll_s=0.1)
