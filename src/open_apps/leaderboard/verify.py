"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The verification tier, maintainer side: re-run a submission's ``agent.yaml``
on the suite's held-out seeds and record the summary in the maintainer-owned
``leaderboard/verifications.yaml``.

    uv run -m open_apps.leaderboard.verify plan <bundle>
    uv run -m open_apps.leaderboard.verify record <bundle> <heldout_logs_dir> [...]

``plan`` prints the commands that stage the submitted agent config and launch
the suite with ``benchmark.seed_set=heldout``, plus the held-out set's
``seeds_hash``. ``record`` packs the finished held-out run *in a temporary
directory* (the same ``pack`` code), validates it against the suite's cells
expanded with the held-out seeds (``validate --seed-set heldout``), computes
its aligned rate the way the leaderboard does, and writes one entry

    {bundle, episodes_sha256, agent_config_hash, date, seeds_hash, aligned, delta}

to ``leaderboard/verifications.yaml`` (``open_apps.leaderboard.verifications``)
-- ``aligned`` the held-out aligned rate and ``delta`` held-out minus public
aligned, both as rates in [0, 1] like every other rate in ``leaderboard.json``
(the page renders ``delta`` in points: -0.05 is "-5.0 points"). The bundle
itself is never touched: verified status is not the submitter's to carry, and
the sha256 and agent hash pin the entry to exactly the bundle verified.
Commit the file in a PR of its own (CI fails one that touches anything else;
CODEOWNERS requires a maintainer's review).

Held-out seeds are read where the sweep reads them (``$OPENAPPS_HELDOUT_SEEDS``
or the gitignored ``config/benchmark/internal-heldout_seeds.yaml``). Nothing
here prints or writes a seed, and nothing per-episode from the held-out run
reaches the repo: only the summary fields above. A seed that leaks is a
public seed, and the tier would stop measuring overfitting.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from open_apps.leaderboard import bundle as B
from open_apps.leaderboard import verifications as VF
from open_apps.leaderboard.suite import (
    HELDOUT_ENV,
    heldout_seeds_hash,
    load_heldout_seeds,
)
from open_apps.leaderboard.validate import OK, BundleReport, validate_bundle

#: Rates are stored to 4 decimals (0.01 points): enough to reproduce the
#: page's one-decimal display, without float noise in a reviewed diff.
DIGITS = 4


class VerifyError(RuntimeError):
    """A verification that cannot be recorded, with what to do about it."""


def _meta(bundle: Path) -> dict:
    if not B.is_bundle(bundle):
        raise VerifyError(f"{bundle} is not a bundle (no {B.META_FILE})")
    meta = B.read_yaml(bundle / B.META_FILE)
    if not isinstance(meta, dict) or not meta.get("suite"):
        raise VerifyError(f"{bundle / B.META_FILE} has no suite")
    return meta


def staging_dir(bundle: Path) -> Path:
    """Where ``plan`` stages the submitted agent config: under the gitignored
    ``log_outputs/``, never under ``config/``, so a maintainer's filled-in
    copy (with their own endpoint and key) cannot be committed by accident."""
    return B.repo_root() / "log_outputs" / "verify" / bundle.name


def _seed_source() -> str:
    """Which source the seeds come from, by kind only (no path, no seeds)."""
    import os

    return (
        f"${HELDOUT_ENV}" if os.environ.get(HELDOUT_ENV) else "the internal seeds file"
    )


def _salt_warning(salted: bool) -> Optional[str]:
    if salted:
        return None
    return (
        "warning: the held-out seeds file has no `salt`; a seeds_hash of a few small "
        "integers can be brute-forced back into the seeds. Add `salt: <random secret>` "
        "to it before publishing a verification (see heldout_seeds.example.yaml)."
    )


def plan(bundle: str | Path, mode: str = "<local|slurm_cluster>") -> str:
    """The commands that re-run ``bundle``'s agent on the held-out seeds.

    Raises ``FileNotFoundError`` when no held-out seed source is configured:
    a plan without seeds would launch the public seeds under a held-out name.
    """
    bundle = Path(bundle)
    meta = _meta(bundle)
    seeds = load_heldout_seeds()
    seeds_hash, salted = heldout_seeds_hash()
    suite = meta["suite"]
    name = bundle.name
    stage = staging_dir(bundle)
    redacted = list(meta.get("redacted") or [])
    lines = [
        f"Verify {name} on {suite}'s held-out seeds",
        f"  {len(seeds)} held-out seed(s) from {_seed_source()}; seeds_hash {seeds_hash}",
        "",
        "1. Stage the submitted agent config (outside config/, so it is never committed):",
        "",
        f"   mkdir -p {stage}/agent",
        f"   cp {bundle.resolve() / B.AGENT_FILE} {stage}/agent/{name}.yaml",
        "",
    ]
    if redacted:
        lines += [
            "   Then fill in its redacted values with your own (keys, endpoints): "
            + ", ".join(redacted),
            "",
        ]
    lines += [
        f"2. Run the suite on the held-out seeds (the same ${HELDOUT_ENV} or internal file",
        "   must be visible to the launcher):",
        "",
        f"   uv run launch_parallel_agents.py benchmark={suite} benchmark.seed_set=heldout \\",
        f'       "hydra.searchpath=[file://{stage}]" agent={name} mode={mode}',
        "",
        "3. Record the result in leaderboard/verifications.yaml (then commit that file alone):",
        "",
        f"   uv run -m open_apps.leaderboard.verify record {bundle} log_outputs/<timestamp>",
    ]
    warning = _salt_warning(salted)
    if warning:
        lines += ["", warning]
    return "\n".join(lines)


# --- record ------------------------------------------------------------------------


def _aligned(report: BundleReport) -> float:
    """The aligned rate exactly as the leaderboard computes it for a row."""
    from open_apps.leaderboard.build import ranked_row

    rate = ranked_row(report, n_boot=0)["aligned"]["rate"]
    if rate is None:
        raise VerifyError(f"{report.path.name}: no episode has an aligned verdict")
    return float(rate)


@dataclass
class Verification:
    verified: dict
    public_aligned: float
    n_heldout: int
    salted: bool
    path: Optional[Path] = None


def heldout_report(
    bundle: Path,
    logs_dirs: Iterable[str | Path],
    meta: dict,
    workdir: Path,
    suite: Optional[str | Path] = None,
) -> BundleReport:
    """Pack the held-out run into ``workdir`` (a temp dir) with ``pack``'s
    code and validate it against the held-out cells."""
    from open_apps.leaderboard.pack import assemble, collect

    logs_dirs = [Path(p) for p in logs_dirs]
    collected, skipped = collect(logs_dirs)
    if not collected:
        raise VerifyError(f"no episode records under {[str(p) for p in logs_dirs]}")
    out = workdir / bundle.name
    assemble(
        collected,
        skipped,
        org=meta["org"],
        agent=meta["agent"],
        out=out,
        suite=meta["suite"],
        date=str(meta.get("date") or ""),
    )
    return validate_bundle(out, suite=suite, seed_set="heldout")


def record(
    bundle: str | Path,
    logs_dirs: Iterable[str | Path],
    suite: Optional[str | Path] = None,
    date: Optional[str] = None,
    verifications: Optional[str | Path] = None,
) -> Verification:
    """Validate the held-out run, then write its entry to ``verifications``
    (default ``leaderboard/verifications.yaml``), replacing any earlier one
    for the bundle.

    Refuses (``VerifyError``) unless the public bundle and the held-out run
    both validate ``OK`` -- a partial held-out run would compare different
    cells -- and the held-out run used the submitted agent config up to its
    secrets. ``suite`` overrides where the suite is read from (tests).
    """
    bundle = Path(bundle)
    meta = _meta(bundle)
    seeds_hash, salted = heldout_seeds_hash()  # raises before any work

    public = validate_bundle(bundle, suite=suite)
    if public.status != OK:
        raise VerifyError(
            f"the public bundle must validate OK before it can be verified:\n{public.format()}"
        )
    with tempfile.TemporaryDirectory(prefix="openapps-verify-") as tmp:
        held = heldout_report(bundle, logs_dirs, meta, Path(tmp), suite=suite)
        if held.status != OK:
            # format() labels held-out seeds by position, never by value.
            raise VerifyError(
                "the held-out run must cover every held-out cell and validate OK:\n"
                + held.format()
            )
        ran = B.normalized_agent_hash(B.read_yaml(held.path / B.AGENT_FILE))
        submitted = B.normalized_agent_hash(B.read_yaml(bundle / B.AGENT_FILE))
        if ran != submitted:
            raise VerifyError(
                "the held-out run used a different agent config than the submitted "
                f"agent.yaml (normalized hashes {ran} vs {submitted}); only redacted "
                "values (keys, endpoints) may differ"
            )
        held_aligned = _aligned(held)
        n_heldout = held.n_records
    public_aligned = _aligned(public)

    entry = {
        "bundle": bundle.name,
        "episodes_sha256": VF.episodes_sha256(bundle),
        "agent_config_hash": str(meta["agent_config_hash"]),
        "date": date or dt.date.today().isoformat(),
        "seeds_hash": seeds_hash,
        "aligned": round(held_aligned, DIGITS),
        "delta": round(held_aligned - public_aligned, DIGITS),
    }
    path = VF.upsert(entry, Path(verifications) if verifications else None)
    verified = {k: entry[k] for k in VF.SHOWN}
    return Verification(verified, public_aligned, n_heldout, salted, path)


# --- CLI ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Maintainer tooling for the verified tier: re-run a submission on the "
            "held-out seeds and record the summary in leaderboard/verifications.yaml. Held-out seeds "
            f"come from ${HELDOUT_ENV} or the gitignored internal seeds file and are "
            "never printed or written."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_plan = sub.add_parser(
        "plan",
        help="print the commands that re-run a bundle's agent on the held-out seeds",
    )
    p_plan.add_argument("bundle", type=Path)
    p_plan.add_argument(
        "--mode", default="<local|slurm_cluster>", help="mode= for the launch command"
    )
    p_record = sub.add_parser(
        "record",
        help="validate a held-out run and write its entry to leaderboard/verifications.yaml",
    )
    p_record.add_argument("bundle", type=Path)
    p_record.add_argument(
        "logs_dirs", nargs="+", type=Path, help="the held-out run's logs dir(s)"
    )
    p_record.add_argument(
        "--date", default=None, help="ISO date to record (default: today)"
    )
    p_record.add_argument(
        "--verifications", type=Path, default=None,
        help="the verifications file to write (default: leaderboard/verifications.yaml)",
    )  # fmt: skip
    p_record.add_argument(
        "--suite",
        default=None,
        help="suite name or YAML path (default: from meta.yaml)",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            print(plan(args.bundle, mode=args.mode))
            return 0
        result = record(
            args.bundle,
            args.logs_dirs,
            suite=args.suite,
            date=args.date,
            verifications=args.verifications,
        )
    except (FileNotFoundError, ValueError, VerifyError) as exc:
        print(f"verify {args.command} failed: {exc}", file=sys.stderr)
        return 1
    v = result.verified
    print(
        f"verified {args.bundle.name}: held-out aligned {v['aligned'] * 100:.1f}% "
        f"vs public {result.public_aligned * 100:.1f}% "
        f"(delta {v['delta'] * 100:+.1f} points) over {result.n_heldout} held-out episodes; "
        f"seeds_hash {v['seeds_hash']}"
    )
    print(f"wrote {args.bundle.name} to {result.path} (commit it in a PR of its own)")
    warning = _salt_warning(result.salted)
    if warning:
        print(warning, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
