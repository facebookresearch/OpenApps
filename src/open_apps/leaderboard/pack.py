"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Pack a finished ``benchmark=<suite>`` sweep into a leaderboard bundle.

    uv run -m open_apps.leaderboard.pack log_outputs/2026-10-16_12-00-00 \\
        --org my-lab --agent my-agent [--contact @me] [--out DIR]

Walks every experiment directory under the given logs dirs (local or SLURM
layouts alike: each job's ``logs_dir`` holds its ``config.yaml`` and one
BrowserGym ``exp_dir``) and collects one ``EpisodeRecord`` per suite cell:

* ``exp_dir/episode.json`` when the launcher wrote it;
* otherwise, rebuilt with ``build_episode_record`` when the episode finished
  (``summary_info.json``) and the task persisted its app states
  (``openapps_states.json``) -- e.g. a run whose scoring step crashed;
* otherwise skipped and reported (the cell will show up as missing).

Every record is scored here with ``open_apps.alignment.score`` and the scores
ride along in the bundle for CI to re-check. ``agent.yaml`` is the run's own
agent config with credentials and hostnames redacted; ``meta.yaml`` is filled
from the configs and records. Run ``validate`` on the result before opening a
PR -- CI runs exactly that.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from omegaconf import OmegaConf

from open_apps.leaderboard import bundle as B
from open_apps.leaderboard.suite import cell_key

RUN_CONFIG = "config.yaml"
SUMMARY_FILE = "summary_info.json"  # BrowserGym's, written at episode end


@dataclass
class Collected:
    record: Any
    exp_dir: Path
    config_path: Optional[Path]
    source: str  # "episode.json" | "rebuilt"

    @property
    def key(self) -> tuple[str, str, int]:
        ident = self.record.identity
        return cell_key(ident.task_name, ident.variation, ident.seed)


@dataclass
class PackResult:
    out: Path
    n_episodes: int
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    duplicates: list[tuple[str, Path]] = field(default_factory=list)


def find_exp_dirs(logs_dirs: Iterable[str | Path]) -> list[Path]:
    from open_apps.alignment.record import EPISODE_FILENAME, STATES_FILENAME

    found: set[Path] = set()
    for root in map(Path, logs_dirs):
        for name in (EPISODE_FILENAME, SUMMARY_FILE, STATES_FILENAME):
            found.update(p.parent for p in root.rglob(name))
    return sorted(found)


def find_run_config(exp_dir: Path, roots: Iterable[Path]) -> Optional[Path]:
    """The nearest ``config.yaml`` at or above ``exp_dir``, not above the
    logs dir it was found under (a parent sweep's config is not this run's)."""
    roots = [Path(r).resolve() for r in roots]
    here = exp_dir.resolve()
    while True:
        candidate = here / RUN_CONFIG
        if candidate.is_file():
            return candidate
        if here in roots or here.parent == here:
            return None
        here = here.parent


def _load_config(path: Path):
    return OmegaConf.load(path)


def _rebuild(exp_dir: Path, config_path: Optional[Path]) -> Any:
    """``build_episode_record`` for an exp_dir without ``episode.json``.

    Identity comes from the job's saved config exactly as the launcher builds
    it (``identity_from_config``), so a rebuilt record is indistinguishable
    from one written live.
    """
    import hydra

    from open_apps.alignment.irreversible import map_from_apps_config
    from open_apps.alignment.record import build_episode_record, identity_from_config

    if config_path is None:
        raise FileNotFoundError(
            f"no {RUN_CONFIG} above {exp_dir}; cannot rebuild its identity"
        )
    config = _load_config(config_path)
    task = hydra.utils.instantiate(config.tasks[config.task_name])
    task_seed = (config.get("browsergym_env_args") or {}).get("task_seed")
    identity = identity_from_config(config, task, task_seed=task_seed)
    return build_episode_record(
        exp_dir,
        identity=identity,
        irreversible=map_from_apps_config(config.get("apps")),
    )


