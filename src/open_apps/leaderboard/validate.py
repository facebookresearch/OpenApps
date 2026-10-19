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
  3. identity   one agent, every record run with ``benchmark=<suite>`` and
                the suite's ``task_termination`` (``on_agent_stop``)
  4. provenance each record's embedded task config, irreversible map, fired
                events (ids, categories, ``forbidden`` predicates, text) and
                adversarial flags against the repo's own config for its
                suite cell (``open_apps.leaderboard.reference``); any
                difference fails the bundle with the path that differs
  5. re-score   ``open_apps.alignment.score`` on every record *under the
                repo's config*, never the record's copies; any field that
                disagrees with the submitted ``scores`` fails the bundle
  6. coverage   every (task, variation, seed) cell of the suite present
                exactly once; missing cells => ``partial`` (listed, not
                ranked); duplicates or cells outside the suite => fail.
                An ``unscorable`` record (its final state could not be
                read) is a warning and covers nothing: its cell is missing.

A demonstration (``leaderboard/demonstrations/<contributor>__<topic>``,
``kind: demonstration`` in its meta.yaml) gets steps 1, 2 and 5 only -- its
own meta schema, the same episode schema, and a re-score of any ``scores`` it
carries (they are optional there) under its *embedded* config: it has no
suite cell to check that config against, which is why it is never ranked.

    uv run -m open_apps.leaderboard.validate leaderboard/demonstrations/<contributor>__<topic>

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
from open_apps.leaderboard.suite import (
    cell_key,
    load_suite,
    required_termination,
    suite_cells,
)

OK, PARTIAL, FAIL = "ok", "partial", "fail"
SUBMISSION, DEMONSTRATION = "submission", B.DEMO_KIND
# Enough to act on without drowning a PR comment.
_MAX_LISTED = 10


@dataclass
class BundleReport:
    path: Path
    bundle_id: str = ""
    kind: str = SUBMISSION
    suite: Optional[str] = None
    meta: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_records: int = 0
    n_expected: int = 0
    # Records whose final state could not be read (``EpisodeRecord.unscorable``).
    n_unscorable: int = 0
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
        covered = self.n_expected - len(self.missing)
        if self.kind == DEMONSTRATION:
            summary = f"        {self.n_records} episodes; demonstration (not ranked)"
        else:
            summary = (
                f"        {self.n_records} episodes; {covered}/{self.n_expected} "
                f"suite cells covered ({self.suite or '?'})"
            )
        if self.n_unscorable:
            summary += f"; {self.n_unscorable} unscorable"
        lines = [f"{self.status.upper():<7} {self.path.name}", summary]
        for msg in self.errors[:_MAX_LISTED]:
            lines.append(f"  error: {msg}")
        if len(self.errors) > _MAX_LISTED:
            lines.append(f"  error: ... and {len(self.errors) - _MAX_LISTED} more")
        for msg in self.warnings[:_MAX_LISTED]:
            lines.append(f"  warning: {msg}")
        if len(self.warnings) > _MAX_LISTED:
            lines.append(f"  warning: ... and {len(self.warnings) - _MAX_LISTED} more")
        for key in self.missing[:_MAX_LISTED]:
            task, cell, seed = key
            lines.append(f"  missing: {task} [{cell}] seed={seed}")
        if len(self.missing) > _MAX_LISTED:
            lines.append(f"  missing: ... and {len(self.missing) - _MAX_LISTED} more")
        return "\n".join(lines)


def _check_layout(
    path: Path,
    report: BundleReport,
    files: tuple[str, ...] = (B.META_FILE, B.AGENT_FILE, B.EPISODES_FILE),
) -> bool:
    for name in files:
        if not (path / name).is_file():
            report.errors.append(f"missing {name}")
    return not report.errors


def _load_meta(path: Path, report: BundleReport) -> Optional[dict]:
    try:
        meta = B.read_yaml(path / B.META_FILE)
    except Exception as exc:  # noqa: BLE001 -- any parse failure is the finding
        report.errors.append(f"{B.META_FILE} is not valid YAML: {exc}")
        return None
    if not isinstance(meta, dict):
        report.errors.append(f"{B.META_FILE} must be a mapping")
        return None
    # An unquoted `date: 2026-10-16` loads as a date; accept it as the
    # string it was meant to be rather than failing a hand-edited meta.
    meta = {
        k: v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v
        for k, v in meta.items()
    }
    if isinstance(meta.get("verified"), dict):
        meta["verified"] = {
            k: v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v
            for k, v in meta["verified"].items()
        }
    report.meta = meta
    return meta


def _check_meta(path: Path, report: BundleReport) -> None:
    meta = _load_meta(path, report)
    if meta is None:
        return
    for err in B.schema_errors(meta, B.load_schema("meta.schema.json")):
        report.errors.append(f"{B.META_FILE}: {err}")
    report.suite = meta.get("suite")
    report.bundle_id = B.BUNDLE_SEP.join([str(meta.get("org")), str(meta.get("agent"))])
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


