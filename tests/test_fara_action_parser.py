"""Fara-7B action_parser: its <tool_call> grammar, the action mapping onto
BrowserGym, and the resized-screenshot coordinate space."""
from __future__ import annotations

import json

import hydra
import numpy as np
import pytest
from agentlab.llm.llm_utils import ParseError
from hydra import compose, initialize

from open_apps.agent.action_parsers import get_action_parser
from open_apps.agent.action_parsers.fara import FaraActionParser, smart_resize

DESKTOP = (1920, 1080)
PHONE = (390, 844)


def _fara(action: str, thought: str = "", **args) -> str:
    """A reply in the shape Fara emits: a thought, then one tool call."""
    payload = {"name": "computer_use", "arguments": {"action": action, **args}}
    return f"{thought}\n<tool_call>\n{json.dumps(payload)}\n</tool_call>"


@pytest.fixture
def fara():
    return FaraActionParser()


# --- coordinate space ----------------------------------------------------------


@pytest.mark.parametrize(
    "height, width",
    [(1080, 1920), (844, 390), (800, 1280), (900, 1440), (1024, 768), (30, 30), (8000, 8000)],
)
def test_smart_resize_matches_the_qwen2_vl_image_processor(height, width):
    hf = pytest.importorskip("transformers.models.qwen2_vl.image_processing_qwen2_vl")
    expected = hf.smart_resize(height, width, factor=28, min_pixels=3136, max_pixels=12845056)
    assert smart_resize(height, width) == tuple(expected)


def test_model_screen_per_device(fara):
    assert fara.model_screen(DESKTOP) == (1932, 1092)
    assert fara.model_screen(PHONE) == (392, 840)
    # The size microsoft/fara's model card advertises for its 1440x900 browser.
    assert fara.model_screen((1440, 900)) == (1428, 896)


def test_coordinates_are_viewport_pixels(fara):
    # Measured: Fara's clicks come back in viewport pixels, not in the
    # resized image the prompt describes (see action_parsers/fara.py).
    assert fara.rescale(960, 540, DESKTOP) == (960, 540)
    assert fara.rescale(1728.4, 950.6, DESKTOP) == (1728, 951)
    assert fara.rescale(195, 422, PHONE) == (195, 422)


def test_rescale_clamps_to_the_viewport(fara):
    # The stated screen (1932x1092, 392x840) is a few px past the viewport.
    assert fara.rescale(1932, 1092, DESKTOP) == (1919, 1079)
    assert fara.rescale(392, 840, PHONE) == (389, 840)
    assert fara.rescale(-3, -1, DESKTOP) == (0, 0)


def test_coord_scale_override_falls_back_to_the_shared_rescale():
    assert get_action_parser("fara", 1000).rescale(500, 500, DESKTOP) == (960, 540)
    assert get_action_parser("fara").coord_scale is None


def test_render_system_prompt_states_the_resized_screen(fara):
    template = "* The screen's resolution is {display_width_px}x{display_height_px}."
    assert fara.render_system_prompt(template, DESKTOP).endswith("is 1932x1092.")
    assert fara.render_system_prompt(template, PHONE).endswith("is 392x840.")


# --- action mapping ------------------------------------------------------------


def test_left_click_keeps_the_thought(fara):
    out = fara.parse(
        _fara("left_click", "I will click the Add button.", coordinate=[1232, 181]), DESKTOP
    )
    assert out["action"] == "mouse_click(x=1232, y=181)"
    assert out["think"] == "I will click the Add button."
    assert out["displayed_action"].startswith("<tool_call>")
    assert out["displayed_action"].endswith("</tool_call>")


def test_left_click_on_phone(fara):
    out = fara.parse(_fara("left_click", coordinate=[196, 900]), PHONE)
    assert out["action"] == "mouse_click(x=196, y=843)"
    assert out["think"] is None


def test_mouse_move(fara):
    assert fara.parse(_fara("mouse_move", coordinate=[644, 364]), DESKTOP)["action"] == (
        "mouse_move(x=644, y=364)"
    )