def collect(logs_dirs: list[Path]) -> tuple[list[Collected], list[tuple[Path, str]]]:
    from open_apps.alignment.record import (
        EPISODE_FILENAME,
        STATES_FILENAME,
        load_record,
    )

    collected, skipped = [], []
    for exp_dir in find_exp_dirs(logs_dirs):
        config_path = find_run_config(exp_dir, logs_dirs)
        try:
            if (exp_dir / EPISODE_FILENAME).is_file():
                record, source = load_record(exp_dir), "episode.json"
            elif (exp_dir / SUMMARY_FILE).is_file() and (
                exp_dir / STATES_FILENAME
            ).is_file():
                record, source = _rebuild(exp_dir, config_path), "rebuilt"
            else:
                skipped.append(
                    (
                        exp_dir,
                        "episode did not finish (no summary_info.json or app states)",
                    )
                )
                continue
        except Exception as exc:  # noqa: BLE001 -- one bad exp_dir must not sink the pack
            skipped.append((exp_dir, f"{type(exc).__name__}: {exc}"))
            continue
        collected.append(Collected(record, exp_dir, config_path, source))
    return collected, skipped


def _prefer(a: Collected, b: Collected) -> Collected:
    """Which of two records for the same cell goes in the bundle.

    An ``error`` termination is an infrastructure failure (a crash, an
    unparseable action), so a re-run of it is legitimate and wins. Otherwise
    the later run wins -- a fixed rule, never "the better score", which would
    turn re-runs into best-of-N.
    """
    a_err = a.record.termination.reason == "error"
    b_err = b.record.termination.reason == "error"
    if a_err != b_err:
        return b if a_err else a

    def mtime(c: Collected) -> float:
        return max(
            (p.stat().st_mtime for p in c.exp_dir.iterdir() if p.is_file()), default=0.0
        )

    return b if (mtime(b), str(b.exp_dir)) >= (mtime(a), str(a.exp_dir)) else a


def _agent_config(collected: list[Collected]) -> tuple[dict, list[str]]:
    """The redacted agent config shared by every run; refuse a mix."""
    by_hash: dict[str, tuple[dict, list[str]]] = {}
    for config_path in sorted({c.config_path for c in collected if c.config_path}):
        agent = OmegaConf.to_container(
            _load_config(config_path).get("agent") or {}, resolve=True
        )
        redacted, paths = B.redact(agent)
        by_hash.setdefault(B.agent_config_hash(redacted), (redacted, paths))
    if not by_hash:
        raise FileNotFoundError(
            f"no {RUN_CONFIG} found next to the runs; agent.yaml needs the run's agent config"
        )
    if len(by_hash) > 1:
        raise ValueError(
            f"runs used {len(by_hash)} different agent configs ({sorted(by_hash)}); "
            "pack one agent per bundle"
        )
    return next(iter(by_hash.values()))


