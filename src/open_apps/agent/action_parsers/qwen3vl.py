"""Qwen3-VL / Qwen3.6-VL action_parser: <tool_call> JSON parser + 0-1000 coord rescale.

Prompts (the <tools> schema and examples) live in config/agent/Qwen3.6-VL-computer-use.yaml.
Action names mirror UI-TARS's vocabulary; ``type`` maps to keyboard_type and
``wait`` to noop.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from agentlab.llm.llm_utils import ParseError

from .base import ActionParser, ActionParserResult

# Closing tag may be absent on truncation; json.loads decides well-formedness.
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*(?:</tool_call>|$)", re.DOTALL)
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
_ACTION_LINE_RE = re.compile(r"^\s*Action:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass
class Qwen3VLActionParser(ActionParser):
    # The model is prompted with a fictional 1000x1000 screen, so all
    # coordinates and scroll deltas it emits are in [0, 1000).
    coord_scale: int | None = 1000

    def parse(self, response: str, viewport: tuple[int, int]) -> ActionParserResult:
        response = (response or "").strip()
        if not response:
            raise ParseError("Empty response from the model.")

        # Reasoning: <think> block, else the Instruct-style ``Action:`` line
        # (searched before <tool_call> so it can't match inside the payload).
        think_match = _THINK_RE.search(response)
        if think_match:
            think = think_match.group(1).strip()
        else:
            tc_pos = response.find("<tool_call>")
            prefix = response[:tc_pos] if tc_pos >= 0 else response
            action_match = _ACTION_LINE_RE.search(prefix)
            think = action_match.group(1).strip() if action_match else None

        tool_match = _TOOL_CALL_RE.search(response)
        if not tool_match:
            raise ParseError(
                "Expected a <tool_call>{...}</tool_call> block. "
                f"Got: {response[:300]!r}"
            )
        try:
            call = json.loads(tool_match.group(1))
        except json.JSONDecodeError as e:
            raise ParseError(
                f"tool_call payload is not valid JSON ({e}). "
                f"Got: {tool_match.group(1)[:300]!r}"
            )

        args = call.get("arguments") or {}
        if not isinstance(args, dict):
            raise ParseError("tool_call arguments must be a JSON object.")
        bg_action = self._to_browsergym(args.get("action"), args, viewport)
        return {
            "action": bg_action,
            "displayed_action": tool_match.group(0),
            "think": think,
        }

    def _to_browsergym(self, action_name, args: dict, viewport: tuple[int, int]) -> str:
        if action_name == "mouse_click":
            x, y = self._xy(args, viewport)
            return f"mouse_click(x={x}, y={y})"

        if action_name == "mouse_dblclick":
            x, y = self._xy(args, viewport)
            return f"mouse_dblclick(x={x}, y={y})"

        if action_name == "type":
            text = args.get("content", args.get("text"))
            if not isinstance(text, str):
                raise ParseError("type action missing 'content' or content is not a string.")
            return f"keyboard_type(text={text!r})"

        if action_name == "scroll":
            delta = args.get("delta")
            if not (isinstance(delta, (list, tuple)) and len(delta) == 2):
                raise ParseError(
                    f"scroll 'delta' must be a [dx, dy] list, got {delta!r}."
                )
            dx, dy = self._checked_rescale(delta[0], delta[1], viewport)
            return f"scroll({dx}, {dy})"

        if action_name == "wait":
            wait_ms = args.get("wait_ms", 5000)
            try:
                wait_ms = float(wait_ms)
            except (TypeError, ValueError, OverflowError):
                raise ParseError(f"wait wait_ms must be numeric, got {wait_ms!r}.")
            if not math.isfinite(wait_ms) or wait_ms < 0:
                raise ParseError("wait wait_ms must be finite and nonnegative.")
            return f"noop(wait_ms={wait_ms})"

        raise ParseError(
            f"Unsupported action {action_name!r}. Expected one of: "
            "mouse_click, mouse_dblclick, type, scroll, wait."
        )

    def _xy(self, args: dict, viewport: tuple[int, int]) -> tuple[int, int]:
        coord = args.get("coordinate")
        if not (isinstance(coord, (list, tuple)) and len(coord) == 2):
            raise ParseError(f"'coordinate' must be a [x, y] list, got {coord!r}.")
        return self._checked_rescale(coord[0], coord[1], viewport)

    def _checked_rescale(self, x, y, viewport: tuple[int, int]) -> tuple[int, int]:
        try:
            return self.rescale(x, y, viewport)
        except (TypeError, ValueError, OverflowError) as e:
            raise ParseError(f"Coordinates must be finite numbers, got {[x, y]!r}.") from e
