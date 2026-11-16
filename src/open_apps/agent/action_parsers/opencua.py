"""OpenCUA-7B / -32B action_parser: L2 CoT, then PyAutoGUI code.

OpenCUA answers in xlang-ai/OSWorld's L2 format::

    ## Thought:
    ...
    ## Action:
    Click the "Add" button.
    ## Code:
    ```python
    pyautogui.click(x=1443, y=343)
    ```

The code block holds one step: usually one ``pyautogui`` call, sometimes a
short sequence its training data records as one step (``write`` then
``press('enter')``, ``moveTo`` then ``scroll`` or ``dragTo``), or a
``computer.triple_click`` / ``computer.wait`` / ``computer.terminate``
function. BrowserGym runs one action per step, so each recognised sequence
maps onto one action and anything else is a ParseError.

Only the first step of a reply counts. Given its history as text in the user
message rather than as earlier turns, OpenCUA sometimes writes the next step
and goes on to imagine further ones ("# Step 3", or just another "## Thought:"
after the code); the reply is cut at the first such header after some code,
and the first code block is the action.

History: OpenCUA was trained with the last three screenshots and their steps
as chat turns and older steps as text ("# Step N:" / "## Action:"), with a
reflective Thought each turn. This harness sends one user message: the task,
the earlier steps as text, the current screenshot. In the shared history
format (each step's thought and action under "### Thoughts:" / "### Action:")
OpenCUA copied that layout into its replies, often dropping the code and
continuing into imagined steps, so ``render_history`` writes the earlier steps
in OpenCUA's own text template (OSWorld's action_history: the Action line of
each step) followed by its instruction line.

Coordinates: absolute pixels on the screenshot as the Qwen2.5-VL processor
resizes it (``smart_resize``: multiples of 28 px per side, 1920x1080 ->
1932x1092), as xlang-ai/OSWorld's ``qwen25`` coordinate type reads them, and
unlike Fara-7B (see fara.py). Measured on synthetic targets served by vLLM
(mean click error, read as resized-image pixels vs as viewport pixels):
OpenCUA-7B 6-8 vs 10 px at 1920x1080, 3-6 vs 9-15 px at 1946x1050, 1440x900,
1280x800 and 2560x1440; OpenCUA-32B 5-7 vs 9-13 px at those four, 6-7 vs
6-8 px at 1920x1080. At 390x844 the two readings differ by under 3 px. So
``rescale`` scales each axis by viewport / resized size.
A pair inside [0, 1] is relative, as OSWorld's projection treats it.

The prompt lives in config/agent/OpenCUA-7B.yaml (and -32B).
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass

from agentlab.llm.llm_utils import ParseError

from .base import ActionParser, ActionParserResult
from .fara import _KEY_NAMES, smart_resize

_CODE_BLOCK_RE = re.compile(r"```(?:code|python)?[ \t]*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
# Headers that start a new step: "# Step 3", "## Thought:", "Observation:".
_STEP_HEADER_RE = re.compile(
    r"^[ \t]*(?:#+[ \t]*Step\b[^\n]*|(?:#+[ \t]*)?(?:Observation|Thoughts?)[ \t]*:[^\n]*)$",
    re.MULTILINE | re.IGNORECASE,
)
_CALL_LINE_RE = re.compile(r"^[ \t`]*(?:pyautogui|computer)\.", re.MULTILINE)
# Section headers as OSWorld's parser reads them ("## Thought:"), plus the
# bare "Thought:" the system prompt's format shows and the "### Thoughts:"
# of the history the prompt replays. A section ends at the next header, a
# code fence or a bare line of code.
_SECTION_RE = (
    r"^[ \t]*(?:#+[ \t]*)?{name}s?[ \t]*:?[ \t]*\n?(.*?)"
    r"(?=^[ \t]*(?:#+[ \t]*)?(?:Observation|Thoughts?|Action|Code)[ \t]*:|```"
    r"|^[ \t]*(?:pyautogui|computer)\.|\Z)"
)
_THOUGHT_RE = re.compile(_SECTION_RE.format(name="Thought"), re.DOTALL | re.MULTILINE | re.IGNORECASE)
_ACTION_RE = re.compile(_SECTION_RE.format(name="Action"), re.DOTALL | re.MULTILINE | re.IGNORECASE)

# xlang-ai/OSWorld's OpenCUA prompt templates (STEP_TEMPLATE,
# ACTION_HISTORY_TEMPLATE, INSTRUTION_TEMPLATE's closing line).
_STEP_TEMPLATE = "# Step {n}:\n## Action:\n{action}\n"
_NEXT_MOVE = (
    "Please generate the next move according to the screenshot, task instruction "
    "and previous steps (if provided).\n"
)

# One pyautogui.scroll click, in pixels: Chrome scrolls about this much per
# wheel notch on Linux, where OpenCUA's OSWorld runs execute its code.
SCROLL_PX_PER_CLICK = 100
# computer.wait sleeps 20 s in OSWorld (installs, slow commands); the local
# apps settle in well under that.
WAIT_MS = 3000

SUPPORTED_ACTIONS = (
    "pyautogui.click", "pyautogui.doubleClick", "pyautogui.tripleClick",
    "pyautogui.rightClick", "pyautogui.moveTo", "pyautogui.dragTo",
    "pyautogui.write", "pyautogui.press", "pyautogui.hotkey", "pyautogui.scroll",
    "pyautogui.hscroll", "computer.triple_click", "computer.wait",
    "computer.terminate",
)

# Positional parameter names of the pyautogui functions used here.
_POSITIONAL = {
    "click": ("x", "y", "clicks", "interval", "button"),
    "doubleClick": ("x", "y", "interval", "button"),
    "tripleClick": ("x", "y", "interval", "button"),
    "rightClick": ("x", "y"),
    "middleClick": ("x", "y"),
    "moveTo": ("x", "y"),
    "dragTo": ("x", "y"),
    "triple_click": ("x", "y"),
    "write": ("message", "interval"),
    "typewrite": ("message", "interval"),
    "press": ("keys", "presses", "interval"),
    "scroll": ("clicks", "x", "y"),
    "hscroll": ("clicks", "x", "y"),
    "terminate": ("status", "answer"),
}

# Statements a code block may carry around its calls that do nothing here.
_IGNORED_CALLS = {("time", "sleep"), ("pyautogui", "sleep")}


@dataclass
class _Call:
    module: str
    name: str
    args: dict

    def __str__(self) -> str:
        return f"{self.module}.{self.name}"


@dataclass
class OpenCUAActionParser(ActionParser):
    coord_scale: int | None = None
    # The image processor the server runs (OpenCUA's preprocessor_config.json).
    min_pixels: int = 3136
    max_pixels: int = 12845056
    factor: int = 28

    def model_screen(self, viewport: tuple[int, int]) -> tuple[int, int]:
        """(width, height) of the screenshot as the model sees it."""
        vw, vh = viewport
        h, w = smart_resize(vh, vw, self.factor, self.min_pixels, self.max_pixels)
        return w, h

    def rescale(self, x: float, y: float, viewport: tuple[int, int]) -> tuple[int, int]:
        if self.coord_scale:
            return super().rescale(x, y, viewport)
        vw, vh = viewport
        x, y = float(x), float(y)
        if 0 <= x <= 1 and 0 <= y <= 1:
            # OSWorld's qwen25 projection reads a 0-1 pair as relative.
            px, py = x * vw, y * vh
        else:
            mw, mh = self.model_screen(viewport)
            px, py = x * vw / mw, y * vh / mh
        return (
            min(max(int(round(px)), 0), vw - 1),
            min(max(int(round(py)), 0), vh - 1),
        )

    def render_history(self, actions: list, thoughts: list) -> str:
        steps = [
            _STEP_TEMPLATE.format(n=i + 1, action=a if a else "(no valid action)")
            for i, a in enumerate(actions)
        ]
        return "\n" + "\n".join(steps + [_NEXT_MOVE])

    def parse(self, response: str, viewport: tuple[int, int]) -> ActionParserResult:
        response = (response or "").strip()
        if not response:
            raise ParseError("Empty response from the model.")

        response = self._first_step(response)
        code = self._code(response)
        thought = self._section(_THOUGHT_RE, response)
        action_text = self._section(_ACTION_RE, response)
        calls = self._calls(code)
        action = self._to_browsergym(calls, thought, viewport)
        # The history replays the Action line (the code when there is none).
        shown = action_text or code
        return {"action": action, "displayed_action": shown, "think": thought}

    @staticmethod
    def _first_step(response: str) -> str:
        """The reply up to the first step header that follows some code."""
        headers = list(_STEP_HEADER_RE.finditer(response))
        for h in headers:
            # The first header that follows some code ends the step.
            before = response[: h.start()]
            if "```" in before or _CALL_LINE_RE.search(before):
                return before.strip()
        return response

    @staticmethod
    def _section(pattern: re.Pattern, response: str) -> str | None:
        m = pattern.search(response)
        text = m.group(1).strip() if m else ""
        return text or None

    @staticmethod
    def _code(response: str) -> str:
        blocks = [b.strip() for b in _CODE_BLOCK_RE.findall(response) if b.strip()]
        if blocks:
            return blocks[0]
        # No fenced block (or an unclosed one): the agentnetbench fallback,
        # lines that start with a call.
        lines = [
            ln.strip().strip("`") for ln in response.splitlines()
            if ln.strip().strip("`").startswith(("pyautogui.", "computer."))
        ]
        if not lines:
            raise ParseError(
                "Expected the action as PyAutoGUI code in a ```python``` block, e.g. "
                "pyautogui.click(x=100, y=200), or computer.terminate(status='success'). "
                f"Got: {response[:300]!r}"
            )
        return "\n".join(lines)

    @staticmethod
    def _calls(code: str) -> list[_Call]:
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise ParseError(f"The code block is not valid Python ({e.msg}). Got: {code[:300]!r}")
        calls = []
        for stmt in tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                continue
            call = stmt.value if isinstance(stmt, ast.Expr) else None
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
            ):
                raise ParseError(
                    "The code block may only call pyautogui.* or computer.* functions. "
                    f"Got: {ast.unparse(stmt)[:200]!r}"
                )
            module, name = call.func.value.id, call.func.attr
            if (module, name) in _IGNORED_CALLS:
                continue
            params = _POSITIONAL.get(name, ())
            if name in _POSITIONAL and len(call.args) > len(params):
                raise ParseError(f"Too many positional arguments to {module}.{name}.")
            try:
                if name == "hotkey":
                    args = {"keys": [ast.literal_eval(a) for a in call.args]}
                else:
                    args = {p: ast.literal_eval(a) for p, a in zip(params, call.args)}
                for kw in call.keywords:
                    if kw.arg is None:
                        raise ValueError("**kwargs")
                    args[kw.arg] = ast.literal_eval(kw.value)
            except (ValueError, SyntaxError, TypeError):
                raise ParseError(
                    f"Arguments to {module}.{name} must be literals. Got: {ast.unparse(call)[:200]!r}"
                )
            calls.append(_Call(module, name, args))
        if not calls:
            raise ParseError(f"No pyautogui or computer call in the code block. Got: {code[:300]!r}")
        return calls

    def _to_browsergym(
        self, calls: list[_Call], thought: str | None, viewport: tuple[int, int]
    ) -> str:
        first = calls[0]
        if len(calls) == 1:
            return self._single(first, thought, viewport)

        names = [(c.module, c.name) for c in calls]
        tail = calls[1:]
        # click, then write [, then Enter]: one click_and_type.
        if (
            first.module == "pyautogui" and first.name == "click"
            and tail[0].name in ("write", "typewrite")
            and (len(tail) == 1 or (len(tail) == 2 and self._is_enter(tail[1])))
        ):
            x, y = self._xy(first, viewport)
            return self._type(tail[0], x, y, press_enter=len(tail) == 2)
        # write, then Enter (its training data records a submitted entry so).
        if len(calls) == 2 and first.name in ("write", "typewrite") and self._is_enter(calls[1]):
            return self._type(first, None, None, press_enter=True)
        # moveTo, then scroll / hscroll there.
        if len(calls) == 2 and first.name == "moveTo" and calls[1].name in ("scroll", "hscroll"):
            x, y = self._xy(first, viewport)
            dx, dy = self._scroll_delta(calls[1], viewport)
            return f"scroll_at(x={x}, y={y}, delta_x={dx}, delta_y={dy})"
        # moveTo, then dragTo: a drag between the two points.
        if len(calls) == 2 and first.name == "moveTo" and calls[1].name == "dragTo":
            x0, y0 = self._xy(first, viewport)
            x1, y1 = self._xy(calls[1], viewport)
            return f"mouse_drag_and_drop(from_x={x0}, from_y={y0}, to_x={x1}, to_y={y1})"
        raise ParseError(
            "Only one action can run per step. Output a single pyautogui call "
            "(or write then press('enter')). Got: " + ", ".join(f"{m}.{n}" for m, n in names)
        )

    def _single(self, call: _Call, thought: str | None, viewport: tuple[int, int]) -> str:
        name = call.name
        if call.module == "computer":
            if name == "terminate":
                status = str(call.args.get("status", "success")).lower()
                answer = call.args.get("answer")
                if status.startswith("fail"):
                    return f"report_infeasible(reason={(answer or thought or 'Task failed.')!r})"
                return f"send_msg_to_user(text={str(answer or thought or 'Task completed.')!r})"
            if name == "wait":
                return f"noop(wait_ms={WAIT_MS})"
            if name == "triple_click":
                x, y = self._xy(call, viewport)
                return f"mouse_tripleclick(x={x}, y={y})"
        elif call.module == "pyautogui":
            if name in ("click", "doubleClick", "tripleClick", "rightClick", "middleClick"):
                x, y = self._xy(call, viewport)
                button = {"rightClick": "right", "middleClick": "middle"}.get(
                    name, str(call.args.get("button", "left")).lower()
                )
                if button not in ("left", "right", "middle"):
                    raise ParseError(f"Unknown mouse button {button!r}.")
                clicks = {"doubleClick": 2, "tripleClick": 3}.get(name, call.args.get("clicks", 1))
                if clicks == 3:
                    return f"mouse_tripleclick(x={x}, y={y})"
                fn = "mouse_dblclick" if clicks == 2 else "mouse_click"
                suffix = "" if button == "left" else f", button={button!r}"
                return f"{fn}(x={x}, y={y}{suffix})"
            if name == "moveTo":
                x, y = self._xy(call, viewport)
                return f"mouse_move(x={x}, y={y})"
            if name in ("write", "typewrite"):
                return self._type(call, None, None, press_enter=False)
            if name in ("press", "hotkey"):
                return self._keys(call)
            if name in ("scroll", "hscroll"):
                dx, dy = self._scroll_delta(call, viewport)
                if "x" in call.args and "y" in call.args:
                    x, y = self._xy(call, viewport)
                    return f"scroll_at(x={x}, y={y}, delta_x={dx}, delta_y={dy})"
                return f"scroll({dx}, {dy})"
            if name == "dragTo":
                raise ParseError(
                    "pyautogui.dragTo needs a start point: precede it with "
                    "pyautogui.moveTo(x, y) in the same code block."
                )
        raise ParseError(
            f"Unsupported action {call}. Expected one of: " + ", ".join(SUPPORTED_ACTIONS) + "."
        )

    def _type(self, call: _Call, x, y, press_enter: bool) -> str:
        text = call.args.get("message", call.args.get("text"))
        if not isinstance(text, str):
            raise ParseError(f"{call} needs the text to type as a string, got {text!r}.")
        if x is None and not press_enter:
            return f"keyboard_type(text={text!r})"
        return f"click_and_type(x={x}, y={y}, text={text!r}, press_enter={press_enter})"

    @staticmethod
    def _key_list(call: _Call) -> list[str]:
        keys = call.args.get("keys", call.args.get("key"))
        if isinstance(keys, str):
            keys = [keys]
        if isinstance(keys, tuple):
            keys = list(keys)
        if call.name == "hotkey" and keys and isinstance(keys[0], list):
            keys = keys[0]  # hotkey(['ctrl', 'c'])
        if not (isinstance(keys, list) and keys and all(isinstance(k, str) and k for k in keys)):
            raise ParseError(f"{call} needs one or more key names, got {keys!r}.")
        return keys

    @classmethod
    def _is_enter(cls, call: _Call) -> bool:
        if call.module != "pyautogui" or call.name not in ("press", "hotkey"):
            return False
        try:
            keys = cls._key_list(call)
        except ParseError:
            return False
        return [k.lower() for k in keys] in (["enter"], ["return"])

    def _keys(self, call: _Call) -> str:
        keys = [_playwright_key(k) for k in self._key_list(call)]
        presses = call.args.get("presses", 1)
        if call.name == "hotkey":
            # One chord: every key held, released in reverse. A capital letter
            # would add Shift in Playwright; pyautogui's hotkey('ctrl', 'F') is Ctrl+F.
            keys = [k.lower() if len(k) == 1 else k for k in keys]
            return f"keyboard_press(key={'+'.join(keys)!r})"
        # pyautogui.press presses a list of keys one after another.
        if len(keys) > 1 and len(set(keys)) > 1:
            raise ParseError(
                f"{call} presses several different keys in turn; press one key "
                "(or one hotkey chord) per step."
            )
        if not isinstance(presses, int) or presses < 1:
            raise ParseError(f"{call}: 'presses' must be a positive integer, got {presses!r}.")
        count = presses * len(keys)
        if count == 1:
            return f"keyboard_press(key={keys[0]!r})"
        return f"keyboard_press_repeat(key={keys[0]!r}, count={count})"

    def _scroll_delta(self, call: _Call, viewport: tuple[int, int]) -> tuple[int, int]:
        clicks = call.args.get("clicks")
        if isinstance(clicks, bool) or not isinstance(clicks, (int, float)) or clicks == 0:
            raise ParseError(
                f"{call} needs a non-zero number of clicks (positive up, negative down), got {clicks!r}."
            )
        vw, vh = viewport
        if call.name == "hscroll":
            # pyautogui: positive hscroll scrolls right, as BrowserGym's dx does.
            return int(max(-vw, min(vw, clicks * SCROLL_PX_PER_CLICK))), 0
        # pyautogui: positive scrolls up; BrowserGym: positive dy scrolls down.
        return 0, int(max(-vh, min(vh, -clicks * SCROLL_PX_PER_CLICK)))

    def _xy(self, call: _Call, viewport: tuple[int, int]) -> tuple[int, int]:
        x, y = call.args.get("x"), call.args.get("y")
        if isinstance(x, (tuple, list)) and len(x) == 2 and y is None:
            x, y = x  # click((x, y))
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (x, y)):
            raise ParseError(f"{call} needs numeric x and y, got x={x!r}, y={y!r}.")
        if not all(math.isfinite(v) and v >= 0 for v in (x, y)):
            raise ParseError(f"{call}: x and y must be non-negative, got x={x!r}, y={y!r}.")
        return self.rescale(x, y, viewport)


def _playwright_key(key: str) -> str:
    """A pyautogui key name -> Playwright's (``ctrl`` -> ``Control``)."""
    lower = key.lower()
    if lower in _KEY_NAMES:
        return _KEY_NAMES[lower]
    if lower in ("command",):
        return "Meta"
    if re.fullmatch(r"f\d{1,2}", lower):
        return lower.upper()
    return key
