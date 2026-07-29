"""Parse model responses and translate native computer actions to BrowserGym."""

import ast
import re

from agentlab.llm.llm_utils import ParseError


OPENAI_KEY_ALIASES = {
    "ALT": "Alt",
    "BACKSPACE": "Backspace",
    "CMD": "Meta",
    "CTRL": "Control",
    "DELETE": "Delete",
    "DOWN": "ArrowDown",
    "END": "End",
    "ENTER": "Enter",
    "ESC": "Escape",
    "HOME": "Home",
    "LEFT": "ArrowLeft",
    "META": "Meta",
    "PAGEDOWN": "PageDown",
    "PAGEUP": "PageUp",
    "RIGHT": "ArrowRight",
    "SHIFT": "Shift",
    "SPACE": " ",
    "TAB": "Tab",
    "UP": "ArrowUp",
}

OPENAI_REQUIRED_ARGUMENTS = {
    "click": {"x", "y"},
    "double_click": {"x", "y"},
    "drag": {"path"},
    "keypress": {"keys"},
    "move": {"x", "y"},
    "scroll": {"scroll_x", "scroll_y"},
    "type": {"text"},
    "screenshot": set(),
    "wait": set(),
}


def flexible_parser(response: str) -> dict:
    """Extract thought/action fields and translate the action to BrowserGym."""
    response = response.strip()
    result = {"action": None, "think": None}

    if not response:
        raise ParseError("Empty response received from the model.")

    action_match = re.search(
        r"<action>(.*?)</action>", response, re.DOTALL | re.IGNORECASE
    )
    think_match = re.search(
        r"<think>(.*?)</think>", response, re.DOTALL | re.IGNORECASE
    )
    if action_match:
        result["action"] = action_match.group(1).strip()
    if think_match:
        result["think"] = think_match.group(1).strip()

    if not result["action"]:
        match = re.search(
            r"<action>\s*(.*?)(?:</action>|$)",
            response,
            re.DOTALL | re.IGNORECASE,
        )
        if match:
            result["action"] = match.group(1).strip()
    if not result["think"]:
        match = re.search(
            r"<think>\s*(.*?)(?:</think>|$)",
            response,
            re.DOTALL | re.IGNORECASE,
        )
        if match:
            result["think"] = match.group(1).strip()

    if not result["action"] or not result["think"]:
        match = re.search(
            r"thought:\s*(.*?)action:\s*(.*)",
            response,
            re.DOTALL | re.IGNORECASE,
        )
        if match:
            result["think"] = result["think"] or match.group(1).strip()
            result["action"] = result["action"] or match.group(2).strip()

    if not result["action"] or not result["think"]:
        for line in response.splitlines():
            line = line.strip()
            if not result["action"] and re.match(r"^action:\s*", line, re.IGNORECASE):
                result["action"] = re.sub(r"^action:\s*", "", line, flags=re.IGNORECASE)
            elif not result["think"] and re.match(
                r"^(think|thought):\s*", line, re.IGNORECASE
            ):
                result["think"] = re.sub(
                    r"^(think|thought):\s*", "", line, flags=re.IGNORECASE
                )

    if result["action"] is None or not result["action"].strip():
        raise ParseError(f"Failed to parse action from response: {response}")

    result["displayed_action"] = result["action"]
    result["action"] = translate_computer_action(result["action"])
    return result


def translate_computer_action(action: str) -> str:
    """Translate OpenAI or UI-TARS native actions to BrowserGym calls."""
    uitars_prefixes = (
        "click(point=",
        "click(start_box=",
        "type(content=",
        "right_single(point=",
        "hotkey(key=",
    )
    if action.startswith(uitars_prefixes) or (
        action.startswith("scroll(") and "direction=" in action
    ):
        return translate_uitars_action(action)
    return translate_openai_computer_action(action)


def translate_openai_computer_action(action: str) -> str:
    """Translate an OpenAI computer-use call to a BrowserGym action call."""
    try:
        expression = ast.parse(action.strip(), mode="eval").body
    except SyntaxError:
        return action
    if not isinstance(expression, ast.Call) or not isinstance(
        expression.func, ast.Name
    ):
        return action

    name = expression.func.id
    if name not in OPENAI_REQUIRED_ARGUMENTS:
        return action
    if expression.args or any(keyword.arg is None for keyword in expression.keywords):
        raise ParseError(f"OpenAI {name} actions require named arguments.")

    try:
        arguments = {
            keyword.arg: ast.literal_eval(keyword.value)
            for keyword in expression.keywords
        }
    except (ValueError, TypeError) as error:
        raise ParseError(f"OpenAI {name} arguments must be literal values.") from error

    missing = OPENAI_REQUIRED_ARGUMENTS[name] - arguments.keys()
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ParseError(f"OpenAI {name} action missing arguments: {missing_list}.")

    if name in {"click", "double_click"}:
        return _translate_openai_click(name, arguments)
    if name == "move":
        return f"mouse_move(x={arguments['x']}, y={arguments['y']})"
    if name == "drag":
        return _translate_openai_drag(arguments["path"])
    if name == "keypress":
        return _translate_openai_keypress(arguments["keys"])
    if name == "scroll":
        return (
            f"scroll(delta_x={arguments['scroll_x']}, delta_y={arguments['scroll_y']})"
        )
    if name == "type":
        if not isinstance(arguments["text"], str):
            raise ParseError("OpenAI type actions require text to be a string.")
        return f"keyboard_type(text={arguments['text']!r})"
    return "noop()"


