"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
End-to-end comparison: does prepending conversational ``context`` to a task
change agent success rate?

For every high-level task that has a ``<key>__with_context`` twin in
``config/tasks/task_contexts.yaml``, this runs the SAME agent twice — once on
the bare task, once on the context variant — through the real
``launch_agent.py`` path (so the context is surfaced via
``OpenAppsTask._get_goal``). It parses ``cum_reward`` from each run and prints
a per-task table plus overall success rates.

Usage:
    export GPT55_API_KEY=...        # credential the GPT-5.5 config expects
    uv run scripts/compare_context_performance.py --agent GPT-5.5-screen-and-text

    # quick smoke test on a couple of tasks with a free agent:
    uv run scripts/compare_context_performance.py --agent dummy \
        --tasks remove_wacv_abstract_deadline message_alice_about_badminton

Notes:
  * Each run boots its own app processes, so this is intentionally serial and
    not fast; it favours transparency/reproducibility over throughput.
  * A run that errors (e.g. missing credential) is recorded as reward=None and
    surfaced in the table rather than silently dropped.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTEXT_SUFFIX = "__with_context"

# Parses the launcher's `cum_reward: 1.0` summary line.
_REWARD_RE = re.compile(r"^cum_reward:\s*([0-9.]+)", re.MULTILINE)


def _discover_context_tasks() -> list[str]:
    """Base task keys that have a ``__with_context`` twin, via the shared loader."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from open_apps.tasks import _load_tasks_cfg

    keys = list(_load_tasks_cfg().keys())
    return sorted(
        k[: -len(CONTEXT_SUFFIX)]
        for k in keys
        if k.endswith(CONTEXT_SUFFIX) and k[: -len(CONTEXT_SUFFIX)] in keys
    )


def _run_task(
    agent: str, task_name: str, headless: bool, max_steps: int | None
) -> float | None:
    """Run one task through launch_agent.py; return cum_reward (or None on error)."""
    cmd = [
        "uv", "run", "launch_agent.py",
        f"agent={agent}",
        f"task_name={task_name}",
        f"browsergym_env_args.headless={headless}",
    ]
    if max_steps is not None:
        cmd.append(f"browsergym_env_args.max_steps={max_steps}")
    print(f"\n=== {agent} | {task_name} ===\n$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True
    )
    out = proc.stdout + proc.stderr
    matches = _REWARD_RE.findall(out)
    if not matches:
        tail = "\n".join(out.strip().splitlines()[-15:])
        print(f"  !! no cum_reward parsed (exit {proc.returncode}). Tail:\n{tail}")
        return None
    reward = float(matches[-1])
    print(f"  -> cum_reward = {reward}")
    return reward


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--agent", default="GPT-5.5-screen-and-text",
                    help="agent config name under config/agent/")
    ap.add_argument("--tasks", nargs="*", default=None,
                    help="base task keys to compare (default: all with a context twin)")
    ap.add_argument("--headless", default="True", choices=["True", "False"])
    ap.add_argument("--max-steps", type=int, default=None,
                    help="override browsergym_env_args.max_steps (default config: 10)")
    args = ap.parse_args()

    base_tasks = args.tasks or _discover_context_tasks()
    if not base_tasks:
        sys.exit("No context tasks found. Is task_contexts.yaml composed in all_tasks.yaml?")

    print(f"Comparing {len(base_tasks)} task(s) with agent={args.agent}\n")
    rows: list[tuple[str, float | None, float | None]] = []
    for base in base_tasks:
        no_ctx = _run_task(args.agent, base, args.headless, args.max_steps)
        with_ctx = _run_task(
            args.agent, base + CONTEXT_SUFFIX, args.headless, args.max_steps
        )
        rows.append((base, no_ctx, with_ctx))

    def fmt(r: float | None) -> str:
        return "  -  " if r is None else f"{r:.2f}"

    print("\n" + "=" * 72)
    print(f"{'task':<44}{'no_ctx':>10}{'with_ctx':>12}{'Δ':>6}")
    print("-" * 72)
    for base, a, b in rows:
        delta = "" if (a is None or b is None) else f"{b - a:+.0f}"
        print(f"{base:<44}{fmt(a):>10}{fmt(b):>12}{delta:>6}")
    print("-" * 72)

    def rate(idx: int) -> str:
        vals = [row[idx] for row in rows if row[idx] is not None]
        if not vals:
            return "n/a"
        return f"{sum(vals) / len(vals):.1%} ({sum(vals):.0f}/{len(vals)})"

    print(f"{'SUCCESS RATE':<44}{rate(1):>10}{rate(2):>12}")
    print("=" * 72)


if __name__ == "__main__":
    main()
