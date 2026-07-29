import pytest

from agentlab.llm.llm_utils import ParseError
from open_apps.agent.utils import flexible_parser


def _action(native: str) -> str:
    return flexible_parser(f"<think>t</think><action>{native}</action>")["action"]


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


@pytest.mark.parametrize(
    "action",
    [
        "drag(path=[{'x': 1, 'y': 2}])",
        "keypress(keys='CTRL')",
        "double_click(x=1, y=2, button='back')",
    ],
)
def test_invalid_openai_action_shapes_raise_parse_error(action):
    with pytest.raises(ParseError):
        _action(action)