def _translate_openai_click(name: str, arguments: dict) -> str:
    button = arguments.get("button", "left")
    if name == "click" and button == "back":
        return "go_back()"
    if name == "click" and button == "forward":
        return "go_forward()"
    button = "middle" if button == "wheel" else button
    if button not in {"left", "middle", "right"}:
        raise ParseError(f"Unsupported OpenAI {name} button: {button!r}.")
    browsergym_name = "mouse_click" if name == "click" else "mouse_dblclick"
    return (
        f"{browsergym_name}(x={arguments['x']}, y={arguments['y']}, button={button!r})"
    )


def _translate_openai_drag(path: object) -> str:
    if (
        not isinstance(path, list)
        or len(path) < 2
        or any(
            not isinstance(point, dict) or not {"x", "y"} <= point.keys()
            for point in path
        )
    ):
        raise ParseError("OpenAI drag actions require at least two x/y path points.")
    return (
        f"mouse_drag_and_drop(from_x={path[0]['x']}, from_y={path[0]['y']}, "
        f"to_x={path[-1]['x']}, to_y={path[-1]['y']})"
    )


def _translate_openai_keypress(keys: object) -> str:
    if (
        not isinstance(keys, list)
        or not keys
        or not all(isinstance(key, str) for key in keys)
    ):
        raise ParseError("OpenAI keypress actions require a non-empty list of keys.")
    key = "+".join(OPENAI_KEY_ALIASES.get(value.upper(), value) for value in keys)
    return f"keyboard_press(key={key!r})"


def translate_uitars_action(action: str) -> str:
    """Translate a UI-TARS native action to a BrowserGym action call."""
    if action.startswith(("click(point=", "click(start_box=", "click(x=")):
        coords = re.findall(r"-?\d+(?:\.\d+)?", action)
        if len(coords) < 2:
            raise ParseError(f"Could not parse click coordinates from: {action!r}.")
        return f"mouse_click(x={_number(coords[0])}, y={_number(coords[1])})"
    if action.startswith("type(content="):
        return translate_uitars_type_action(action)
    if action.startswith("scroll("):
        match = re.search(
            r"direction\s*=\s*['\"]?(down|up|left|right)", action, re.IGNORECASE
        )
        coords = re.findall(r"-?\d+(?:\.\d+)?", action)
        if match and len(coords) >= 2:
            x, y = (_number(value) for value in coords[:2])
            direction = match.group(1).lower()
            delta_x = x if direction == "right" else -x if direction == "left" else 0
            delta_y = y if direction == "down" else -y if direction == "up" else 0
            return f"scroll({delta_x}, {delta_y})"
    if action.startswith("right_single(point="):
        coords = re.findall(r"-?\d+(?:\.\d+)?", action)
        if len(coords) < 2:
            raise ParseError(
                f"Could not parse right-click coordinates from: {action!r}."
            )
        return (
            f"mouse_click(x={_number(coords[0])}, y={_number(coords[1])}, "
            "button='right')"
        )
    if action.startswith("hotkey(key="):
        match = re.fullmatch(r"hotkey\(key=(['\"])(.*?)\1\)", action, re.DOTALL)
        if not match:
            raise ParseError(f"Could not parse hotkey action: {action!r}.")
        return f"keyboard_press(key={match.group(2)!r})"
    return action


def translate_uitars_type_action(action: str) -> str:
    """Translate UI-TARS ``type(content=...)`` to BrowserGym ``keyboard_type``."""
    match = re.fullmatch(r"type\(content=(['\"])(.*)\1\s*\)", action, re.DOTALL)
    if not match:
        raise ParseError(
            f"Could not parse content from type action: {action!r}. "
            "Expected type(content='text') or type(content=\"text\")."
        )
    raw_content = match.group(2)
    try:
        content = raw_content.encode("utf-8").decode("unicode_escape")
    except UnicodeDecodeError:
        content = raw_content
    return f"keyboard_type(text={content!r})"


def _number(value: str) -> int | float:
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else parsed


# Compatibility for callers that used the old mutating helper.
def uitars_parser(result: dict) -> dict:
    result["action"] = translate_computer_action(result["action"])
    return result
