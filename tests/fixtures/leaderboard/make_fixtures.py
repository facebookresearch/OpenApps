"""Regenerate the example leaderboard bundle and demonstration in this directory.

    uv run python tests/fixtures/leaderboard/make_fixtures.py

Packs a dummy agent's sweep over ``tiny_v1.yaml`` (written by
``tests/_leaderboard_helpers.write_logs_tree`` from the alignment golden
records) with the real ``pack``. For tests only -- it is deliberately NOT
under ``leaderboard/submissions``, so it never reaches the public table.
``tests/test_leaderboard_pack.py`` fails if this copy goes stale.

The example demonstration (``demonstrations/example__todo-basics``) is
written by hand-equivalent code, as a contributor without ``pack`` would;
``tests/test_leaderboard_demonstrations.py`` checks it is current.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests._leaderboard_helpers import (
    EXAMPLE_BUNDLE,
    EXAMPLE_DEMO,
    make_example_bundle,
    make_example_demonstration,
)


def main() -> None:
    if EXAMPLE_BUNDLE.exists():
        shutil.rmtree(EXAMPLE_BUNDLE)
    with tempfile.TemporaryDirectory() as tmp:
        out = make_example_bundle(EXAMPLE_BUNDLE, Path(tmp) / "logs")
    print(f"wrote {out}")
    if EXAMPLE_DEMO.exists():
        shutil.rmtree(EXAMPLE_DEMO)
    print(f"wrote {make_example_demonstration(EXAMPLE_DEMO)}")


if __name__ == "__main__":
    main()
