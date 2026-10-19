"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Frozen benchmark suites (``config/benchmark/<suite>.yaml``).

A suite is a list of *blocks*, each a ``task_names x app_variations`` product,
run once per seed. The same file serves two readers:

* the sweep -- ``launch_parallel_agents.py benchmark=align_v1`` composes it and
  instantiates :class:`SuiteParallelTasksConfig`, which fans out through the
  existing ``AppVariationParallelTasksConfig`` one block at a time;
* the leaderboard -- :func:`suite_cells` expands the same blocks straight from
  the YAML (no Hydra, no task configs) so ``validate`` can check a bundle's
  coverage in CI without composing anything.

Both expand blocks with :func:`expand_blocks`, so what is run and what is
checked cannot drift apart.

    uv run -m open_apps.leaderboard.suite align_v1                   # summary
    uv run -m open_apps.leaderboard.suite align_v1 --list            # every cell
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from omegaconf import DictConfig, OmegaConf

from open_apps import config_dir
from open_apps.tasks.parallel_tasks import (
    ALL_TASKS,
    AppVariationParallelTasksConfig,
    ParallelTasksConfig,
)

SEED_SETS = ("public", "heldout")
#: Path to a YAML file ``{seeds: [int, ...]}``; wins over the default file.
HELDOUT_ENV = "OPENAPPS_HELDOUT_SEEDS"
#: Gitignored by the repo-wide ``internal-*`` rule. Never commit real seeds:
#: a public held-out seed is a public seed.
HELDOUT_FILENAME = "internal-heldout_seeds.yaml"
#: Hard ceiling on a suite's size per agent. A suite that grows past it is a
#: mistake (a product that should have been a block), not a bigger benchmark.
MAX_CELLS = 400


def suites_dir() -> Path:
    return config_dir() / "benchmark"


def default_heldout_path() -> Path:
    return suites_dir() / HELDOUT_FILENAME


@dataclass(frozen=True, order=True)
class Cell:
    """One run of a suite: what a single ``EpisodeRecord`` must cover."""

    block: str
    task_name: str
    variation: tuple[str, ...]
    seed: int

    @property
    def key(self) -> tuple[str, str, int]:
        """Identity of the cell as a record can report it (no block: a
        record does not know which block it came from)."""
        return cell_key(self.task_name, self.variation, self.seed)


def cell_key(
    task_name: Optional[str], variation: Iterable[str], seed: Optional[int]
) -> tuple[str, str, int]:
    """``(task, variation cell, seed)``. The variation is canonicalised the
    way ``alignment.aggregate.cell_of`` does it (sorted, ``|``-joined), so
    override order never matters."""
    from open_apps.alignment.aggregate import cell_of

    return (
        str(task_name),
        cell_of(list(variation)),
        int(seed if seed is not None else -1),
    )


# --- loading -------------------------------------------------------------------


def suite_path(suite: str | Path) -> Path:
    """``align_v1`` -> ``config/benchmark/align_v1.yaml``; a path is kept."""
    path = Path(suite)
    if path.suffix in (".yaml", ".yml"):
        return path
    return suites_dir() / f"{suite}.yaml"


def load_suite(suite: str | Path) -> DictConfig:
    """The suite's ``benchmark`` node, read without Hydra.

    Only literal data is read from it (blocks, seeds, name), so the
    ``${...}`` interpolations elsewhere in the file never need resolving.
    """
    path = suite_path(suite)
    if not path.is_file():
        raise FileNotFoundError(f"No suite at {path}")
    raw = OmegaConf.load(path)
    bench = raw.get("benchmark")
    if bench is None or "blocks" not in bench:
        raise ValueError(f"{path} has no benchmark.blocks")
    if "name" not in bench:
        bench.name = path.stem
    return bench


#: What a suite runs under when its file does not pin ``task_termination``:
#: the alignment protocol, the only mode where honesty is measurable.
DEFAULT_TERMINATION = "on_agent_stop"


def required_termination(suite: str | Path) -> str:
    """The ``task_termination`` the suite pins (a root key of its file,
    next to ``benchmark``), else :data:`DEFAULT_TERMINATION`."""
    value = OmegaConf.load(suite_path(suite)).get("task_termination")
    return str(value) if value else DEFAULT_TERMINATION


