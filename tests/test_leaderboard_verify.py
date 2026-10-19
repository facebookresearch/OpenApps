"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

``open_apps.leaderboard.verify`` and ``validate --seed-set heldout``: the
plan prints no seed, a held-out run is checked against the held-out cells,
``record`` writes exactly the ``verified`` summary (and nothing per-episode),
build surfaces it, a missing seed source is a clear error, and the seeds hash
is deterministic. Held-out seeds come from a temp file via the env var; no
real seed is ever involved.
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys

import pytest
import yaml

from open_apps.leaderboard import bundle as B
from open_apps.leaderboard import suite as suite_mod
from open_apps.leaderboard import verify as V
from open_apps.leaderboard.suite import HELDOUT_ENV, heldout_seeds_hash
from open_apps.leaderboard.validate import FAIL, OK, validate_bundle
from tests._leaderboard_helpers import (
    EXAMPLE_BUNDLE,
    FIXED_DATE,
    JOBS,
    TINY_SUITE,
    write_logs_tree,
)

# Distinctive, so "never printed" can be checked by substring.
HELDOUT_SEEDS = [918273, 564738]


@pytest.fixture
def seeds_file(tmp_path, monkeypatch):
    path = tmp_path / "seeds.yaml"
    path.write_text(yaml.safe_dump({"seeds": HELDOUT_SEEDS, "salt": "test-only-salt"}))
    monkeypatch.setenv(HELDOUT_ENV, str(path))
    # Never fall back to a maintainer's real internal file.
    monkeypatch.setattr(
        suite_mod, "default_heldout_path", lambda: tmp_path / "absent.yaml"
    )
    return path


@pytest.fixture
def no_seeds(tmp_path, monkeypatch):
    monkeypatch.delenv(HELDOUT_ENV, raising=False)
    monkeypatch.setattr(
        suite_mod, "default_heldout_path", lambda: tmp_path / "absent.yaml"
    )


@pytest.fixture
def bundle(tmp_path):
    dst = tmp_path / "bundles" / EXAMPLE_BUNDLE.name
    shutil.copytree(EXAMPLE_BUNDLE, dst)
    return dst


def heldout_logs(root, worse: bool = False):
    """The example agent's sweep re-run on every held-out seed. ``worse``
    makes one goal-required delete do nothing, so held-out aligned drops."""
    from open_apps.alignment.record import EPISODE_FILENAME

    for seed in HELDOUT_SEEDS:
        write_logs_tree(root / str(seed), rebuilt_job=None, seed=seed)
    if worse:
        victim = next(
            p
            for p in sorted((root / str(HELDOUT_SEEDS[0])).rglob(EPISODE_FILENAME))
            if "delete_call_aunt_lisa" in str(p)
        )
        data = json.loads(victim.read_text())
        data["final_state"] = data["initial_state"]
        victim.write_text(json.dumps(data))
    return root


def _bundle_text(path):
    return "".join(
        gzip.decompress(p.read_bytes()).decode() if p.suffix == ".gz" else p.read_text()
        for p in sorted(path.iterdir())
    )


# --- seeds hash ------------------------------------------------------------------------


def test_seeds_hash_is_deterministic(seeds_file, tmp_path):
    first, salted = heldout_seeds_hash()
    assert salted and first == heldout_seeds_hash()[0]
    assert len(first) == 16 and all(c in "0123456789abcdef" for c in first)
    assert not any(str(s) in first for s in HELDOUT_SEEDS)

    def hash_of(data):
        path = tmp_path / "other.yaml"
        path.write_text(yaml.safe_dump(data))
        return heldout_seeds_hash(path)

    # Order and repeats do not change which cells run, so not the hash.
    reordered = list(reversed(HELDOUT_SEEDS)) + [HELDOUT_SEEDS[0]]
    assert hash_of({"seeds": reordered, "salt": "test-only-salt"})[0] == first
    assert hash_of({"seeds": HELDOUT_SEEDS, "salt": "another"})[0] != first
    assert hash_of({"seeds": HELDOUT_SEEDS[:1], "salt": "test-only-salt"})[0] != first
    unsalted, salted = hash_of({"seeds": HELDOUT_SEEDS})
    assert not salted and unsalted != first


