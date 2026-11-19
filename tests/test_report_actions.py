"""Every agent can answer, ask, or refuse.

``OpenAppsTask.validate`` scores an ``AnswerTask`` on the agent's
``send_msg_to_user`` text and an ``AbstentionTask`` on whether the agent
acted, asked (``send_msg_to_user``) or reported the request infeasible
(``report_infeasible``). An agent whose action set lacks those two can never
pass either kind of task. These tests pin that every config offers both, the
action set can execute them, and the prompt the model sees describes them.
"""
from pathlib import Path

import pytest
from hydra import compose, initialize_config_dir

from open_apps import config_dir
from open_apps.agent.action_parsers import get_action_parser
from open_apps.agent.utils import CustomActionSetArgs

REPORT_ACTIONS = ("send_msg_to_user", "report_infeasible")

# The dummy agent clicks random links and never reads a prompt; it has no
# custom_actions and no model that could choose to answer or refuse.
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


_QWEN_CALL = '<tool_call>{{"name": "computer_use", "arguments": {}}}</tool_call>'

# How each action_parser family answers or asks, says it is done, and
# refuses in its own words, and the names its tool schema has to advertise
# for the model to know it can. A config with a new action_parser needs an
# entry here.
NATIVE_REPORTS = {
    "uitars": {
        "responses": [
            f"<think>t</think><action>{native}</action>"
            for native in (
                "send_msg_to_user(text='Which Dennis?')",
                "finished(content='Added it.')",
                "report_infeasible(reason='No such contact.')",
            )
        ],
        # The prompt is the hand-written action_prompt, or generated from
        # the action set.
        "schema_names": (),
    },
    "qwen3vl": {
        "responses": [
            _QWEN_CALL.format('{"action": "answer", "text": "Which Dennis?"}'),
            _QWEN_CALL.format('{"action": "terminate", "status": "success", "text": "Added it."}'),
            _QWEN_CALL.format('{"action": "terminate", "status": "failure", "text": "No such contact."}'),
        ],
        "schema_names": ('"answer"', '"terminate"'),
    },
}


def _family(cfg) -> dict:
    parser_name = cfg.get("action_parser") or "uitars"
    if parser_name not in NATIVE_REPORTS:
        pytest.fail(
            f"No NATIVE_REPORTS entry for action_parser {parser_name!r}: add how "
            "this family answers, says it is done, and refuses."
        )
    return NATIVE_REPORTS[parser_name]


@pytest.mark.parametrize("name", AGENT_CONFIGS)
def test_every_agent_prompt_describes_how_to_report(name):
    cfg = _compose(name)
    prompt = cfg.prompt_txt
    if prompt.get("action_prompt"):
        # A hand-written action space must list them itself.
        for action in REPORT_ACTIONS:
            assert f"{action}(" in prompt.action_prompt, f"{name} prompt omits {action}"
    # A family that speaks through a tool schema must advertise its own names.
    for native in _family(cfg)["schema_names"]:
        assert native in prompt.system_prompt, f"{name} tool schema omits {native}"
    # Otherwise the prompt is generated from the action set, checked above.


@pytest.mark.parametrize("name", AGENT_CONFIGS)
def test_every_agent_parser_emits_executable_reports(name):
    # What the parser makes of the model's words has to be a call the action
    # set accepts, or the answer never reaches BrowserGym's chat.
    cfg = _compose(name)
    parser = get_action_parser(cfg.get("action_parser"), cfg.get("coord_scale"))
    action_set = CustomActionSetArgs(
        subsets=("custom",), custom_actions=list(cfg.custom_actions)
    ).make_action_set()
    emitted = [
        parser.parse(response, viewport=(1280, 800))["action"]
        for response in _family(cfg)["responses"]
    ]
    assert [a.split("(")[0] for a in emitted] == [
        "send_msg_to_user",
        "send_msg_to_user",
        "report_infeasible",
    ]
    for action in emitted:
        action_set.to_python_code(action)