def test_type_clicks_its_coordinate_and_presses_enter_by_default(fara):
    # FaraAgent's default when press_enter is absent is True.
    out = fara.parse(_fara("type", text="Call mom", coordinate=[948, 181]), DESKTOP)
    assert out["action"] == (
        "click_and_type(x=948, y=181, text='Call mom', press_enter=True, clear=False)"
    )


def test_type_honours_press_enter_and_delete_existing_text(fara):
    out = fara.parse(
        _fara("type", text="Hi Bob", coordinate=[948, 960], press_enter=False,
              delete_existing_text=True),
        DESKTOP,
    )
    assert out["action"] == (
        "click_and_type(x=948, y=960, text='Hi Bob', press_enter=False, clear=True)"
    )


def test_type_without_coordinate_types_into_the_focused_field(fara):
    out = fara.parse(_fara("type", text="it's", press_enter="false"), DESKTOP)
    assert out["action"] == (
        "click_and_type(x=None, y=None, text=\"it's\", press_enter=False, clear=False)"
    )


@pytest.mark.parametrize(
    "keys, chord",
    [(["Enter"], "Enter"), (["ctrl", "a"], "Control+a"), (["Control", "Shift", "t"],
     "Control+Shift+t"), ("esc", "Escape"), (["space"], "Space")],
)
def test_key_presses_one_chord(fara, keys, chord):
    assert fara.parse(_fara("key", keys=keys), DESKTOP)["action"] == (
        f"keyboard_press(key='{chord}')"
    )


def test_scroll_is_a_fixed_wheel_with_fara_signs(fara):
    # Fara: positive pixels scroll up; BrowserGym: positive dy scrolls down.
    assert fara.parse(_fara("scroll", pixels=-500), DESKTOP)["action"] == "scroll(0, 400)"
    assert fara.parse(_fara("scroll", pixels=300, coordinate=[10, 10]), DESKTOP)["action"] == (
        "scroll(0, -400)"
    )


def test_wait_maps_to_noop_in_milliseconds(fara):
    assert fara.parse(_fara("wait", time=2), DESKTOP)["action"] == "noop(wait_ms=2000)"
    assert fara.parse(_fara("wait"), DESKTOP)["action"] == "noop(wait_ms=3000)"
    assert fara.parse(_fara("wait", time=600), DESKTOP)["action"] == "noop(wait_ms=10000)"


def test_history_back_and_memorize(fara):
    assert fara.parse(_fara("history_back"), DESKTOP)["action"] == "go_back()"
    out = fara.parse(_fara("pause_and_memorize_fact", fact="Bob is free at 3"), DESKTOP)
    assert out["action"] == "noop(wait_ms=0)"


def test_terminate_success_reports_to_the_user(fara):
    out = fara.parse(_fara("terminate", "The todo was added.", status="success"), DESKTOP)
    assert out["action"] == "send_msg_to_user(text='The todo was added.')"


def test_terminate_failure_reports_infeasible(fara):
    out = fara.parse(_fara("terminate", "There is no such contact.", status="failure"), DESKTOP)
    assert out["action"] == "report_infeasible(reason='There is no such contact.')"
    out = fara.parse(_fara("terminate", status="failure"), DESKTOP)
    assert out["action"] == "report_infeasible(reason='Task failed.')"


# --- malformed replies ---------------------------------------------------------


def test_truncated_closing_tag_still_parses(fara):
    reply = (
        "Click it.\n<tool_call>\n"
        '{"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [610, 1045]}}'
    )
    assert fara.parse(reply, DESKTOP)["action"] == "mouse_click(x=610, y=1045)"


def test_python_literal_payload_is_accepted(fara):
    reply = (
        "<tool_call>\n{'name': 'computer_use', 'arguments': {'action': 'type', "
        "'text': 'x', 'press_enter': False}}\n</tool_call>"
    )
    assert fara.parse(reply, DESKTOP)["action"] == (
        "click_and_type(x=None, y=None, text='x', press_enter=False, clear=False)"
    )


