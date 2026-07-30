"""UI-TARS action_parser (default): <think>/<action> ReAct, raw pixel coordinates."""
from __future__ import annotations

from open_apps.agent.utils import flexible_parser

from .base import ActionParser, ActionParserResult


class UITarsActionParser(ActionParser):
    def parse(self, response: str, viewport: tuple[int, int]) -> ActionParserResult:
        return flexible_parser(response)
