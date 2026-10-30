"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Generate ui_questions.json from the saved initial_state.json.

Usage:
    uv run tests/ui_questions/question_generation/create_ui_questions.py
"""

import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `tests.*` imports resolve.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.ui_questions.question_generation.generator import (
    generate_questions_from_state,
    questions_to_json,
)

STATES_DIR = Path("tests/states")
OUTPUT_PATH = Path("tests/ui_questions/ui_questions.json")


def _messenger_config() -> dict:
    """The default messenger config, for the group titles.

    ``/messages_all`` keys a group by ``group:<slug>`` and the list draws its
    title from config, so without this the group would be asked about by its
    key. Only the messenger node is passed: every other template keeps the
    config-free behaviour this file has always been generated with.
    """
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf

    from open_apps import config_dir

    with initialize_config_dir(version_base=None, config_dir=str(config_dir())):
        cfg = compose(config_name="config", overrides=["logs_dir=/tmp"])
    return {"groups": OmegaConf.to_container(cfg.apps.messenger.groups, resolve=True)}


def main() -> None:
    state_path = STATES_DIR / "initial_state.json"
    with open(state_path) as f:
        state = json.load(f)

    config = {"apps": {"messenger": _messenger_config()}}
    questions = generate_questions_from_state(state, config, seed=42)
    output = questions_to_json(questions)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Generated {len(output)} questions -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
