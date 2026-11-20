import json

import pytest
from agentlab.llm.llm_utils import ParseError

from open_apps.agent.action_parsers.qwen3vl import Qwen3VLActionParser
from open_apps.agent.utils import retry


def response(arguments):
    return "<tool_call>" + json.dumps({"name": "computer_use", "arguments": arguments}) + "</tool_call>"


@pytest.mark.parametrize("arguments", [
    ["mouse_click"], "mouse_click", 7,
    {"action": "mouse_click", "coordinate": [None, 10]},
    {"action": "mouse_click", "coordinate": ["bad", 10]},
    {"action": "mouse_click", "coordinate": ["NaN", 10]},
    {"action": "mouse_click", "coordinate": ["Infinity", 10]},
    {"action": "scroll", "delta": [{}, 10]},
    {"action": "scroll", "delta": [10, "NaN"]},
    {"action": "scroll", "delta": [10, "Infinity"]},
    {"action": "type", "content": 123},
    {"action": "type", "content": ["text"]},
    {"action": "wait", "wait_ms": "NaN"},
    {"action": "wait", "wait_ms": "Infinity"},
    {"action": "wait", "wait_ms": -1},
    {"action": "wait", "wait_ms": 10 ** 400},
])
def test_invalid_argument_values_are_recoverable_parse_errors(arguments):
    with pytest.raises(ParseError):
        Qwen3VLActionParser().parse(response(arguments), (1280, 800))


def test_retry_can_request_a_new_action_after_invalid_coordinates():
    replies = iter([
        {"content": response({"action": "mouse_click", "coordinate": ["bad", 10]})},
        {"content": response({"action": "mouse_click", "coordinate": [500, 500]})},
    ])
    messages = []
    parser = Qwen3VLActionParser()
    parsed = retry(lambda history: next(replies), messages, n_retry=2,
                   parser=lambda text: parser.parse(text, (1280, 800)))
    assert parsed["action"] == "mouse_click(x=640, y=400)"
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


def test_valid_numeric_strings_and_wait_zero_still_parse():
    parser = Qwen3VLActionParser()
    assert parser.parse(response({"action": "mouse_click", "coordinate": ["500", "500"]}),
                        (1280, 800))["action"] == "mouse_click(x=640, y=400)"
    assert parser.parse(response({"action": "wait", "wait_ms": 0}),
                        (1280, 800))["action"] == "noop(wait_ms=0.0)"
