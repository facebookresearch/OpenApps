"""Tests for per-model-family action_parsers (registry + qwen3vl parser + uitars)."""
from __future__ import annotations

import json

import pytest
from agentlab.llm.llm_utils import ParseError

from open_apps.agent.action_parsers import REGISTRY, get_action_parser, rescale_xy
from open_apps.agent.action_parsers.qwen3vl import Qwen3VLActionParser


VIEWPORT = (1920, 1080)


# ---------------------------------------------------------------------------
# Registry plumbing
# ---------------------------------------------------------------------------

def test_registry_lists_known_action_parsers():
    assert set(REGISTRY) == {"uitars", "qwen3vl"}


def test_get_action_parser_defaults_to_uitars_when_none():
    assert type(get_action_parser(None)).__name__ == "UITarsActionParser"


def test_get_action_parser_raises_on_unknown_name():
    with pytest.raises(ValueError, match="Unknown action_parser 'bogus'"):
        get_action_parser("bogus")


def test_action_parser_families_carry_their_own_coord_space():
    assert get_action_parser("uitars").coord_scale is None
    assert get_action_parser("qwen3vl").coord_scale == 1000


def test_coord_scale_argument_overrides_the_family_default():
    assert get_action_parser("uitars", 1024).coord_scale == 1024
    assert get_action_parser("qwen3vl", 1024).coord_scale == 1024
    # None means "keep the family default", not "raw pixels".
    assert get_action_parser("qwen3vl", None).coord_scale == 1000


# ---------------------------------------------------------------------------
# rescale_xy: the shared coordinate conversion
# ---------------------------------------------------------------------------

def test_rescale_xy_passes_raw_pixels_through_when_scale_is_none():
    assert rescale_xy(612, 455, None, VIEWPORT) == (612, 455)


def test_rescale_xy_rounds_raw_pixels_to_int():
    assert rescale_xy(612.6, 455.4, None, VIEWPORT) == (613, 455)


def test_rescale_xy_maps_normalized_1000_space_to_viewport():
    assert rescale_xy(870, 940, 1000, VIEWPORT) == (1670, 1015)
    assert rescale_xy(500, 500, 1000, VIEWPORT) == (960, 540)


def test_rescale_xy_maps_normalized_1024_space_to_viewport():
    # Gemma/PaliGemma-lineage <locNNNN> bins are 0-1024.
    assert rescale_xy(512, 512, 1024, (1280, 800)) == (640, 400)


def test_rescale_xy_scales_each_axis_by_its_own_viewport_dimension():
    # Same input coordinate, non-square viewport -> different pixel per axis.
    assert rescale_xy(500, 500, 1000, (1280, 800)) == (640, 400)


def test_rescale_xy_preserves_sign_for_negative_deltas():
    assert rescale_xy(0, -500, 1000, VIEWPORT) == (0, -540)


def test_rescale_xy_treats_zero_scale_as_raw_pixels():
    # ``if coord_scale`` guards the division; 0 must not raise.
    assert rescale_xy(100, 200, 0, VIEWPORT) == (100, 200)


# ---------------------------------------------------------------------------
# qwen3vl: prompts live in yaml, not the action_parser
# ---------------------------------------------------------------------------

def test_qwen_action_parser_supplies_no_prompt_defaults():
    assert Qwen3VLActionParser().default_prompts() == {}


# ---------------------------------------------------------------------------
# qwen3vl parser: every supported action, end-to-end
# ---------------------------------------------------------------------------

def _qwen_response(action: str, **args) -> str:
    """The envelope shape Qwen-VL emits when it issues a tool call."""
    payload = {"name": "computer_use", "arguments": {"action": action, **args}}
    return f"<tool_call>\n{json.dumps(payload)}\n</tool_call>"


@pytest.fixture
def qwen():
    return Qwen3VLActionParser()


