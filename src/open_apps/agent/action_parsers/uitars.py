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
        # Pass no hook at all when there is nothing to convert: flexible_parser
        # treats "no rescale" as "leave every coordinate exactly as written",
        # which is stricter than converting through an identity (that would
        # still round floats and rewrite already-valid browsergym calls).
        rescale = None
        if self.coord_scale:
            rescale = lambda x, y: self.rescale(x, y, viewport)  # noqa: E731
        return flexible_parser(response, rescale=rescale)
