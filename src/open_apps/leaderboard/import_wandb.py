"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Import a ``benchmark=<suite>`` sweep from W&B into a leaderboard bundle.

    uv run -m open_apps.leaderboard.import_wandb <entity>/<project> \\
        --group <group> [--tag <tag> ...] [--run-ids ID ...] \\
        --org my-lab --agent my-agent [--out DIR] [--report-url https://...] \\
        [--base-url https://<your-wandb-host>]

For runs whose logs dirs are gone (or live on someone else's cluster) but
whose W&B runs remain. Each run launched with ``use_wandb=True`` and
``record_alignment`` on (the default) logs its ``EpisodeRecord`` as an
``episode_record`` artifact (``AgentLauncher._log_alignment_to_wandb``). This
downloads those artifacts and hands the records to the same code ``pack``
uses (``pack.assemble``): suite selection, re-run dedupe, agent-config
redaction, scoring and the bundle layout are identical either way.

What is deliberately *not* read from W&B:

* the ``align/*`` summary keys, or any other summary value. Every record is
  re-scored locally with ``open_apps.alignment.score`` -- a summary is
  editable after the fact, and CI re-scores anyway;
* credentials. The ``wandb`` client reads ``WANDB_API_KEY`` /
  ``WANDB_BASE_URL`` (or ``~/.netrc``) from the submitter's environment; this
  module never prints, logs or writes them, nor the host it talked to.

The agent config comes from the run's config (``agent``), redacted exactly
as ``pack`` redacts a job's ``config.yaml``.

W&B is an ingestion path, never the source of truth: runs can be edited or
deleted and instances are not publicly readable, so the bundle in the repo is
what CI checks and the leaderboard ranks.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence, TypeVar

from open_apps.leaderboard.pack import Collected, PackResult, assemble

#: ``wandb.Artifact(type=...)`` in ``AgentLauncher._log_alignment_to_wandb``.
ARTIFACT_TYPE = "episode_record"
NO_ARTIFACT = (
    f"no {ARTIFACT_TYPE!r} artifact (recorded before record_alignment existed, "
    "or with record_alignment=false)"
)
#: Runs per page when listing. W&B's default is 50; bigger pages mean fewer
#: requests against the rate limit for a ~150-run suite.
PER_PAGE = 100
MAX_RETRIES = 5
BASE_DELAY_S = 2.0
MAX_DELAY_S = 60.0

T = TypeVar("T")


@dataclass
class ImportResult(PackResult):
    n_runs: int = 0
    # (run path, reason) for runs left out before any record was read.
    skipped_runs: list[tuple[str, str]] = field(default_factory=list)


# --- transport ---------------------------------------------------------------------


def _http_status(exc: BaseException) -> Optional[int]:
    """The HTTP status behind ``exc``, if any. ``wandb.errors.CommError``
    wraps the ``requests`` error in ``.exc``."""
    for candidate in (exc, getattr(exc, "exc", None), exc.__cause__):
        response = getattr(candidate, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    return None


def is_transient(exc: BaseException) -> bool:
    """Worth retrying: a rate limit (429), a server error (5xx), or a
    connection problem with no HTTP status at all. A 401/403/404 is not --
    retrying a bad key or a wrong project only delays the real error."""
    status = _http_status(exc)
    if status is not None:
        return status == 429 or status >= 500
    import requests  # a wandb dependency

    network = (
        requests.ConnectionError,
        requests.Timeout,
        ConnectionError,
        TimeoutError,
    )
    # A bare CommError (no status, no network cause) is usually "no such
    # project" or "permission denied": not transient. wandb's own client has
    # already retried what it considers retryable by the time one surfaces.
    return any(
        isinstance(candidate, network)
        for candidate in (exc, getattr(exc, "exc", None), exc.__cause__)
    )


def with_backoff(
    fn: Callable[[], T],
    what: str,
    retries: int = MAX_RETRIES,
    base_delay: float = BASE_DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """``fn()``, retried with exponential backoff on :func:`is_transient`
    errors. Only the exception type and status are printed: an error's text
    can carry request details, and nothing about the connection belongs in a
    log a submitter might paste into a PR."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 -- filtered by is_transient
            if attempt == retries or not is_transient(exc):
                raise
            delay = min(base_delay * 2**attempt, MAX_DELAY_S)
            status = _http_status(exc)
            why = f"HTTP {status}" if status else type(exc).__name__
            print(
                f"{what}: {why}; retry {attempt + 1}/{retries} in {delay:.0f}s",
                file=sys.stderr,
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def make_api(base_url: Optional[str] = None) -> Any:
    """A ``wandb.Api`` on the submitter's own credentials (environment or
    netrc). ``base_url`` overrides ``WANDB_BASE_URL`` for this process only;
    nothing is saved."""
    import wandb

    return wandb.Api(overrides={"base_url": base_url} if base_url else None, timeout=60)


# --- runs --------------------------------------------------------------------------


def check_path(path: str) -> str:
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"expected <entity>/<project>, got {path!r}")
    return path


def run_filters(group: Optional[str], tags: Sequence[str]) -> dict:
    """Server-side filters (W&B's MongoDB-style query): the run is in
    ``group`` and carries every tag."""
    clauses: list[dict] = []
    if group:
        clauses.append({"group": group})
    clauses += [{"tags": {"$in": [tag]}} for tag in tags]
    if not clauses:
        return {}
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _matches(run: Any, group: Optional[str], tags: Sequence[str]) -> bool:
    """The same filter, client-side: what is imported must not depend on
    the server honouring the query (or on an explicit ``--run-ids`` list
    that strays outside the group)."""
    if group and getattr(run, "group", None) != group:
        return False
    return set(tags) <= set(getattr(run, "tags", None) or [])


def find_runs(
    api: Any,
    path: str,
    group: Optional[str] = None,
    tags: Sequence[str] = (),
    run_ids: Sequence[str] = (),
    sleep: Callable[[float], None] = time.sleep,
) -> list[Any]:
    """Matching runs, oldest first, each once.

    ``api.runs`` pages lazily, and a page fetch that fails mid-iteration
    leaves the paginator in an undefined place, so a transient failure
    restarts the whole listing (it is metadata only). Runs are keyed by id,
    so a run that shifts across a page boundary while new runs are created
    is still taken once.
    """
    if run_ids:
        runs, unknown = [], []
        for rid in run_ids:
            try:
                runs.append(
                    with_backoff(
                        lambda rid=rid: api.run(f"{path}/{rid}"),
                        f"run {rid}",
                        sleep=sleep,
                    )
                )
            except Exception as exc:  # noqa: BLE001 -- wandb raises several types for "no such run"
                if is_transient(exc):
                    raise  # retries exhausted: not the run's fault
                unknown.append(rid)
        if unknown:
            raise ValueError(
                f"run id(s) not found or not readable in {path}: {unknown}"
            )
    else:
        filters = run_filters(group, tags)
        runs = with_backoff(
            lambda: list(api.runs(path, filters=filters, per_page=PER_PAGE)),
            f"listing runs in {path}",
            sleep=sleep,
        )
    unique = {run.id: run for run in runs}
    return [run for run in unique.values() if _matches(run, group, tags)]


def _recency(run: Any) -> tuple:
    """Last activity (heartbeat), else start time, then the id as a
    deterministic tie-break -- the W&B analogue of pack's exp_dir mtime. ISO
    timestamps from one server sort as strings."""
    when = getattr(run, "heartbeat_at", None) or getattr(run, "created_at", None)
    return (str(when or ""), str(run.id))


def episode_artifact(run: Any, sleep: Callable[[float], None] = time.sleep) -> Any:
    """The run's latest ``episode_record`` artifact, or None."""
    artifacts = with_backoff(
        lambda: list(run.logged_artifacts()), f"artifacts of run {run.id}", sleep=sleep
    )
    matching = [a for a in artifacts if getattr(a, "type", None) == ARTIFACT_TYPE]
    return matching[-1] if matching else None


def load_artifact_record(
    artifact: Any, sleep: Callable[[float], None] = time.sleep
) -> Any:
    """Download ``artifact`` into a temp dir and load its ``episode.json``.
    Nothing from W&B stays on disk afterwards except what the bundle keeps."""
    from open_apps.alignment.record import EPISODE_FILENAME, load_record

    with tempfile.TemporaryDirectory(prefix="openapps-wandb-") as tmp:
        root = Path(
            with_backoff(
                lambda: artifact.download(root=tmp),
                f"downloading {getattr(artifact, 'name', 'artifact')}",
                sleep=sleep,
            )
            or tmp
        )
        found = sorted(root.rglob(EPISODE_FILENAME)) or sorted(root.rglob("*.json"))
        if len(found) != 1:
            raise FileNotFoundError(
                f"expected one {EPISODE_FILENAME} in the artifact, found {len(found)}"
            )
        return load_record(found[0])


def collect_runs(
    api: Any,
    path: str,
    runs: Iterable[Any],
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[Collected], list[tuple[str, str]]]:
    """One :class:`Collected` per run with a loadable record; ``(run path,
    reason)`` for every run left out."""
    collected: list[Collected] = []
    skipped: list[tuple[str, str]] = []
    for run in runs:
        where = f"{path}/{run.id}"
        if getattr(run, "state", None) == "running":
            skipped.append(
                (where, "still running (its record may not be uploaded yet)")
            )
            continue
        agent_cfg = (getattr(run, "config", None) or {}).get("agent")
        if not isinstance(agent_cfg, dict):
            skipped.append(
                (where, "run config has no `agent` (not an OpenApps launcher run?)")
            )
            continue
        try:
            artifact = episode_artifact(run, sleep=sleep)
            if artifact is None:
                skipped.append((where, NO_ARTIFACT))
                continue
            record = load_artifact_record(artifact, sleep=sleep)
        except Exception as exc:  # noqa: BLE001 -- one bad run must not sink the import
            skipped.append((where, f"{type(exc).__name__}: {exc}"))
            continue
        collected.append(
            Collected(
                record,
                origin=where,
                source="wandb",
                recency=_recency(run),
                agent_config=agent_cfg,
            )
        )
    return collected, skipped


def import_wandb(
    path: str,
    org: str,
    agent: str,
    group: Optional[str] = None,
    tags: Sequence[str] = (),
    run_ids: Sequence[str] = (),
    out: Optional[str | Path] = None,
    suite: Optional[str] = None,
    date: Optional[str] = None,
    contact: Optional[str] = None,
    compute_notes: Optional[str] = None,
    report_url: Optional[str] = None,
    base_url: Optional[str] = None,
    api: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ImportResult:
    """W&B runs -> a bundle, through ``pack.assemble``. ``api`` is injectable
    (tests pass a fake); by default it is :func:`make_api`."""
    path = check_path(path)
    if not (group or tags or run_ids):
        raise ValueError(
            "pick the runs: --group, --tag or --run-ids (a whole project mixes "
            "agents and suites)"
        )
    api = api if api is not None else make_api(base_url)
    runs = find_runs(api, path, group=group, tags=tags, run_ids=run_ids, sleep=sleep)
    if not runs:
        raise FileNotFoundError(
            f"no runs in {path} match {run_filters(group, tags) or run_ids}"
        )
    collected, skipped_runs = collect_runs(api, path, runs, sleep=sleep)
    if not collected:
        reasons = sorted({reason for _, reason in skipped_runs})
        raise FileNotFoundError(
            f"none of the {len(runs)} matching run(s) has a usable episode record: {reasons}"
        )
    result = assemble(
        collected,
        [],
        org=org,
        agent=agent,
        out=out,
        suite=suite,
        date=date,
        contact=contact,
        compute_notes=compute_notes,
        wandb_report=report_url,
    )
    return ImportResult(
        out=result.out,
        n_episodes=result.n_episodes,
        skipped=result.skipped,
        duplicates=result.duplicates,
        n_runs=len(runs),
        skipped_runs=skipped_runs,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import benchmark runs from W&B into a leaderboard bundle. Downloads each "
            "run's episode_record artifact and re-scores it locally; W&B summary "
            "values are never used. Credentials come from your environment "
            "(WANDB_API_KEY, WANDB_BASE_URL or ~/.netrc) and are never written."
        )
    )
    parser.add_argument("path", metavar="ENTITY/PROJECT", help="the W&B project")
    parser.add_argument("--group", default=None, help="W&B run group of the sweep")
    parser.add_argument(
        "--tag", action="append", default=[], dest="tags", metavar="TAG",
        help="only runs with this tag (repeatable: runs must have every tag)",
    )  # fmt: skip
    parser.add_argument(
        "--run-ids", nargs="+", default=[], metavar="ID",
        help="explicit run ids (still filtered by --group/--tag when given)",
    )  # fmt: skip
    parser.add_argument("--org", required=True, help="your org / team slug")
    parser.add_argument(
        "--agent", required=True, help="agent slug shown on the leaderboard"
    )
    parser.add_argument(
        "--out", default=None,
        help="bundle dir (default: leaderboard/submissions/<org>__<agent>__<suite>)",
    )  # fmt: skip
    parser.add_argument(
        "--suite",
        default=None,
        help="suite to import (default: the records' suite_version)",
    )
    parser.add_argument(
        "--date", default=None, help="ISO date for meta.yaml (default: today)"
    )
    parser.add_argument("--contact", default=None)
    parser.add_argument("--compute-notes", default=None)
    parser.add_argument(
        "--report-url", default=None,
        help="public W&B report URL for meta.yaml's wandb_report (optional)",
    )  # fmt: skip
    parser.add_argument(
        "--base-url", default=None,
        help="W&B server for this run only (default: $WANDB_BASE_URL, else api.wandb.ai)",
    )  # fmt: skip
    args = parser.parse_args(argv)
    try:
        result = import_wandb(
            args.path,
            org=args.org,
            agent=args.agent,
            group=args.group,
            tags=args.tags,
            run_ids=args.run_ids,
            out=args.out,
            suite=args.suite,
            date=args.date,
            contact=args.contact,
            compute_notes=args.compute_notes,
            report_url=args.report_url,
            base_url=args.base_url,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- wandb/requests errors: a message, not a traceback
        status = _http_status(exc)
        why = f"HTTP {status}" if status else type(exc).__name__
        print(
            f"import failed talking to W&B ({why}). Check WANDB_API_KEY / WANDB_BASE_URL "
            "(or --base-url) and that you can read the project.",
            file=sys.stderr,
        )
        return 1
    for where, reason in result.skipped_runs + result.skipped:
        print(f"skipped {where}: {reason}")
    if result.skipped_runs:
        by_reason: dict[str, int] = {}
        for _, reason in result.skipped_runs:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        for reason, n in sorted(by_reason.items()):
            print(f"skipped {n} run(s): {reason}")
    for cell, loser in result.duplicates:
        print(f"duplicate {cell}: dropped {loser}")
    print(
        f"wrote {result.out} ({result.n_episodes} episodes from {result.n_runs} matching run(s))"
    )
    print(f"next: uv run -m open_apps.leaderboard.validate {result.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