def test_qwen_parses_left_click_and_rescales_to_viewport(qwen):
    # 0-1000: (870, 940) on 1920x1080 -> (round(870*1920/1000)=1670,
    # round(940*1080/1000)=1015).
    response = (
        "<think>The Send button is bottom-right.</think>\n"
        + _qwen_response("mouse_click", coordinate=[870, 940])
    )
    out = qwen.parse(response, viewport=VIEWPORT)
    assert out["action"] == "mouse_click(x=1670, y=1015)"
    assert out["think"] == "The Send button is bottom-right."
    assert "<tool_call>" in out["displayed_action"]


def test_qwen_captures_action_line_as_think(qwen):
    # Instruct variants use ``Action: ...`` instead of <think>.
    response = (
        "Action: Click the Send button at the bottom-right of the chat panel.\n"
        + _qwen_response("mouse_click", coordinate=[870, 940])
    )
    out = qwen.parse(response, viewport=VIEWPORT)
    assert out["think"] == "Click the Send button at the bottom-right of the chat panel."
    assert out["action"] == "mouse_click(x=1670, y=1015)"


def test_qwen_think_block_wins_over_action_line(qwen):
    response = (
        "<think>structured plan</think>\n"
        "Action: short plan\n"
        + _qwen_response("mouse_click", coordinate=[100, 200])
    )
    assert qwen.parse(response, viewport=VIEWPORT)["think"] == "structured plan"


def test_qwen_parses_type_action_with_content_kwarg(qwen):
    response = _qwen_response("type", content="hello world\n")
    out = qwen.parse(response, viewport=VIEWPORT)
    assert out["action"] == "keyboard_type(text='hello world\\n')"
    assert out["think"] is None


def test_qwen_type_action_accepts_text_alias(qwen):
    response = _qwen_response("type", text="hi\n")
    assert qwen.parse(response, viewport=VIEWPORT)["action"] == "keyboard_type(text='hi\\n')"


def test_qwen_parses_scroll_down_as_positive_delta(qwen):
    # delta [0, 500] in 0-1000 -> (0, round(500*1080/1000)=540).
    response = _qwen_response("scroll", delta=[0, 500])
    assert qwen.parse(response, viewport=VIEWPORT)["action"] == "scroll(0, 540)"


def test_qwen_parses_scroll_up_signs(qwen):
    response = _qwen_response("scroll", delta=[0, -500])
    assert qwen.parse(response, viewport=VIEWPORT)["action"] == "scroll(0, -540)"


def test_qwen_parses_mouse_dblclick(qwen):
    # 100/1000*1920=192; 200/1000*1080=216.
    response = _qwen_response("mouse_dblclick", coordinate=[100, 200])
    assert qwen.parse(response, viewport=VIEWPORT)["action"] == "mouse_dblclick(x=192, y=216)"


def test_qwen_wait_maps_to_noop_with_5s_default(qwen):
    assert qwen.parse(_qwen_response("wait"), viewport=VIEWPORT)["action"] == "noop(wait_ms=5000.0)"
    out = qwen.parse(_qwen_response("wait", wait_ms=1000), viewport=VIEWPORT)
    assert out["action"] == "noop(wait_ms=1000.0)"


# ---------------------------------------------------------------------------
# qwen3vl parser: robustness
# ---------------------------------------------------------------------------

def test_qwen_handles_truncated_closing_tag(qwen):
    # vLLM sometimes cuts off mid-tag; the regex allows a missing </tool_call>.
    response = (
        "<tool_call>\n"
        '{"name": "computer_use", "arguments": {"action": "mouse_click", "coordinate": [100, 200]}}'
    )
    assert qwen.parse(response, viewport=VIEWPORT)["action"] == "mouse_click(x=192, y=216)"


def test_qwen_raises_on_empty_response(qwen):
    with pytest.raises(ParseError, match="Empty response"):
        qwen.parse("", viewport=VIEWPORT)


