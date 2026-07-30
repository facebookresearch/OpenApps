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
