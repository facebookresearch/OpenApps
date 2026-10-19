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


def test_wait_translates_to_five_second_noop():
    assert _action("wait()") == "noop(wait_ms=5000)"
    assert _action("wait( )") == "noop(wait_ms=5000)"


def test_wait_tolerates_trailing_comment_or_semicolon():
    # The prompt line is literally "wait() #Sleep for 5s ...", so models copy it.
    assert _action("wait() #Sleep for 5s") == "noop(wait_ms=5000)"
    assert _action("wait();") == "noop(wait_ms=5000)"


def test_wait_with_arguments_is_a_parse_error():
    import pytest
    from agentlab.llm.llm_utils import ParseError

    with pytest.raises(ParseError):
        _action("wait(forever=True)")


def test_every_config_advertising_wait_can_execute_it():
    # BrowserGym always allows noop, so the translated wait() must be accepted
    # by every config whose prompt advertises wait(), while the untranslated
    # call is what used to fail with "Invalid action type 'wait'".
    import pytest
    from hydra import compose, initialize_config_dir

    from open_apps import config_dir
    from open_apps.agent.utils import CustomActionSetArgs

    agent_dir = config_dir() / "agent"
    advertising = [p.stem for p in agent_dir.glob("*.yaml") if "wait()" in p.read_text()]
    assert "GPT-5.5-computer-use" in advertising
    inheriting = [
        p.stem
        for p in agent_dir.glob("*.yaml")
        if any(f"- {name}\n" in p.read_text() for name in advertising)
    ]
    with initialize_config_dir(config_dir=str(agent_dir), version_base=None):
        for name in sorted({*advertising, *inheriting}):
            cfg = compose(config_name=name)
            action_set = CustomActionSetArgs(
                subsets=("custom",), custom_actions=list(cfg.custom_actions)
            ).make_action_set()
            action_set.to_python_code(_action("wait()"))
            with pytest.raises(NameError, match="Invalid action type 'wait'"):
                action_set.to_python_code("wait()")