def test_qwen_raises_when_tool_call_block_missing(qwen):
    with pytest.raises(ParseError, match="Expected a <tool_call>"):
        qwen.parse("I think I should click somewhere.", viewport=VIEWPORT)


def test_qwen_raises_on_malformed_json(qwen):
    with pytest.raises(ParseError, match="not valid JSON"):
        qwen.parse("<tool_call>{not valid json}</tool_call>", viewport=VIEWPORT)


def test_qwen_raises_on_unsupported_action(qwen):
    with pytest.raises(ParseError, match="Unsupported action 'teleport'"):
        qwen.parse(_qwen_response("teleport", coordinate=[1, 2]), viewport=VIEWPORT)


def test_qwen_raises_when_mouse_click_missing_coordinate(qwen):
    response = (
        '<tool_call>{"name": "computer_use", '
        '"arguments": {"action": "mouse_click"}}</tool_call>'
    )
    with pytest.raises(ParseError, match="must be a \\[x, y\\] list"):
        qwen.parse(response, viewport=VIEWPORT)


def test_qwen_raises_when_type_missing_content(qwen):
    response = (
        '<tool_call>{"name": "computer_use", '
        '"arguments": {"action": "type"}}</tool_call>'
    )
    with pytest.raises(ParseError, match="missing 'content'"):
        qwen.parse(response, viewport=VIEWPORT)


# ---------------------------------------------------------------------------
# uitars action_parser: default path must be untouched
# ---------------------------------------------------------------------------

def test_uitars_action_parser_parses_native_action_syntax():
    a = get_action_parser("uitars")
    response = (
        "<think>Click the Submit button.</think>"
        "<action>click(point='<point>100 200</point>')</action>"
    )
    out = a.parse(response, viewport=VIEWPORT)
    assert out["action"] == "mouse_click(x=100, y=200)"
    assert out["think"] == "Click the Submit button."
    assert out["displayed_action"] == "click(point='<point>100 200</point>')"


# ---------------------------------------------------------------------------
# uitars grammar + a normalized coord space (the Gemma screenshot-only case)
# ---------------------------------------------------------------------------

def test_uitars_grammar_rescales_clicks_when_coord_scale_is_set():
    a = get_action_parser("uitars", coord_scale=1000)
    response = "<think>t</think><action>click(point='(500,500)')</action>"
    out = a.parse(response, viewport=(1280, 800))
    assert out["action"] == "mouse_click(x=640, y=400)"
    # The model-native text shown in history stays in the model's own space.
    assert out["displayed_action"] == "click(point='(500,500)')"


def test_uitars_grammar_rescales_right_click_when_coord_scale_is_set():
    a = get_action_parser("uitars", coord_scale=1000)
    response = "<think>t</think><action>right_single(point='(500,500)')</action>"
    out = a.parse(response, viewport=(1280, 800))
    assert out["action"] == "mouse_click(x=640, y=400, button='right')"


def test_uitars_grammar_rescales_scroll_magnitude_when_coord_scale_is_set():
    # The magnitude is read off the point, so it lives in the same space.
    a = get_action_parser("uitars", coord_scale=1000)
    response = "<think>t</think><action>scroll(direction='down', point='(500,500)')</action>"
    assert a.parse(response, viewport=(1280, 800))["action"] == "scroll(0, 400)"


def test_coord_scale_does_not_touch_bid_or_text_actions():
    a = get_action_parser("uitars", coord_scale=1000)
    for action in ['click("96")', 'fill("92", "hello")', "scroll(0, 400)"]:
        response = f"<think>t</think><action>{action}</action>"
        assert a.parse(response, viewport=(1280, 800))["action"] == action


def test_default_uitars_parser_still_passes_raw_pixels_through():
    """Regression guard: the default path must be byte-identical to before."""
    a = get_action_parser("uitars")
    response = "<think>t</think><action>click(point='(500,500)')</action>"
    assert a.parse(response, viewport=(1280, 800))["action"] == "mouse_click(x=500, y=500)"
