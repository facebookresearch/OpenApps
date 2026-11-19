"""Fara-7B action_parser: Qwen2.5-VL ``computer_use`` <tool_call> JSON.

Fara answers with a short thought followed by one
``<tool_call>{"name": "computer_use", "arguments": {...}}</tool_call>``.

Screen size and coordinates: the Qwen2.5-VL processor resizes every image to
a multiple of 28 px per side (``smart_resize``; 1920x1080 -> 1932x1092), and
microsoft/fara's harness states that resized size as the screen resolution
in the system prompt, which ``render_system_prompt`` reproduces. Its
coordinates, however, come out in viewport pixels at common screen sizes,
not in the resized image's: on synthetic targets served by vLLM, 1920x1080
and 1440x900 clicks were off by ~2 px read as viewport pixels and by
~7-11 px read as resized-image pixels (at 390x844 the two readings differ
by under 1 px). So ``rescale`` passes them through, clamped to the
viewport.

The prompt (the <tools> schema) lives in config/agent/Fara-7B.yaml. The
action semantics follow microsoft/fara's FaraAgent.execute_action:
``type`` clicks its coordinate first and presses Enter unless told not to,
``scroll`` wheels a fixed 400 px, ``key`` presses its keys as one chord.
"""
from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass

from agentlab.llm.llm_utils import ParseError

from .base import ActionParser, ActionParserResult

# Closing tag may be absent on truncation; the decoder decides well-formedness.
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*(?:</tool_call>|$)", re.DOTALL)
_THINK_RE = re.compile(r"</?think>", re.IGNORECASE)

# FaraAgent's scroll is a page_up / page_down of this many pixels, whatever
# ``pixels`` the model asked for.
SCROLL_PX = 400
# FaraAgent's default for ``wait`` without ``time``.
DEFAULT_WAIT_S = 3.0
MAX_WAIT_S = 10.0

# Fara's key names (lowercased) -> Playwright's, after microsoft/fara's
# CUA_KEY_TO_PLAYWRIGHT_KEY. Anything else passes through as written.
_KEY_NAMES = {
    "alt": "Alt", "option": "Alt",
    "arrowdown": "ArrowDown", "down": "ArrowDown",
    "arrowleft": "ArrowLeft", "left": "ArrowLeft",
    "arrowright": "ArrowRight", "right": "ArrowRight",
    "arrowup": "ArrowUp", "up": "ArrowUp",
    "backspace": "Backspace", "capslock": "CapsLock",
    "cmd": "Meta", "meta": "Meta", "super": "Meta", "win": "Meta",
    "ctrl": "Control", "control": "Control",
    "delete": "Delete", "end": "End", "enter": "Enter", "return": "Enter",
    "esc": "Escape", "escape": "Escape", "home": "Home", "insert": "Insert",
    "pagedown": "PageDown", "pageup": "PageUp", "shift": "Shift",
    "space": "Space", " ": "Space", "tab": "Tab",
}

SUPPORTED_ACTIONS = (
    "left_click", "mouse_move", "type", "key", "scroll", "wait",
    "history_back", "pause_and_memorize_fact", "terminate",
)
# In Fara's native schema but left out of the prompt: the apps are local, so
# there is no address bar or search engine to send the model to.
_UNAVAILABLE_ACTIONS = ("visit_url", "web_search")


def smart_resize(
    height: int,
    width: int,
    factor: int = 28,
    min_pixels: int = 3136,
    max_pixels: int = 12845056,
) -> tuple[int, int]:
    """Qwen2.5-VL's image resize: (height, width) -> multiples of ``factor``.

    Same arithmetic as transformers' Qwen2VLImageProcessor (and microsoft/fara's
    copy); the defaults are Fara-7B's preprocessor_config.json.
    """
    h_bar = max(factor, round(height / factor) * factor)
    w_bar = max(factor, round(width / factor) * factor)
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = math.floor(height / beta / factor) * factor
        w_bar = math.floor(width / beta / factor) * factor
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = math.ceil(height * beta / factor) * factor
        w_bar = math.ceil(width * beta / factor) * factor
    return h_bar, w_bar


