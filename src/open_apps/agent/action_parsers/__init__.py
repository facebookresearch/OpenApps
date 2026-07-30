"""ActionParser registry. Default is ``uitars`` (preserves the flexible_parser path).

Add a family: write an ``ActionParser`` subclass and register it below.
"""
from .base import ActionParser, ActionParserResult
from .uitars import UITarsActionParser
from .qwen3vl import Qwen3VLActionParser

REGISTRY: dict[str, type[ActionParser]] = {
    "uitars": UITarsActionParser,
    "qwen3vl": Qwen3VLActionParser,
}


def get_action_parser(name: str | None) -> ActionParser:
    if not name:
        name = "uitars"
    if name not in REGISTRY:
        raise ValueError(f"Unknown action_parser {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name]()


__all__ = ["ActionParser", "ActionParserResult", "REGISTRY", "get_action_parser"]