# --- plan ------------------------------------------------------------------------------


def test_plan_prints_the_command_and_no_seed(seeds_file, bundle):
    text = V.plan(bundle)
    for seed in HELDOUT_SEEDS:
        assert str(seed) not in text
    assert heldout_seeds_hash()[0] in text
    assert "2 held-out seed(s)" in text
    assert "benchmark=tiny_v1 benchmark.seed_set=heldout" in text
    assert f"agent={bundle.name}" in text
    assert "hydra.searchpath=[file://" in text
    assert f"{V.staging_dir(bundle)}/agent/{bundle.name}.yaml" in text
    # The keys pack redacted, which the maintainer must fill in.
    assert "api_key, hostname" in text
    assert "verify record" in text
    # Staged under the gitignored log_outputs/, never config/.
    rel = V.staging_dir(bundle).relative_to(B.repo_root())
    assert rel.parts[0] == "log_outputs"


def test_plan_warns_about_an_unsalted_seeds_file(seeds_file, bundle):
    seeds_file.write_text(yaml.safe_dump({"seeds": HELDOUT_SEEDS}))
    assert "no `salt`" in V.plan(bundle)


def test_cli_plan(seeds_file, bundle):
    proc = subprocess.run(
        [sys.executable, "-m", "open_apps.leaderboard.verify", "plan", str(bundle)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "benchmark.seed_set=heldout" in proc.stdout
    for seed in HELDOUT_SEEDS:
        assert str(seed) not in proc.stdout + proc.stderr


# --- missing seed source ---------------------------------------------------------------


def test_missing_seed_source_is_a_clear_error(no_seeds, bundle, tmp_path):
    with pytest.raises(FileNotFoundError, match=HELDOUT_ENV):
        V.plan(bundle)
    with pytest.raises(FileNotFoundError, match=HELDOUT_ENV):
        V.record(bundle, [tmp_path / "logs"], suite=TINY_SUITE)
    report = validate_bundle(bundle, suite=TINY_SUITE, seed_set="heldout")
    assert report.status == FAIL
    assert any(HELDOUT_ENV in e for e in report.errors)
    # The bundle is untouched.
    assert (bundle / B.META_FILE).read_text() == (
        EXAMPLE_BUNDLE / B.META_FILE
    ).read_text()


# --- held-out validation ---------------------------------------------------------------


def test_heldout_validation_uses_the_heldout_cells(seeds_file, bundle, tmp_path):
    """A held-out run validates against held-out cells only; the public
    bundle does not, and output never shows a held-out seed."""
    meta = B.read_yaml(bundle / B.META_FILE)
    held = V.heldout_report(
        bundle, [heldout_logs(tmp_path / "logs")], meta, tmp_path, suite=TINY_SUITE
    )
    assert held.errors == [], held.format()
    assert held.status == OK
    assert held.n_expected == len(JOBS) * len(HELDOUT_SEEDS) == held.n_records

    as_public = validate_bundle(held.path, suite=TINY_SUITE)
    assert as_public.status == FAIL
    assert any("not a tiny_v1 cell (public seeds)" in e for e in as_public.errors)

    public_as_heldout = validate_bundle(bundle, suite=TINY_SUITE, seed_set="heldout")
    assert public_as_heldout.status == FAIL
    assert len(public_as_heldout.missing) == held.n_expected
    text = public_as_heldout.format()
    assert "held-out#0" in text and "seed=233" in text
    for seed in HELDOUT_SEEDS:
        assert str(seed) not in text


def test_partial_heldout_run_is_refused_without_naming_seeds(
    seeds_file, bundle, tmp_path
):
    logs = tmp_path / "logs"
    write_logs_tree(logs / "only", rebuilt_job=None, seed=HELDOUT_SEEDS[0])
    with pytest.raises(V.VerifyError, match="every held-out cell") as info:
        V.record(bundle, [logs], suite=TINY_SUITE, date=FIXED_DATE)
    # 918273 ran, so the missing cells are the other seed: position 0 of the sorted set.
    assert "missing: " in str(info.value) and "seed=held-out#0" in str(info.value)
    for seed in HELDOUT_SEEDS:
        assert str(seed) not in str(info.value)
    assert "verified: false" in (bundle / B.META_FILE).read_text()


# --- record ----------------------------------------------------------------------------


def test_record_writes_the_verified_block(seeds_file, bundle, tmp_path):
    episodes = (bundle / B.EPISODES_FILE).read_bytes()
    public = validate_bundle(bundle, suite=TINY_SUITE)
    public_rate = sum(r["aligned"] for r in public.rows) / len(public.rows)

    result = V.record(
        bundle,
        [heldout_logs(tmp_path / "logs", worse=True)],
        suite=TINY_SUITE,
        date="2026-10-20",
    )
    meta = yaml.safe_load((bundle / B.META_FILE).read_text())
    verified = meta["verified"]
    assert set(verified) == {"date", "seeds_hash", "aligned", "delta"}
    assert verified["date"] == "2026-10-20"
    assert verified["seeds_hash"] == heldout_seeds_hash()[0]
    # One of 2 x 6 held-out episodes lost its aligned verdict.
    n = len(JOBS) * len(HELDOUT_SEEDS)
    held_rate = (public_rate * n - 1) / n
    assert verified["aligned"] == round(held_rate, V.DIGITS)
    assert verified["delta"] == round(held_rate - public_rate, V.DIGITS) < 0
    assert result.n_heldout == n

    # Only the summary reached the bundle: episodes untouched, no seed anywhere.
    assert (bundle / B.EPISODES_FILE).read_bytes() == episodes
    text = _bundle_text(bundle)
    for seed in HELDOUT_SEEDS:
        assert str(seed) not in text
    assert sorted(p.name for p in bundle.iterdir()) == sorted(
        [B.META_FILE, B.AGENT_FILE, B.EPISODES_FILE]
    )

    # Still a valid bundle (meta.schema.json accepts the block), and build
    # carries it to the page.
    from open_apps.leaderboard.build import build

    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert report.errors == [] and report.status == OK
    doc = build([report], when="2026-10-20T00:00:00Z", n_boot=10)
    (row,) = doc["suites"]["tiny_v1"]["rows"]
    assert row["verified"] == verified


def test_record_refuses_a_different_agent_config(seeds_file, bundle, tmp_path):
    logs = heldout_logs(tmp_path / "logs")
    for config in logs.rglob("config.yaml"):
        data = yaml.safe_load(config.read_text())
        data["agent"]["max_tokens"] = 1
        config.write_text(yaml.safe_dump(data))
    with pytest.raises(V.VerifyError, match="different agent config"):
        V.record(bundle, [logs], suite=TINY_SUITE)


def test_record_accepts_filled_in_secrets(seeds_file, bundle, tmp_path):
    """The maintainer's own endpoint and key are not a different agent."""
    logs = heldout_logs(tmp_path / "logs")
    for config in logs.rglob("config.yaml"):
        data = yaml.safe_load(config.read_text())
        data["agent"].update(api_key="maintainer-key", hostname="maintainer-host")
        config.write_text(yaml.safe_dump(data))
    V.record(bundle, [logs], suite=TINY_SUITE, date=FIXED_DATE)
    text = _bundle_text(bundle)
    assert "maintainer-key" not in text and "maintainer-host" not in text


def test_cli_record(seeds_file, bundle, tmp_path):
    logs = heldout_logs(tmp_path / "logs")
    cmd = [sys.executable, "-m", "open_apps.leaderboard.verify", "record"]
    cmd += [str(bundle), str(logs), "--suite", str(TINY_SUITE), "--date", FIXED_DATE]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "delta +0.0 points" in proc.stdout
    assert heldout_seeds_hash()[0] in proc.stdout
    for seed in HELDOUT_SEEDS:
        assert str(seed) not in proc.stdout + proc.stderr
