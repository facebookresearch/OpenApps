"""UI-TARS action_parser (default): <think>/<action> ReAct grammar.

UI-TARS grounds in raw viewport pixels, so ``coord_scale`` defaults to None and
coordinates pass through untouched. Other models reuse this grammar with a
normalized coordinate grid (Gemma emits a 0-N box); set ``agent.coord_scale``
in their yaml and the same parser converts to pixels.
"""
from __future__ import annotations

from dataclasses import dataclass

from open_apps.agent.utils import flexible_parser

from .base import ActionParser, ActionParserResult


@dataclass
class UITarsActionParser(ActionParser):
    coord_scale: int | None = None

    def parse(self, response: str, viewport: tuple[int, int]) -> ActionParserResult:
        return flexible_parser(
            response,
            rescale=lambda x, y: self.rescale(x, y, viewport),
        )