def _heldout_data(path: str | Path | None = None) -> tuple[Path, dict]:
    """``(source, parsed file)`` of the held-out seeds file, from ``path``,
    ``$OPENAPPS_HELDOUT_SEEDS``, or the gitignored default -- in that order."""
    candidates = [path, os.environ.get(HELDOUT_ENV), default_heldout_path()]
    source = next((Path(p) for p in candidates if p), None)
    if source is None or not source.is_file():
        tried = os.environ.get(HELDOUT_ENV) or default_heldout_path()
        raise FileNotFoundError(
            "benchmark.seed_set=heldout needs the held-out seeds: set "
            f"{HELDOUT_ENV}=/path/to/seeds.yaml or create {default_heldout_path()} "
            f"(gitignored; see heldout_seeds.example.yaml). Not found: {tried}"
        )
    data = OmegaConf.to_container(OmegaConf.load(source), resolve=True) or {}
    if isinstance(data, list):
        data = {"seeds": data}
    seeds = data.get("seeds") if isinstance(data, dict) else None
    if not isinstance(seeds, list) or not seeds:
        raise ValueError(f"{source}: expected a non-empty `seeds: [int, ...]` list")
    return source, data


def load_heldout_seeds(path: str | Path | None = None) -> list[int]:
    """Held-out seeds from ``path``, ``$OPENAPPS_HELDOUT_SEEDS``, or the
    gitignored ``config/benchmark/internal-heldout_seeds.yaml`` -- in that
    order. Raises if none is available: silently falling back to the public
    seeds would make a "verified" run verify nothing.
    """
    _, data = _heldout_data(path)
    return [int(s) for s in data["seeds"]]


def heldout_seeds_hash(path: str | Path | None = None) -> tuple[str, bool]:
    """``(seeds_hash, salted)`` for the held-out seed set -- the identifier a
    verified bundle's ``meta.yaml`` records, so two verifications can be told
    to have used the same seeds without either revealing them.

    Deterministic: the sorted, de-duplicated seeds (order and repeats do not
    change which cells run), canonically serialised, sha256'd. The file's
    optional ``salt`` (a secret string kept next to the seeds) is mixed in
    first. Why it matters: a held-out set is typically a few small integers,
    and an unsalted hash of ``[233, 234]`` can be inverted by enumeration in
    seconds -- publishing it would publish the seeds. ``salted`` is False when
    the file has no salt, so callers can warn.
    """
    _, data = _heldout_data(path)
    seeds = sorted({int(s) for s in data["seeds"]})
    salt = str(data.get("salt") or "")
    blob = json.dumps({"salt": salt, "seeds": seeds}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16], bool(salt)


def resolve_seeds(
    public: Sequence[int],
    seed_set: str = "public",
    heldout_path: str | Path | None = None,
) -> list[int]:
    if seed_set not in SEED_SETS:
        raise ValueError(
            f"benchmark.seed_set must be one of {SEED_SETS}, got {seed_set!r}"
        )
    if seed_set == "heldout":
        return load_heldout_seeds(heldout_path)
    seeds = [int(s) for s in public]
    if not seeds:
        raise ValueError("a suite needs at least one public seed")
    return seeds


def expand_blocks(blocks: Mapping[str, Any]) -> list[tuple[str, str, list[str]]]:
    """``[(block, task_name, variation), ...]`` in file order.

    ``task_names: all`` is refused: it expands against whatever the task
    files contain *today*, which is exactly what a frozen suite must not do.
    """
    out = []
    for block, spec in blocks.items():
        spec = (
            OmegaConf.to_container(spec, resolve=True)
            if OmegaConf.is_config(spec)
            else spec
        )
        tasks = spec.get("task_names")
        if tasks == ALL_TASKS or isinstance(tasks, str):
            raise ValueError(
                f"suite block {block!r}: task_names must be an explicit list "
                "(a frozen suite cannot depend on the task files' contents)"
            )
        variations = spec.get("app_variations") or [[]]
        for variation in variations:
            for task in tasks or []:
                out.append((str(block), str(task), [str(o) for o in variation]))
    return out


def suite_cells(
    suite: str | Path | DictConfig,
    seed_set: str = "public",
    heldout_path: str | Path | None = None,
) -> list[Cell]:
    """Every cell of ``suite`` for one seed set."""
    bench = suite if isinstance(suite, DictConfig) else load_suite(suite)
    seeds = resolve_seeds(bench.seeds.public, seed_set, heldout_path)
    cells = [
        Cell(block, task, tuple(variation), seed)
        for block, task, variation in expand_blocks(bench.blocks)
        for seed in seeds
    ]
    dupes = [k for k, n in Counter(c.key for c in cells).items() if n > 1]
    if dupes:
        raise ValueError(
            f"suite has duplicate cells (same task, variation, seed): {dupes[:3]}"
        )
    return cells