def test_arguments_given_as_a_json_string(fara):
    reply = (
        '<tool_call>{"name": "computer_use", "arguments": '
        '"{\\"action\\": \\"left_click\\", \\"coordinate\\": [610, 1045]}"}</tool_call>'
    )
    assert fara.parse(reply, DESKTOP)["action"] == "mouse_click(x=610, y=1045)"


def test_think_tags_are_stripped_from_the_thought(fara):
    out = fara.parse("<think>Open the app.</think>" + _fara("left_click", coordinate=[1, 1]), DESKTOP)
    assert out["think"] == "Open the app."


@pytest.mark.parametrize(
    "reply, match",
    [
        ("", "Empty response"),
        ("I should click the button.", "Expected one <tool_call>"),
        ("<tool_call>{not json}</tool_call>", "not valid JSON"),
        ("<tool_call>[1, 2]</tool_call>", "Expected one <tool_call>"),
        (_fara("left_click"), "must be a \\[x, y\\] list"),
        (_fara("left_click", coordinate=[1, 2, 3]), "must be a \\[x, y\\] list"),
        (_fara("left_click", coordinate=["a", "b"]), "two numbers"),
        (_fara("type", coordinate=[1, 2]), "missing 'text'"),
        (_fara("key", keys=[]), "non-empty 'keys'"),
        (_fara("scroll"), "non-zero 'pixels'"),
        (_fara("scroll", pixels="lots"), "must be a number"),
        (_fara("visit_url", url="https://bing.com"), "not available here"),
        (_fara("web_search", query="todo app"), "not available here"),
        (_fara("teleport"), "Unsupported action 'teleport'"),
    ],
)
def test_malformed_replies_raise_parse_errors(fara, reply, match):
    with pytest.raises(ParseError, match=match):
        fara.parse(reply, DESKTOP)


# --- the agent config ----------------------------------------------------------


def _agent_args():
    overrides = ["agent=Fara-7B", "agent.hostname=placeholder-node"]
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=overrides)
    return hydra.utils.instantiate(config.agent)


SAMPLES = [
    _fara("left_click", coordinate=[10, 20]),
    _fara("mouse_move", coordinate=[10, 20]),
    _fara("type", text="a 'quoted'\nline", coordinate=[10, 20], delete_existing_text=True),
    _fara("type", text="plain"),
    _fara("key", keys=["ctrl", "a"]),
    _fara("scroll", pixels=-1),
    _fara("wait", time=1),
    _fara("history_back"),
    _fara("pause_and_memorize_fact", fact="f"),
    _fara("terminate", "done", status="success"),
    _fara("terminate", "can't", status="failure"),
]


@pytest.mark.parametrize("reply", SAMPLES)
def test_every_mapped_action_runs_in_the_configured_action_set(reply):
    args = _agent_args()
    action_set = args.make_flags().action.action_set.make_action_set()
    action = FaraActionParser().parse(reply, DESKTOP)["action"]
    # Raises NameError for an action missing from custom_actions.
    action_set.to_python_code(action)


class _FakeChat:
    def __init__(self, reply: str):
        self.reply = reply
        self.messages = None

    def __call__(self, messages):
        self.messages = messages
        return {"role": "assistant", "content": self.reply}

    def get_stats(self):
        return {}


@pytest.mark.parametrize("viewport, screen", [(DESKTOP, "1932x1092"), (PHONE, "392x840")])
def test_agent_states_the_resized_screen_and_clicks_in_viewport_pixels(viewport, screen):
    agent = _agent_args().make_agent()
    agent.chat_llm = _FakeChat(_fara("left_click", "Click.", coordinate=[196, 420]))
    w, h = viewport
    obs = {
        "goal_object": [{"type": "text", "text": "Add a todo"}],
        "screenshot": np.zeros((h, w, 3), dtype=np.uint8),
        "last_action_error": "",
        "axtree_txt": "",
        "pruned_html": "",
        "focused_element_bid": "",
    }
    action, _ = agent.get_action(obs)
    system = agent.chat_llm.messages[0]["content"]
    system = system if isinstance(system, str) else system[0]["text"]
    assert f"The screen's resolution is {screen}." in system
    assert "{display_width_px}" not in system
    assert action == "mouse_click(x=196, y=420)"
