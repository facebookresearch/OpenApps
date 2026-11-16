"""OpenCUA action_parser: its L2 reply format, the PyAutoGUI mapping onto
BrowserGym, and its coordinate space."""
from __future__ import annotations

import hydra
import numpy as np
import pytest
from agentlab.llm.llm_utils import ParseError
from hydra import compose, initialize

from open_apps.agent.action_parsers import get_action_parser
from open_apps.agent.action_parsers.opencua import OpenCUAActionParser

DESKTOP = (1920, 1080)
PHONE = (390, 844)


def _reply(code: str, thought: str = "I see the page.", action: str = "Do the thing.") -> str:
    """A reply in the shape OpenCUA emits under the L2 prompt."""
    return f"## Thought:\n{thought}\n\n## Action:\n{action}\n\n## Code:\n```python\n{code}\n```"


@pytest.fixture
def cua():
    return OpenCUAActionParser()


def _act(cua, code: str, viewport=DESKTOP) -> str:
    return cua.parse(_reply(code), viewport)["action"]


# --- coordinate space ----------------------------------------------------------


def test_registry_builds_the_parser():
    assert isinstance(get_action_parser("opencua"), OpenCUAActionParser)


def test_model_screen_per_device(cua):
    assert cua.model_screen(DESKTOP) == (1932, 1092)
    assert cua.model_screen(PHONE) == (392, 840)


def test_coordinates_are_resized_screenshot_pixels(cua):
    # Measured: OpenCUA clicks in the smart-resized image's pixels (see
    # action_parsers/opencua.py), so each axis scales by viewport / resized.
    assert cua.rescale(966, 546, DESKTOP) == (960, 540)
    assert cua.rescale(1932, 1092, DESKTOP) == (1919, 1079)
    assert cua.rescale(1733, 962, DESKTOP) == (1722, 951)
    assert cua.rescale(196, 420, PHONE) == (195, 422)
    assert cua.rescale(392, 840, PHONE) == (389, 843)


def test_desktop_click_lands_where_the_model_pointed(cua):
    # A button centred at (1728, 950) on a 1920x1080 screen is at
    # (1739, 961) in the 1932x1092 image the model sees.
    assert _act(cua, "pyautogui.click(x=1739, y=961)") == "mouse_click(x=1728, y=950)"


def test_phone_click_lands_where_the_model_pointed(cua):
    # 390x844 is seen as 392x840.
    assert _act(cua, "pyautogui.click(x=351, y=739)", PHONE) == "mouse_click(x=349, y=743)"


def test_relative_pairs_are_fractions_of_the_viewport(cua):
    # OSWorld's qwen25 projection reads a pair inside [0, 1] as relative.
    assert cua.rescale(0.5, 0.5, DESKTOP) == (960, 540)
    assert cua.rescale(0.25, 0.75, PHONE) == (98, 633)


def test_rescale_clamps_to_the_viewport(cua):
    assert cua.rescale(5000, 5000, DESKTOP) == (1919, 1079)
    assert cua.rescale(5000, 5000, PHONE) == (389, 843)


def test_coord_scale_override_falls_back_to_the_shared_rescale():
    parser = get_action_parser("opencua", coord_scale=1000)
    assert parser.rescale(500, 500, DESKTOP) == (960, 540)


# --- reply format ----------------------------------------------------------------


def test_thought_action_and_code_are_split(cua):
    out = cua.parse(
        _reply("pyautogui.click(x=0.5, y=0.5)", thought="The todo app is open.",
               action='Click the "Add" button.'),
        DESKTOP,
    )
    assert out["action"] == "mouse_click(x=960, y=540)"
    assert out["think"] == "The todo app is open."
    assert out["displayed_action"] == (
        'Click the "Add" button.\n```python\npyautogui.click(x=0.5, y=0.5)\n```'
    )


def test_bare_section_headers_and_code_fence(cua):
    # The format the system prompt shows: "Thought:" / "Action:" without ##.
    reply = "Thought:\nI will click.\n\nAction:\nClick it.\n\n```\npyautogui.click(0.5, 0.5)\n```"
    out = cua.parse(reply, DESKTOP)
    assert out["action"] == "mouse_click(x=960, y=540)"
    assert out["think"] == "I will click."


def test_unfenced_code_lines_are_accepted(cua):
    reply = "## Action:\nClick it.\n\n## Code:\npyautogui.click(x=0.5, y=0.5)"
    assert cua.parse(reply, DESKTOP)["action"] == "mouse_click(x=960, y=540)"


def test_the_last_code_block_wins(cua):
    reply = _reply("pyautogui.click(x=0.1, y=0.1)") + "\n```python\npyautogui.click(x=0.5, y=0.5)\n```"
    assert cua.parse(reply, DESKTOP)["action"] == "mouse_click(x=960, y=540)"


