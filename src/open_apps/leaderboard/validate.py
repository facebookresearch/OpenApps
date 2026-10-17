"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Validate leaderboard submission bundles -- the same checks CI runs.

    uv run -m open_apps.leaderboard.validate leaderboard/submissions/<org>__<agent>__align_v1
    uv run -m open_apps.leaderboard.validate leaderboard/submissions/      # every bundle

In order:
  1. layout     meta.yaml, agent.yaml, episodes.jsonl.gz; dir name matches meta
  2. schema     meta against meta.schema.json; every line against
                episode.schema.json (+ a ``scores`` object); agent.yaml hash
  3. re-score   ``open_apps.alignment.score`` on every record; any field that
                disagrees with the submitted ``scores`` fails the bundle
  4. coverage   every (task, variation, seed) cell of the suite present
                exactly once; missing cells => ``partial`` (listed, not
                ranked); duplicates or cells outside the suite => fail

Exit status is non-zero if any bundle fails (``--strict``: or is partial).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from open_apps.leaderboard import bundle as B
from open_apps.leaderboard.suite import cell_key, load_suite, suite_cells

OK, PARTIAL, FAIL = "ok", "partial", "fail"
# Enough to act on without drowning a PR comment.
_MAX_LISTED = 10


@dataclass
class BundleReport:
    path: Path
    bundle_id: str = ""
    suite: Optional[str] = None
    meta: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_records: int = 0
    n_expected: int = 0
    missing: list[tuple] = field(default_factory=list)
    # Aggregation rows built from the *re-scored* records (see
    # ``alignment.aggregate.row_from_record``), keyed by bundle id; plus the
    # records themselves, for leaderboard columns the scorer does not emit.
    rows: list[dict] = field(default_factory=list)
    records: list[Any] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors:
            return FAIL
        return PARTIAL if self.missing else OK

    def format(self) -> str:
        head = f"{self.status.upper():<7} {self.path.name}"
        lines = [
            head,
            f"        {self.n_records} episodes; {self.n_expected - len(self.missing)}"
            f"/{self.n_expected} suite cells covered ({self.suite or '?'})",
        ]
        for msg in self.errors[:_MAX_LISTED]:
            lines.append(f"  error: {msg}")
        if len(self.errors) > _MAX_LISTED:
            lines.append(f"  error: ... and {len(self.errors) - _MAX_LISTED} more")
        for msg in self.warnings[:_MAX_LISTED]:
            lines.append(f"  warning: {msg}")
        for key in self.missing[:_MAX_LISTED]:
            task, cell, seed = key
            lines.append(f"  missing: {task} [{cell}] seed={seed}")
        if len(self.missing) > _MAX_LISTED:
            lines.append(f"  missing: ... and {len(self.missing) - _MAX_LISTED} more")
        return "\n".join(lines)


def _check_layout(path: Path, report: BundleReport) -> bool:
    for name in (B.META_FILE, B.AGENT_FILE, B.EPISODES_FILE):
        if not (path / name).is_file():
            report.errors.append(f"missing {name}")
    return not report.errors


def _check_meta(path: Path, report: BundleReport) -> None:
    try:
        meta = B.read_yaml(path / B.META_FILE)
    except Exception as exc:  # noqa: BLE001 -- any parse failure is the finding
        report.errors.append(f"{B.META_FILE} is not valid YAML: {exc}")
        return
    if not isinstance(meta, dict):
        report.errors.append(f"{B.META_FILE} must be a mapping")
        return
    # An unquoted `date: 2026-10-16` loads as a date; accept it as the
    # string it was meant to be rather than failing a hand-edited meta.
    meta = {k: v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v for k, v in meta.items()}
    if isinstance(meta.get("verified"), dict):
        meta["verified"] = {
            k: v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v
            for k, v in meta["verified"].items()
        }
    report.meta = meta
    for err in B.schema_errors(meta, B.load_schema("meta.schema.json")):
        report.errors.append(f"{B.META_FILE}: {err}")
    report.suite = meta.get("suite")
    report.bundle_id = B.BUNDLE_SEP.join(
        [str(meta.get("org")), str(meta.get("agent"))]
    )
    try:
        org, agent, suite = B.parse_bundle_name(path.name)
    except ValueError as exc:
        report.errors.append(str(exc))
        return
    if (org, agent, suite) != (meta.get("org"), meta.get("agent"), meta.get("suite")):
        report.errors.append(
            f"dir name {path.name!r} does not match meta org/agent/suite "
            f"({meta.get('org')}/{meta.get('agent')}/{meta.get('suite')})"
        )


def _check_agent(path: Path, report: BundleReport) -> None:
    try:
        agent_cfg = B.read_yaml(path / B.AGENT_FILE)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"{B.AGENT_FILE} is not valid YAML: {exc}")
        return
    if not isinstance(agent_cfg, dict):
        report.errors.append(f"{B.AGENT_FILE} must be a mapping")
        return
    want = report.meta.get("agent_config_hash")
    got = B.agent_config_hash(agent_cfg)
    if want and want != got:
        report.errors.append(
            f"{B.AGENT_FILE} hashes to {got}, meta says {want} (edited after packing?)"
        )
    _, leaked = B.redact(agent_cfg)
    if leaked:
        report.errors.append(
            f"{B.AGENT_FILE} has unredacted secret-looking values at {leaked}; "
            "re-pack, or replace them with <redacted>"
        )


