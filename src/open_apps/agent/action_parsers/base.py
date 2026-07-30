"""Per-model-family action_parser contract: response parsing + coordinate conversion.

Prompts live in the agent yaml, not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class ActionParserResult(TypedDict, total=False):
    action: str
    displayed_action: str
    think: str | None


@dataclass
class ActionParser:
    def default_prompts(self) -> dict:
        return {}

    def parse(self, response: str, viewport: tuple[int, int]) -> ActionParserResult:
        """``viewport`` is (width, height) px. Raise ParseError on bad output."""
        raise NotImplementedError