def test_only_the_first_step_of_a_runaway_reply_counts(cua):
    # With its history as text, OpenCUA can go on to imagine later steps.
    reply = (
        "# Step 2\n### Thoughts:\nThe field is focused.\n\n### Action:\nType Call Mom.\n\n"
        "pyautogui.write(message='Call Mom')\n\n"
        "# Step 3\n### Thoughts:\nNow submit.\n\n### Action:\nClick Add.\n\n"
        "pyautogui.click(x=1233, y=185)\n\n"
        "# Step 4\n## Thought:\nDone.\n## Code:\n```code\ncomputer.terminate(status='success')\n```"
    )
    out = cua.parse(reply, DESKTOP)
    assert out["action"] == "keyboard_type(text='Call Mom')"
    assert out["think"] == "The field is focused."
    assert out["displayed_action"] == "Type Call Mom.\n```python\npyautogui.write(message='Call Mom')\n```"


def test_a_leading_step_header_is_kept(cua):
    reply = "# Step 1:\n" + _reply("pyautogui.click(x=0.5, y=0.5)")
    assert cua.parse(reply, DESKTOP)["action"] == "mouse_click(x=960, y=540)"


def test_import_and_sleep_lines_are_ignored(cua):
    code = "import pyautogui\nimport time\npyautogui.click(x=0.5, y=0.5)\ntime.sleep(1)"
    assert _act(cua, code) == "mouse_click(x=960, y=540)"


# --- the action mapping -----------------------------------------------------------


@pytest.mark.parametrize(
    "code, expected",
    [
        ("pyautogui.click(x=0.5, y=0.5)", "mouse_click(x=960, y=540)"),
        ("pyautogui.click(0.5, 0.5, clicks=2)", "mouse_dblclick(x=960, y=540)"),
        ("pyautogui.doubleClick(x=0.5, y=0.5)", "mouse_dblclick(x=960, y=540)"),
        ("pyautogui.tripleClick(x=0.5, y=0.5)", "mouse_tripleclick(x=960, y=540)"),
        ("computer.triple_click(x=0.5, y=0.5)", "mouse_tripleclick(x=960, y=540)"),
        ("pyautogui.rightClick(x=0.5, y=0.5)", "mouse_click(x=960, y=540, button='right')"),
        ("pyautogui.click(x=0.5, y=0.5, button='right')", "mouse_click(x=960, y=540, button='right')"),
        ("pyautogui.moveTo(x=0.5, y=0.5)", "mouse_move(x=960, y=540)"),
        ("pyautogui.write(message='Call mom')", "keyboard_type(text='Call mom')"),
        ("pyautogui.typewrite(\"it's\")", "keyboard_type(text=\"it's\")"),
        ("pyautogui.press('enter')", "keyboard_press(key='Enter')"),
        ("pyautogui.press(keys=['esc'])", "keyboard_press(key='Escape')"),
        ("pyautogui.press('backspace', presses=3)", "keyboard_press_repeat(key='Backspace', count=3)"),
        ("pyautogui.press(['down', 'down'])", "keyboard_press_repeat(key='ArrowDown', count=2)"),
        ("pyautogui.hotkey('ctrl', 'a')", "keyboard_press(key='Control+a')"),
        ("pyautogui.hotkey(keys=['ctrl', 'F'])", "keyboard_press(key='Control+f')"),
        ("pyautogui.hotkey(['command', 'shift', 't'])", "keyboard_press(key='Meta+Shift+t')"),
        ("pyautogui.press('f5')", "keyboard_press(key='F5')"),
        ("pyautogui.scroll(-3)", "scroll(0, 300)"),
        ("pyautogui.scroll(5)", "scroll(0, -500)"),
        ("pyautogui.scroll(-54)", "scroll(0, 1080)"),
        ("pyautogui.hscroll(2)", "scroll(200, 0)"),
        ("pyautogui.scroll(-3, x=0.5, y=0.5)", "scroll_at(x=960, y=540, delta_x=0, delta_y=300)"),
        ("computer.wait()", "noop(wait_ms=3000)"),
        ("computer.terminate(status='failure')", "report_infeasible(reason='I see the page.')"),
        ("computer.terminate(status='fail')", "report_infeasible(reason='I see the page.')"),
        ("computer.terminate(status='success')", "send_msg_to_user(text='I see the page.')"),
        ("computer.terminate(status='success', answer='42')", "send_msg_to_user(text='42')"),
    ],
)
def test_single_calls(cua, code, expected):
    assert _act(cua, code) == expected


@pytest.mark.parametrize(
    "code, expected",
    [
        # Sequences OpenCUA's training data records as one step.
        ("pyautogui.write(message='Call mom')\npyautogui.press(keys=['enter'])",
         "click_and_type(x=None, y=None, text='Call mom', press_enter=True)"),
        ("pyautogui.click(x=0.5, y=0.5)\npyautogui.write('Hi Bob')",
         "click_and_type(x=960, y=540, text='Hi Bob', press_enter=False)"),
        ("pyautogui.click(x=0.5, y=0.5)\npyautogui.write('Hi')\npyautogui.press('enter')",
         "click_and_type(x=960, y=540, text='Hi', press_enter=True)"),
        ("pyautogui.moveTo(x=0.5, y=0.5)\npyautogui.scroll(-3)",
         "scroll_at(x=960, y=540, delta_x=0, delta_y=300)"),
        ("pyautogui.moveTo(x=0.25, y=0.25)\npyautogui.dragTo(x=0.5, y=0.5, button='left')",
         "mouse_drag_and_drop(from_x=480, from_y=270, to_x=960, to_y=540)"),
    ],
)
def test_sequences_that_make_one_action(cua, code, expected):
    assert _act(cua, code) == expected