def pack(
    logs_dirs: Iterable[str | Path],
    org: str,
    agent: str,
    out: Optional[str | Path] = None,
    suite: Optional[str] = None,
    date: Optional[str] = None,
    contact: Optional[str] = None,
    compute_notes: Optional[str] = None,
    wandb_report: Optional[str] = None,
) -> PackResult:
    from open_apps.alignment.record import RECORD_VERSION
    from open_apps.alignment.score import score

    logs_dirs = [Path(p) for p in logs_dirs]
    collected, skipped = collect(logs_dirs)
    if not collected:
        raise FileNotFoundError(
            f"no episode records under {[str(p) for p in logs_dirs]}"
        )

    suites = Counter(c.record.identity.suite_version for c in collected)
    suite = suite or suites.most_common(1)[0][0]
    if not suite:
        raise ValueError(
            "records carry no suite_version: the runs were not launched with "
            "benchmark=<suite> (or pass --suite to pick one)"
        )
    kept: list[Collected] = []
    for c in collected:
        if c.record.identity.suite_version != suite:
            skipped.append(
                (
                    c.exp_dir,
                    f"suite_version {c.record.identity.suite_version!r} != {suite!r}",
                )
            )
        else:
            kept.append(c)
    agents = Counter(c.record.identity.agent_name for c in kept)
    if len(agents) > 1:
        raise ValueError(
            f"records from several agents {dict(agents)}; pack one agent per bundle"
        )

    by_key: dict[tuple, Collected] = {}
    duplicates = []
    for c in kept:
        if c.key in by_key:
            winner = _prefer(by_key[c.key], c)
            loser = c if winner is by_key[c.key] else by_key[c.key]
            duplicates.append(
                (f"{c.key[0]} [{c.key[1]}] seed={c.key[2]}", loser.exp_dir)
            )
            by_key[c.key] = winner
        else:
            by_key[c.key] = c

    agent_cfg, redacted = _agent_config(list(by_key.values()))
    lines = []
    for key in sorted(by_key):
        record = by_key[key].record
        lines.append(record.to_dict() | {B.SCORES_KEY: score(record).summary()})

    out_dir = (
        Path(out) if out else B.submissions_dir() / B.bundle_name(org, agent, suite)
    )
    B.bundle_name(org, agent, suite)  # validates the slugs even with --out
    out_dir.mkdir(parents=True, exist_ok=True)
    B.write_yaml(agent_cfg, out_dir / B.AGENT_FILE)
    n = B.write_episodes(lines, out_dir / B.EPISODES_FILE)
    meta = {
        "meta_version": 1,
        "org": org,
        "agent": agent,
        "model": str(
            agent_cfg.get("model_name") or agent_cfg.get("model_pretty_name") or agent
        ),
        "suite": suite,
        "date": date or dt.date.today().isoformat(),
        "contact": contact,
        "agent_config_hash": B.agent_config_hash(agent_cfg),
        "redacted": redacted,
        "n_episodes": n,
        "record_version": RECORD_VERSION,
        "compute_notes": compute_notes,
        "wandb_report": wandb_report,
        "videos": [],
        "verified": False,
    }
    B.write_yaml(meta, out_dir / B.META_FILE)
    return PackResult(out=out_dir, n_episodes=n, skipped=skipped, duplicates=duplicates)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pack benchmark runs into a leaderboard bundle."
    )
    parser.add_argument(
        "logs_dirs", nargs="+", help="logs dir(s) of a benchmark=<suite> sweep"
    )
    parser.add_argument("--org", required=True, help="your org / team slug")
    parser.add_argument(
        "--agent", required=True, help="agent slug shown on the leaderboard"
    )
    parser.add_argument(
        "--out",
        default=None,
        help="bundle dir (default: leaderboard/submissions/<org>__<agent>__<suite>)",
    )
    parser.add_argument(
        "--suite",
        default=None,
        help="suite to pack (default: the records' suite_version)",
    )
    parser.add_argument(
        "--date", default=None, help="ISO date for meta.yaml (default: today)"
    )
    parser.add_argument("--contact", default=None)
    parser.add_argument("--compute-notes", default=None)
    parser.add_argument(
        "--wandb-report", default=None, help="public W&B report URL (optional)"
    )
    args = parser.parse_args(argv)
    try:
        result = pack(
            args.logs_dirs,
            org=args.org,
            agent=args.agent,
            out=args.out,
            suite=args.suite,
            date=args.date,
            contact=args.contact,
            compute_notes=args.compute_notes,
            wandb_report=args.wandb_report,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"pack failed: {exc}", file=sys.stderr)
        return 1
    for exp_dir, reason in result.skipped:
        print(f"skipped {exp_dir}: {reason}")
    for cell, loser in result.duplicates:
        print(f"duplicate {cell}: dropped {loser}")
    print(f"wrote {result.out} ({result.n_episodes} episodes)")
    print(f"next: uv run -m open_apps.leaderboard.validate {result.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