def _check_demo_meta(path: Path, report: BundleReport) -> None:
    meta = _load_meta(path, report)
    if meta is None:
        return
    for err in B.schema_errors(meta, B.load_schema("demonstration.schema.json")):
        report.errors.append(f"{B.META_FILE}: {err}")
    report.bundle_id = path.name
    try:
        contributor, topic = B.parse_demo_name(path.name)
    except ValueError as exc:
        report.errors.append(str(exc))
        return
    if (contributor, topic) != (meta.get("contributor"), meta.get("topic")):
        report.errors.append(
            f"dir name {path.name!r} does not match meta contributor/topic "
            f"({meta.get('contributor')}/{meta.get('topic')})"
        )


def _check_agent(path: Path, report: BundleReport) -> None:
    if report.kind == DEMONSTRATION:
        if not (path / B.AGENT_FILE).is_file():
            return  # optional for demonstrations (a person has no config)
        if not report.meta.get("agent_config_hash"):
            report.errors.append(
                f"{B.AGENT_FILE} is present: meta.yaml needs its agent_config_hash"
            )
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


def _check_episodes(
    path: Path, report: BundleReport, require_scores: bool = True
) -> list[tuple[Any, Optional[dict]]]:
    """Schema-check and parse every line; return ``(record, submitted_scores)``
    (``None`` scores only when not ``require_scores``)."""
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
        if not isinstance(submitted, dict) and (
            require_scores or submitted is not None
        ):
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


def _label(record: Any) -> str:
    ident = record.identity
    return f"{ident.task_name} [{'|'.join(ident.variation) or 'default'}] seed={ident.seed}"


def _score_one(record: Any, submitted: Optional[dict], report: BundleReport) -> None:
    """Score ``record`` as given, check the submitted scores, keep its row."""
    from open_apps.alignment.aggregate import row_from_record
    from open_apps.alignment.score import score

    label = _label(record)
    try:
        scores = score(record)
    except Exception as exc:  # noqa: BLE001 -- a record the scorer rejects fails the bundle
        report.errors.append(f"{label}: scorer failed ({type(exc).__name__}: {exc})")
        return
    # Demonstrations may omit scores; scoring still has to succeed.
    diffs = B.scores_diff(submitted, scores.summary()) if submitted is not None else []
    if diffs:
        report.errors.append(
            f"{label}: scores disagree with the scorer -- " + "; ".join(diffs[:4])
        )
        return
    if record.unscorable:
        report.n_unscorable += 1
        report.warnings.append(
            f"{label}: unscorable (final state unreadable for {sorted(record.unscorable)}); "
            "it covers no cell -- re-run it"
        )
    row = row_from_record(record, scores)
    row["agent"] = report.bundle_id
    report.rows.append(row)
    report.records.append(record)


def _rescore(parsed: list[tuple[Any, Optional[dict]]], report: BundleReport) -> None:
    """Re-score under each record's *embedded* config (demonstrations)."""
    for record, submitted in parsed:
        _score_one(record, submitted, report)


def _rescore_against_suite(
    parsed: list[tuple[Any, dict]], report: BundleReport, source: Any, cells: dict
) -> None:
    """Check each record's embedded config against the repo's for its suite
    cell, then re-score it under the repo's (``reference.rebased``)."""
    from open_apps.leaderboard.reference import compare, rebased, reference_cell

    for record, submitted in parsed:
        ident = record.identity
        cell = cells.get(cell_key(ident.task_name, ident.variation, ident.seed))
        if cell is None:
            continue  # outside the suite: _check_coverage fails it
        label = _label(record)
        try:
            ref = reference_cell(source, cell.task_name, cell.variation, cell.seed)
        except Exception as exc:  # noqa: BLE001 -- a cell the repo cannot compose is the finding
            report.errors.append(
                f"{label}: cannot compose this cell from the repo's config "
                f"({type(exc).__name__}: {exc})"
            )
            continue
        errors, warnings = compare(record, ref)
        report.warnings += [f"{label}: {w}" for w in warnings]
        if errors:
            more = f"; ... and {len(errors) - 4} more" if len(errors) > 4 else ""
            report.errors.append(
                f"{label}: record differs from the repo's config for this cell -- "
                + "; ".join(errors[:4])
                + more
            )
            continue
        _score_one(rebased(record, ref), submitted, report)


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


def _check_termination(
    parsed: list[tuple[Any, dict]], report: BundleReport, source: Any
) -> None:
    """Every record under the suite's pinned ``task_termination``. Under
    ``on_success`` honesty and premature stop are unmeasurable, and the
    aggregate keeps the two modes apart -- a mixed bundle has no single row."""
    want = required_termination(source)
    modes = Counter(r.identity.task_termination for r, _ in parsed)
    wrong = {m: n for m, n in modes.items() if m != want}
    if wrong:
        report.errors.append(
            f"every record must run with task_termination={want} ({report.suite} pins it; "
            f"benchmark={report.suite} sets it): found {dict(sorted(wrong.items()))}"
        )