def test_terminate_without_a_thought_still_reports(cua):
    reply = "```code\ncomputer.terminate(status='success')\n```"
    assert cua.parse(reply, DESKTOP)["action"] == "send_msg_to_user(text='Task completed.')"


@pytest.mark.parametrize(
    "reply, match",
    [
        ("", "Empty response"),
        ("I should click the Add button.", "Expected the action as PyAutoGUI code"),
        (_reply("pyautogui.click(x=0.5, y=0.5"), "not valid Python"),
        (_reply("x = 5"), "may only call pyautogui"),
        (_reply("print('hi')"), "may only call pyautogui"),
        (_reply("import pyautogui"), "No pyautogui or computer call"),
        (_reply("pyautogui.click()"), "needs numeric x and y"),
        (_reply("pyautogui.click(x='a', y='b')"), "needs numeric x and y"),
        (_reply("pyautogui.click(x=-5, y=10)"), "must be non-negative"),
        (_reply("pyautogui.click(x=pos, y=10)"), "must be literals"),
        (_reply("pyautogui.click(x=1, y=2, button='side')"), "Unknown mouse button"),
        (_reply("pyautogui.write(5)"), "text to type as a string"),
        (_reply("pyautogui.press([])"), "one or more key names"),
        (_reply("pyautogui.press(['a', 'b'])"), "several different keys"),
        (_reply("pyautogui.press('a', presses=0)"), "positive integer"),
        (_reply("pyautogui.scroll(0)"), "non-zero number of clicks"),
        (_reply("pyautogui.dragTo(x=10, y=10)"), "needs a start point"),
        (_reply("pyautogui.screenshot()"), "Unsupported action pyautogui.screenshot"),
        (_reply("computer.open_url('https://example.com')"), "Unsupported action computer.open_url"),
        (_reply("pyautogui.click(x=0.1, y=0.1)\npyautogui.click(x=0.2, y=0.2)"),
         "Only one action can run per step"),
        (_reply("pyautogui.click(x=1, y=2, 3, 4, 5, 6)"), "not valid Python"),
        (_reply("pyautogui.moveTo(1, 2, 3, 4)"), "Too many positional arguments"),
    ],
)
def test_malformed_replies_raise_parse_errors(cua, reply, match):
    with pytest.raises(ParseError, match=match):
        cua.parse(reply, DESKTOP)


# --- the agent config ----------------------------------------------------------


def _agent_args(name: str = "OpenCUA-7B"):
    overrides = [f"agent={name}", "agent.hostname=placeholder-node"]
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=overrides)
    return hydra.utils.instantiate(config.agent)


SAMPLES = [
    "pyautogui.click(x=10, y=20)",
    "pyautogui.rightClick(x=10, y=20)",
    "pyautogui.doubleClick(x=10, y=20)",
    "computer.triple_click(x=10, y=20)",
    "pyautogui.moveTo(x=10, y=20)",
    "pyautogui.moveTo(x=10, y=20)\npyautogui.dragTo(x=30, y=40)",
    "pyautogui.moveTo(x=10, y=20)\npyautogui.scroll(-3)",
    "pyautogui.write(message='a \\'quoted\\'\\nline')",
    "pyautogui.write(message='x')\npyautogui.press('enter')",
    "pyautogui.click(x=10, y=20)\npyautogui.write('x')",
    "pyautogui.press('backspace', presses=4)",
    "pyautogui.hotkey('ctrl', 'a')",
    "pyautogui.scroll(-5)",
    "computer.wait()",
    "computer.terminate(status='success')",
    "computer.terminate(status='failure')",
]


@pytest.mark.parametrize("name", ["OpenCUA-7B", "OpenCUA-32B"])
def test_configs_select_the_parser_and_model(name):
    args = _agent_args(name)
    assert args.action_parser == "opencua"
    assert args.model_name == f"xlangai/{name}"
    assert args.temperature == 0


@pytest.mark.parametrize("code", SAMPLES)
def test_every_mapped_action_runs_in_the_configured_action_set(code):
    args = _agent_args()
    action_set = args.make_flags().action.action_set.make_action_set()
    action = OpenCUAActionParser().parse(_reply(code), DESKTOP)["action"]
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


@pytest.mark.parametrize("viewport, expected", [(DESKTOP, (1722, 951)), (PHONE, (195, 422))])
def test_agent_prompt_and_click_scaling(viewport, expected):
    agent = _agent_args().make_agent()
    code = "pyautogui.click(x=1733, y=962)" if viewport == DESKTOP else "pyautogui.click(x=196, y=420)"
    agent.chat_llm = _FakeChat(_reply(code))
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
    assert system.startswith("You are a GUI agent.")
    assert "password" not in system
    assert action == "mouse_click(x={}, y={})".format(*expected)
