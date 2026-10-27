"""Every agent can end an episode by its own word.

Under ``task_termination=on_agent_stop`` an episode ends only when the agent
says it is done (``send_msg_to_user``) or refuses (``report_infeasible``); an
agent that cannot say either runs to ``max_steps`` and is scored unreported,
so ``aligned`` and ``honest`` are unmeasurable for it. These tests pin that
every config offers both actions, the action set can execute them, and the
prompt the model sees actually describes them.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from hydra import compose, initialize_config_dir

from open_apps import config_dir
from open_apps.agent.utils import CustomActionSetArgs

REPORT_ACTIONS = ("send_msg_to_user", "report_infeasible")

# The dummy agent replays a fixed script and has no action set.
_SKIP = {"dummy"}
AGENT_CONFIGS = sorted(
    p.stem for p in (Path(config_dir()) / "agent").glob("*.yaml") if p.stem not in _SKIP
)


def _compose(name: str):
    with initialize_config_dir(config_dir=str(config_dir() / "agent"), version_base=None):
        return compose(config_name=name)


@pytest.mark.parametrize("name", AGENT_CONFIGS)
def test_every_agent_offers_the_report_actions(name):
    cfg = _compose(name)
    action_set = CustomActionSetArgs(
        subsets=("custom",), custom_actions=list(cfg.custom_actions)
    ).make_action_set()
    for action in REPORT_ACTIONS:
        assert action in cfg.custom_actions, f"{name} lacks {action}"
        assert action in action_set.action_set
    action_set.to_python_code("send_msg_to_user('Done.')")
    action_set.to_python_code("report_infeasible('Cannot be done.')")


@pytest.mark.parametrize("name", AGENT_CONFIGS)
def test_every_agent_prompt_describes_how_to_report(name):
    cfg = _compose(name)
    prompt = cfg.prompt_txt
    if prompt.get("action_prompt"):
        # A hand-written action space must list them itself.
        for action in REPORT_ACTIONS:
            assert f"{action}(" in prompt.action_prompt, f"{name} prompt omits {action}"
    elif cfg.get("action_parser") == "qwen3vl":
        # Qwen speaks through its tool schema; the parser maps these names.
        assert '"answer"' in prompt.system_prompt
        assert '"terminate"' in prompt.system_prompt
    # Otherwise the prompt is generated from the action set, checked above.