@dataclass
class FaraActionParser(ActionParser):
    # Viewport pixels (see the module docstring). Setting agent.coord_scale
    # switches to the shared normalized-grid rescale_xy.
    coord_scale: int | None = None
    # The image processor the server runs (Fara-7B's preprocessor_config.json),
    # for the screen size the prompt states.
    min_pixels: int = 3136
    max_pixels: int = 12845056
    factor: int = 28

    def model_screen(self, viewport: tuple[int, int]) -> tuple[int, int]:
        """(width, height) of the screenshot as the model sees it."""
        vw, vh = viewport
        h, w = smart_resize(vh, vw, self.factor, self.min_pixels, self.max_pixels)
        return w, h

    def render_system_prompt(self, system_prompt: str, viewport: tuple[int, int]) -> str:
        w, h = self.model_screen(viewport)
        return system_prompt.replace("{display_width_px}", str(w)).replace(
            "{display_height_px}", str(h)
        )

    def rescale(self, x: float, y: float, viewport: tuple[int, int]) -> tuple[int, int]:
        if self.coord_scale:
            return super().rescale(x, y, viewport)
        vw, vh = viewport
        px, py = int(round(float(x))), int(round(float(y)))
        # The prompt states a screen a few px larger than the viewport; a click
        # past the edge lands on the last pixel rather than on nothing.
        return min(max(px, 0), vw - 1), min(max(py, 0), vh - 1)

    def parse(self, response: str, viewport: tuple[int, int]) -> ActionParserResult:
        response = (response or "").strip()
        if not response:
            raise ParseError("Empty response from the model.")

        tool_match = _TOOL_CALL_RE.search(response)
        if not tool_match:
            raise ParseError(
                "Expected one <tool_call>{...}</tool_call> block calling computer_use. "
                f"Got: {response[:300]!r}"
            )
        think = _THINK_RE.sub("", response[: tool_match.start()]).strip() or None

        args = self._arguments(tool_match.group(1))
        action = self._to_browsergym(args.get("action"), args, think, viewport)
        return {
            "action": action,
            "displayed_action": tool_match.group(0),
            "think": think,
        }

    @staticmethod
    def _arguments(payload: str) -> dict:
        try:
            call = json.loads(payload)
        except json.JSONDecodeError as e:
            # FaraAgent's own fallback: Python-literal dicts (single quotes,
            # True/False) decode with literal_eval.
            try:
                call = ast.literal_eval(payload)
            except (ValueError, SyntaxError):
                raise ParseError(
                    f"tool_call payload is not valid JSON ({e}). Got: {payload[:300]!r}"
                )
        if not isinstance(call, dict):
            raise ParseError(f"tool_call payload must be a JSON object. Got: {payload[:300]!r}")
        args = call.get("arguments", call)
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError as e:
                raise ParseError(f"tool_call 'arguments' is not valid JSON ({e}).")
        if not isinstance(args, dict):
            raise ParseError(f"tool_call 'arguments' must be an object, got {args!r}.")
        return args

    def _to_browsergym(
        self, name, args: dict, think: str | None, viewport: tuple[int, int]
    ) -> str:
        if name in ("left_click", "click"):
            x, y = self._xy(args.get("coordinate"), viewport)
            return f"mouse_click(x={x}, y={y})"

        if name in ("mouse_move", "hover"):
            x, y = self._xy(args.get("coordinate"), viewport)
            return f"mouse_move(x={x}, y={y})"

        if name in ("type", "input_text"):
            text = args.get("text", args.get("text_value"))
            if text is None:
                raise ParseError("type action missing 'text'.")
            coord = args.get("coordinate")
            x, y = self._xy(coord, viewport) if coord is not None else (None, None)
            press_enter = self._flag(args, "press_enter", True)
            clear = self._flag(args, "delete_existing_text", False)
            return (
                f"click_and_type(x={x}, y={y}, text={str(text)!r}, "
                f"press_enter={press_enter}, clear={clear})"
            )

        if name in ("key", "keypress"):
            keys = args.get("keys", args.get("key"))
            if isinstance(keys, str):
                keys = [keys]
            if not (isinstance(keys, list) and keys and all(isinstance(k, str) and k for k in keys)):
                raise ParseError(f"key action needs a non-empty 'keys' list, got {keys!r}.")
            chord = "+".join(_KEY_NAMES.get(k.lower(), k) for k in keys)
            return f"keyboard_press(key={chord!r})"

        if name == "scroll":
            try:
                pixels = float(args.get("pixels", 0))
            except (TypeError, ValueError):
                raise ParseError(f"scroll 'pixels' must be a number, got {args.get('pixels')!r}.")
            if pixels == 0:
                raise ParseError("scroll needs non-zero 'pixels' (positive up, negative down).")
            # Fara: positive pixels scroll up; BrowserGym: positive dy scrolls down.
            return f"scroll(0, {-SCROLL_PX if pixels > 0 else SCROLL_PX})"

        if name in ("wait", "sleep"):
            seconds = args.get("time", args.get("duration", DEFAULT_WAIT_S))
            try:
                seconds = min(max(float(seconds), 0.0), MAX_WAIT_S)
            except (TypeError, ValueError):
                raise ParseError(f"wait 'time' must be a number of seconds, got {seconds!r}.")
            return f"noop(wait_ms={seconds * 1000:g})"

        if name == "history_back":
            return "go_back()"

        if name == "pause_and_memorize_fact":
            # The fact stays in the action history the prompt replays.
            return "noop(wait_ms=0)"

        if name in ("terminate", "stop"):
            status = str(args.get("status", "success")).lower()
            if status == "failure":
                return f"report_infeasible(reason={(think or 'Task failed.')!r})"
            return f"send_msg_to_user(text={(think or 'Task completed.')!r})"

        if name in _UNAVAILABLE_ACTIONS:
            raise ParseError(
                f"Action {name!r} is not available here: there is no address bar "
                "or web search. Use the apps on screen. Available actions: "
                + ", ".join(SUPPORTED_ACTIONS) + "."
            )
        raise ParseError(
            f"Unsupported action {name!r}. Expected one of: "
            + ", ".join(SUPPORTED_ACTIONS) + "."
        )

    @staticmethod
    def _flag(args: dict, key: str, default: bool) -> bool:
        value = args.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)

    def _xy(self, coord, viewport: tuple[int, int]) -> tuple[int, int]:
        if isinstance(coord, str):
            coord = re.findall(r"-?\d+(?:\.\d+)?", coord)
        if not (isinstance(coord, (list, tuple)) and len(coord) == 2):
            raise ParseError(f"'coordinate' must be a [x, y] list, got {coord!r}.")
        try:
            return self.rescale(coord[0], coord[1], viewport)
        except (TypeError, ValueError):
            raise ParseError(f"'coordinate' must hold two numbers, got {coord!r}.")
