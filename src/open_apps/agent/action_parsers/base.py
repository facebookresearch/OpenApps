"""Per-model-family action_parser contract: response parsing + coordinate conversion.

Prompts live in the agent yaml, not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from .coords import rescale_xy


class ActionParserResult(TypedDict, total=False):
    action: str
    displayed_action: str
    think: str | None


@dataclass
class ActionParser:
    # Coordinate convention of this model family: None = raw viewport pixels,
    # N = normalized [0, N) grid. Subclasses override the default; an agent yaml
    # can override the subclass via ``agent.coord_scale``. See coords.rescale_xy.
    coord_scale: int | None = None

    def default_prompts(self) -> dict:
        return {}

    def parse(self, response: str, viewport: tuple[int, int]) -> ActionParserResult:
        """``viewport`` is (width, height) px. Raise ParseError on bad output."""
        raise NotImplementedError

    def rescale(self, x: float, y: float, viewport: tuple[int, int]) -> tuple[int, int]:
        """Convert one model-space (x, y) pair into viewport pixels."""
        return rescale_xy(x, y, self.coord_scale, viewport)
