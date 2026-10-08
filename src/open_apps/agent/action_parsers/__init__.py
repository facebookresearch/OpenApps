"""ActionParser registry. Default is ``uitars`` (preserves the flexible_parser path).

Add a family: write an ``ActionParser`` subclass and register it below.
"""
from .base import ActionParser, ActionParserResult
from .coords import rescale_xy
from .uitars import UITarsActionParser
from .qwen3vl import Qwen3VLActionParser

REGISTRY: dict[str, type[ActionParser]] = {
    "uitars": UITarsActionParser,
    "qwen3vl": Qwen3VLActionParser,
}


def get_action_parser(
    name: str | None, coord_scale: int | None = None
) -> ActionParser:
    """Build the action_parser for ``name``.

    ``coord_scale`` overrides the family's default coordinate space when given;
    None keeps the subclass default (uitars: raw pixels, qwen3vl: 0-1000).
    """
    if not name:
        name = "uitars"
    if name not in REGISTRY:
        raise ValueError(f"Unknown action_parser {name!r}. Available: {sorted(REGISTRY)}")
    cls = REGISTRY[name]
    if coord_scale is None:
        return cls()
    return cls(coord_scale=coord_scale)


__all__ = [
    "ActionParser",
    "ActionParserResult",
    "REGISTRY",
    "get_action_parser",
    "rescale_xy",
]