def summarize(cells: Sequence[Cell], name: str = "suite") -> str:
    """Human-readable cell count, by block -- printed at launch so nobody
    finds out the size of a sweep from the SLURM queue."""
    by_block: dict[str, Counter] = {}
    for c in cells:
        counts = by_block.setdefault(c.block, Counter())
        counts["runs"] += 1
        counts[("task", c.task_name)] += 1
        counts[("variation", c.variation)] += 1
    seeds = sorted({c.seed for c in cells})
    lines = [f"{name}: {len(cells)} runs per agent ({len(seeds)} seed(s))"]
    for block, counts in by_block.items():
        n_tasks = sum(1 for k in counts if isinstance(k, tuple) and k[0] == "task")
        n_vars = sum(1 for k in counts if isinstance(k, tuple) and k[0] == "variation")
        lines.append(
            f"  {block:<10} {counts['runs']:>4} = {n_tasks} tasks x {n_vars} cells x {len(seeds)} seed(s)"
        )
    return "\n".join(lines)


# --- the sweep -----------------------------------------------------------------


class SuiteParallelTasksConfig(ParallelTasksConfig):
    """The sweep for a frozen suite: each block through
    ``AppVariationParallelTasksConfig``, then once per seed.

    Seeds are applied here rather than as a cell override because ``seed``
    is a root key (it drives ``random``, the event scheduler and the task),
    and ``create_configs`` only carries ``apps``/``device`` over from a cell.
    """

    def __init__(
        self,
        blocks: Mapping[str, Any],
        seeds: Sequence[int],
        seed_set: str = "public",
        suite_version: Optional[str] = None,
        task_names: Any = None,
        app_variations: Any = None,
        heldout_path: Optional[str] = None,
    ) -> None:
        if task_names is not None or app_variations is not None:
            raise ValueError(
                "benchmark=<suite> pins its own tasks and variations; drop "
                "parallel_tasks.task_names / parallel_tasks.app_variations "
                "overrides (or run without benchmark= for an ad-hoc sweep)."
            )
        # Plain dicts: the blocks arrive as a DictConfig interpolated from
        # `benchmark.blocks`, and each fan-out wants plain lists.
        self.blocks: dict = (
            OmegaConf.to_container(blocks, resolve=True)
            if OmegaConf.is_config(blocks)
            else dict(blocks)
        )
        self.public_seeds = [int(s) for s in seeds]
        self.seed_set = seed_set
        self.suite_version = suite_version
        self.heldout_path = heldout_path

    def seeds(self) -> list[int]:
        return resolve_seeds(self.public_seeds, self.seed_set, self.heldout_path)

    def cells(self) -> list[Cell]:
        bench = OmegaConf.create(
            {
                "name": self.suite_version or "suite",
                "seeds": {"public": self.public_seeds},
                "blocks": self.blocks,
            }
        )
        return suite_cells(bench, self.seed_set, self.heldout_path)

    def create_configs(
        self, default_config: DictConfig, *args, **kwargs
    ) -> list[DictConfig]:
        """``*args/**kwargs`` are forwarded to the per-block fan-out so this
        keeps working when ``AppVariationParallelTasksConfig.create_configs``
        grows parameters (e.g. the command-line overrides it composes with)."""
        cells = self.cells()
        seeds = self.seeds()
        print(summarize(cells, f"{self.suite_version} [{self.seed_set} seeds]"))
        configs: list[DictConfig] = []
        for spec in self.blocks.values():
            fanout = AppVariationParallelTasksConfig(
                app_variations=spec.get("app_variations") or [[]],
                task_names=list(spec["task_names"]),
            )
            for config in fanout.create_configs(default_config, *args, **kwargs):
                for i, seed in enumerate(seeds):
                    # Copying a composed config is the expensive part of a
                    # fan-out (~1 s each: the whole task set comes along), so
                    # the last seed takes the original instead of a copy.
                    run = config.copy() if i < len(seeds) - 1 else config
                    run.seed = seed
                    configs.append(run)
        assert len(configs) == len(cells), (len(configs), len(cells))
        return configs


# --- CLI -----------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Show a frozen suite's cells.")
    parser.add_argument("suite", nargs="?", default="align_v1", help="name or path")
    parser.add_argument("--seed-set", choices=SEED_SETS, default="public")
    parser.add_argument("--list", action="store_true", help="print every cell")
    args = parser.parse_args(argv)
    bench = load_suite(args.suite)
    cells = suite_cells(bench, args.seed_set)
    print(summarize(cells, str(bench.name)))
    if args.list:
        for c in cells:
            print(
                f"{c.block}\t{c.task_name}\t{' '.join(c.variation) or '-'}\tseed={c.seed}"
            )
    if len(cells) > MAX_CELLS:
        print(f"suite exceeds MAX_CELLS={MAX_CELLS}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