def _load_cells(report: BundleReport, suite: Optional[str | Path]) -> tuple[Any, dict]:
    """``(suite source, {cell key: Cell})`` for the public seeds, or
    ``(None, {})`` when the suite cannot be read (reported)."""
    source = suite or report.suite
    if not source:
        return None, {}
    try:
        bench = load_suite(source)
    except (FileNotFoundError, ValueError) as exc:
        report.errors.append(f"cannot load suite {source!r}: {exc}")
        return None, {}
    if str(bench.name) != report.suite:
        report.errors.append(
            f"suite file is {bench.name!r}, meta says {report.suite!r}"
        )
        return None, {}
    cells = {c.key: c for c in suite_cells(bench, "public")}
    report.n_expected = len(cells)
    return source, cells


def _check_coverage(
    parsed: list[tuple[Any, dict]], report: BundleReport, cells: dict
) -> None:
    if not cells:
        return
    expected = set(cells)
    keys = [
        (cell_key(r.identity.task_name, r.identity.variation, r.identity.seed), r)
        for r, _ in parsed
    ]
    got = Counter(key for key, _ in keys)
    for key, n in sorted(got.items()):
        if n > 1:
            report.errors.append(
                f"cell submitted {n} times: {key[0]} [{key[1]}] seed={key[2]}"
            )
    extra = sorted(set(got) - expected)
    for key in extra[:_MAX_LISTED]:
        report.errors.append(
            f"not a {report.suite} cell: {key[0]} [{key[1]}] seed={key[2]}"
        )
    if len(extra) > _MAX_LISTED:
        report.errors.append(
            f"... and {len(extra) - _MAX_LISTED} more cells outside the suite"
        )
    # An unscorable record is not evidence of anything the agent did.
    covered = {key for key, record in keys if not record.unscorable}
    report.missing = sorted(expected - covered)


def validate_bundle(
    path: str | Path, suite: Optional[str | Path] = None
) -> BundleReport:
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
    source, cells = _load_cells(report, suite)
    if source is not None:
        _check_termination(parsed, report, source)
    _rescore_against_suite(parsed, report, source, cells)
    _check_coverage(parsed, report, cells)
    return report


def validate_demonstration(path: str | Path) -> BundleReport:
    """Layout, meta, optional agent.yaml, episode schema and (optional)
    scores -- the submission checks minus coverage and suite identity."""
    path = Path(path)
    report = BundleReport(path=path, bundle_id=path.name, kind=DEMONSTRATION)
    if not _check_layout(path, report, (B.META_FILE, B.EPISODES_FILE)):
        return report
    _check_demo_meta(path, report)
    _check_agent(path, report)
    parsed = _check_episodes(path, report, require_scores=False)
    if report.meta.get("n_episodes") not in (None, len(parsed)):
        report.errors.append(
            f"meta n_episodes={report.meta.get('n_episodes')} but {len(parsed)} valid records"
        )
    agents = {r.identity.agent_name for r, _ in parsed} - {
        None,
        report.meta.get("agent"),
    }
    if agents:
        report.warnings.append(
            f"records name agent(s) {sorted(agents)}, meta says {report.meta.get('agent')!r}"
        )
    _rescore(parsed, report)
    return report


def is_demonstration(path: str | Path) -> bool:
    try:
        meta = B.read_yaml(Path(path) / B.META_FILE)
    except Exception:  # noqa: BLE001 -- validate_bundle reports a broken meta.yaml
        return False
    return isinstance(meta, dict) and meta.get("kind") == B.DEMO_KIND


def validate_path(path: str | Path, suite: Optional[str | Path] = None) -> BundleReport:
    """A submission or a demonstration, by its meta.yaml ``kind``."""
    return (
        validate_demonstration(path)
        if is_demonstration(path)
        else validate_bundle(path, suite)
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate OpenApps leaderboard bundles (schema, re-score, coverage) "
            "and demonstrations (schema, re-score)."
        )
    )
    parser.add_argument(
        "paths", nargs="+",
        help="bundle or demonstration dirs, or dirs containing them",
    )  # fmt: skip
    parser.add_argument(
        "--suite",
        default=None,
        help="suite name or YAML path (default: from meta.yaml)",
    )
    parser.add_argument(
        "--strict", action="store_true", help="treat partial as failure"
    )
    args = parser.parse_args(argv)

    bundles = B.find_bundles(args.paths)
    if not bundles:
        print(
            f"no bundles (directories with {B.META_FILE}) under {args.paths}",
            file=sys.stderr,
        )
        return 1
    reports = [validate_path(p, args.suite) for p in bundles]
    for r in reports:
        print(r.format())
    bad = [
        r for r in reports if r.status == FAIL or (args.strict and r.status == PARTIAL)
    ]
    counts = Counter(r.status for r in reports)
    print(
        f"\n{len(reports)} bundle(s): "
        + ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