def _check_episodes(path: Path, report: BundleReport) -> list[tuple[Any, dict]]:
    """Schema-check and parse every line; return ``(record, submitted_scores)``."""
    from open_apps.alignment.record import EpisodeRecord

    schema = B.episode_schema()
    parsed = []
    try:
        lines = list(B.iter_episodes(path / B.EPISODES_FILE))
    except (OSError, EOFError) as exc:
        report.errors.append(f"{B.EPISODES_FILE} is not a readable gzip file: {exc}")
        return parsed
    for line_no, data in lines:
        where = f"{B.EPISODES_FILE}:{line_no}"
        if isinstance(data, Exception):
            report.errors.append(f"{where}: not JSON ({data})")
            continue
        if not isinstance(data, dict):
            report.errors.append(f"{where}: expected an object")
            continue
        data = dict(data)
        submitted = data.pop(B.SCORES_KEY, None)
        if not isinstance(submitted, dict):
            report.errors.append(f"{where}: missing the {B.SCORES_KEY!r} object")
            continue
        errs = B.schema_errors(data, schema)
        if errs:
            report.errors += [f"{where}: {e}" for e in errs[:3]]
            continue
        try:
            record = EpisodeRecord.from_dict(data)
        except (ValueError, TypeError) as exc:
            report.errors.append(f"{where}: {exc}")
            continue
        parsed.append((record, submitted))
    report.n_records = len(parsed)
    return parsed


def _rescore(parsed: list[tuple[Any, dict]], report: BundleReport) -> None:
    from open_apps.alignment.aggregate import row_from_record
    from open_apps.alignment.score import score

    for record, submitted in parsed:
        ident = record.identity
        label = f"{ident.task_name} [{'|'.join(ident.variation) or 'default'}] seed={ident.seed}"
        try:
            scores = score(record)
        except Exception as exc:  # noqa: BLE001 -- an unscorable record fails the bundle
            report.errors.append(f"{label}: scorer failed ({type(exc).__name__}: {exc})")
            continue
        diffs = B.scores_diff(submitted, scores.summary())
        if diffs:
            report.errors.append(f"{label}: scores disagree with the scorer -- " + "; ".join(diffs[:4]))
            continue
        row = row_from_record(record, scores)
        row["agent"] = report.bundle_id
        report.rows.append(row)
        report.records.append(record)


def _check_identity(parsed: list[tuple[Any, dict]], report: BundleReport) -> None:
    agents = Counter(r.identity.agent_name for r, _ in parsed)
    if len(agents) > 1:
        report.errors.append(f"one agent per bundle; found {dict(agents)}")
    suites = Counter(r.identity.suite_version for r, _ in parsed)
    wrong = {s: n for s, n in suites.items() if s != report.suite}
    if wrong:
        report.errors.append(
            f"records not run with benchmark={report.suite}: suite_version counts {wrong}"
        )
    if report.meta.get("n_episodes") not in (None, len(parsed)):
        report.errors.append(
            f"meta n_episodes={report.meta.get('n_episodes')} but {len(parsed)} valid records"
        )


def _check_coverage(
    parsed: list[tuple[Any, dict]], report: BundleReport, suite: Optional[str | Path]
) -> None:
    source = suite or report.suite
    if not source:
        return
    try:
        bench = load_suite(source)
    except (FileNotFoundError, ValueError) as exc:
        report.errors.append(f"cannot load suite {source!r}: {exc}")
        return
    if str(bench.name) != report.suite:
        report.errors.append(f"suite file is {bench.name!r}, meta says {report.suite!r}")
        return
    expected = {c.key for c in suite_cells(bench, "public")}
    report.n_expected = len(expected)
    got = Counter(
        cell_key(r.identity.task_name, r.identity.variation, r.identity.seed)
        for r, _ in parsed
    )
    for key, n in sorted(got.items()):
        if n > 1:
            report.errors.append(f"cell submitted {n} times: {key[0]} [{key[1]}] seed={key[2]}")
    extra = sorted(set(got) - expected)
    for key in extra[:_MAX_LISTED]:
        report.errors.append(f"not a {report.suite} cell: {key[0]} [{key[1]}] seed={key[2]}")
    if len(extra) > _MAX_LISTED:
        report.errors.append(f"... and {len(extra) - _MAX_LISTED} more cells outside the suite")
    report.missing = sorted(expected - set(got))


def validate_bundle(path: str | Path, suite: Optional[str | Path] = None) -> BundleReport:
    """Run every check on one bundle. ``suite`` overrides where the suite is
    read from (default: ``config/benchmark/<meta.suite>.yaml``)."""
    path = Path(path)
    report = BundleReport(path=path, bundle_id=path.name)
    if not _check_layout(path, report):
        return report
    _check_meta(path, report)
    _check_agent(path, report)
    parsed = _check_episodes(path, report)
    _check_identity(parsed, report)
    _rescore(parsed, report)
    _check_coverage(parsed, report, suite)
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate OpenApps leaderboard bundles (schema, re-score, coverage)."
    )
    parser.add_argument("paths", nargs="+", help="bundle dirs, or dirs containing bundles")
    parser.add_argument(
        "--suite", default=None, help="suite name or YAML path (default: from meta.yaml)"
    )
    parser.add_argument("--strict", action="store_true", help="treat partial as failure")
    args = parser.parse_args(argv)

    bundles = B.find_bundles(args.paths)
    if not bundles:
        print(f"no bundles (directories with {B.META_FILE}) under {args.paths}", file=sys.stderr)
        return 1
    reports = [validate_bundle(p, args.suite) for p in bundles]
    for r in reports:
        print(r.format())
    bad = [r for r in reports if r.status == FAIL or (args.strict and r.status == PARTIAL)]
    counts = Counter(r.status for r in reports)
    print(f"\n{len(reports)} bundle(s): " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
