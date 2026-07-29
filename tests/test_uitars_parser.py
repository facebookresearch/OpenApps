import pytest

from agentlab.llm.llm_utils import ParseError
from open_apps.agent.parse_actions import (
    flexible_parser,
    translate_openai_computer_action,
    translate_uitars_action,
)


def _action(native: str) -> str:
    return flexible_parser(f"<think>t</think><action>{native}</action>")["action"]


@pytest.mark.parametrize(
    ("response", "think", "action"),
    [
        (
            "<think>inspect</think><action>wait()</action>",
            "inspect",
            "noop()",
        ),
        ("Thought: inspect\nAction: wait()", "inspect", "noop()"),
        ("THINK: inspect\nACTION: wait()", "inspect", "noop()"),
        ("<think>inspect</think><action>wait()", "inspect", "noop()"),
    ],
)
def test_response_formats(response, think, action):
    parsed = flexible_parser(response)
    assert parsed["think"] == think
    assert parsed["displayed_action"] == "wait()"
    assert parsed["action"] == action


@pytest.mark.parametrize("response", ["", "<think>nothing</think>", "plain text"])
def test_response_without_action_raises(response):
    with pytest.raises(ParseError):
        flexible_parser(response)


def test_scroll_down_translates_to_positive_dy():
    assert _action("scroll(direction='down', point='(612,455)')") == "scroll(0, 455)"


def test_scroll_up_translates_to_negative_dy():
    assert _action("scroll(direction='up', point='(1920,536)')") == "scroll(0, -536)"


def test_scroll_left_and_right_move_along_x_axis():
    assert _action("scroll(direction='right', point='(300,400)')") == "scroll(300, 0)"
    assert _action("scroll(direction='left', point='(300,400)')") == "scroll(-300, 0)"


def test_scroll_tolerates_whitespace_in_point():
    assert _action("scroll(direction='down', point='(612, 455)')") == "scroll(0, 455)"


def test_click_point_regression():
    assert _action("click(point='(100,200)')") == "mouse_click(x=100, y=200)"


def test_type_regression():
    assert _action("type(content='hello\\n')") == "keyboard_type(text='hello\\n')"


def test_uitars_coordinates_support_decimals_and_negatives():
    assert translate_uitars_action("click(point='(-10.5,20.25)')") == (
        "mouse_click(x=-10.5, y=20.25)"
    )


def test_uitars_right_click_and_hotkey():
    assert translate_uitars_action("right_single(point='(10,20)')") == (
        "mouse_click(x=10, y=20, button='right')"
    )
    assert translate_uitars_action("hotkey(key='ctrl alt e')") == (
        "keyboard_press(key='ctrl alt e')"
    )


def test_openai_pointer_actions_translate_to_browsergym():
    assert _action("click(x=100, y=200)") == (
        "mouse_click(x=100, y=200, button='left')"
    )
    assert _action("double_click(x=10, y=20, button='right')") == (
        "mouse_dblclick(x=10, y=20, button='right')"
    )
    assert _action("move(x=30, y=40)") == "mouse_move(x=30, y=40)"
    assert _action("click(x=10, y=20, button='wheel')") == (
        "mouse_click(x=10, y=20, button='middle')"
    )
    assert _action("click(x=10, y=20, button='back')") == "go_back()"
    assert _action("click(x=10, y=20, button='forward')") == "go_forward()"


def test_openai_keyboard_and_scroll_actions_translate_to_browsergym():
    assert _action("keypress(keys=['CTRL', 'L'])") == (
        "keyboard_press(key='Control+L')"
    )
    assert _action("type(text='hello')") == "keyboard_type(text='hello')"
    assert _action("scroll(x=10, y=20, scroll_x=0, scroll_y=500)") == (
        "scroll(delta_x=0, delta_y=500)"
    )


@pytest.mark.parametrize(
    ("openai_key", "playwright_key"),
    [
        ("ENTER", "Enter"),
        ("ESC", "Escape"),
        ("DOWN", "ArrowDown"),
        ("CMD", "Meta"),
        ("META", "Meta"),
    ],
)
def test_openai_named_keys_translate_to_playwright(openai_key, playwright_key):
    assert _action(f"keypress(keys=['{openai_key}'])") == (
        f"keyboard_press(key='{playwright_key}')"
    )


def test_openai_drag_and_wait_actions_translate_to_browsergym():
    assert _action("drag(path=[{'x': 1, 'y': 2}, {'x': 30, 'y': 40}])") == (
        "mouse_drag_and_drop(from_x=1, from_y=2, to_x=30, to_y=40)"
    )
    assert _action("wait()") == "noop()"
    assert _action("screenshot()") == "noop()"


def test_unknown_browsergym_action_passes_through():
    assert translate_openai_computer_action("mouse_click(x=1, y=2)") == (
        "mouse_click(x=1, y=2)"
    )


@pytest.mark.parametrize(
    "action",
    [
        "drag(path=[{'x': 1, 'y': 2}])",
        "keypress(keys='CTRL')",
        "double_click(x=1, y=2, button='back')",
        "type(text=123)",
        "click(1, 2)",
        "scroll(scroll_y=10)",
        "type(text=value)",
    ],
)
def test_invalid_openai_action_shapes_raise_parse_error(action):
    with pytest.raises(ParseError):
        _action(action)


def test_utils_retains_flexible_parser_compatibility_import():
    from open_apps.agent.utils import flexible_parser as compatibility_parser

    assert compatibility_parser is flexible_parser
