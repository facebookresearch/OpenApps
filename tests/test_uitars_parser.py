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


def test_wait_translates_to_three_second_noop():
    assert _action("wait()") == "noop(wait_ms=3000)"
    assert _action("wait( )") == "noop(wait_ms=3000)"


def test_wait_tolerates_trailing_comment_or_semicolon():
    # The prompt line is literally "wait() #Sleep for 3s ...", so models copy it.
    assert _action("wait() #Sleep for 3s") == "noop(wait_ms=3000)"
    assert _action("wait();") == "noop(wait_ms=3000)"


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


def test_gpt55_abstention_config_offers_abstention_actions():
    # Opt-in config: the baseline GPT-5.5-computer-use stays unchanged.
    from hydra import compose, initialize_config_dir

    from open_apps import config_dir
    from open_apps.agent.utils import CustomActionSetArgs

    with initialize_config_dir(config_dir=str(config_dir() / "agent"), version_base=None):
        cfg = compose(config_name="GPT-5.5-computer-use-abstention")
        baseline = compose(config_name="GPT-5.5-computer-use")
    assert list(cfg.custom_actions[: len(baseline.custom_actions)]) == list(baseline.custom_actions)
    action_set = CustomActionSetArgs(
        subsets=("custom",), custom_actions=list(cfg.custom_actions)
    ).make_action_set()
    for native in ("send_msg_to_user(text='Which one?')", "report_infeasible(reason='No such item.')"):
        name = native.split("(")[0]
        assert name in cfg.custom_actions
        assert name in action_set.action_set
        assert f"{name}(" in cfg.prompt_txt.action_prompt
        assert name not in baseline.custom_actions
        assert f"{name}(" not in baseline.prompt_txt.action_prompt
        # The uitars parser passes these through unchanged, so BrowserGym accepts them.
        assert _action(native) == native
        action_set.to_python_code(_action(native))


def test_wait_prompts_describe_the_actual_sleep():
    # wait() becomes noop(wait_ms=3000); prompts must not promise another duration.
    from open_apps import config_dir

    for path in (config_dir() / "agent").glob("*.yaml"):
        text = path.read_text()
        if "wait()" in text:
            assert "for 3s" in text and "for 5s" not in text, path.name
