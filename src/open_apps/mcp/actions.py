"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

OpenApps action layer.

The action *space* is BrowserGym's ``HighLevelActionSet(subsets=["coord",
"nav"])`` — the canonical OpenApps action vocabulary (the same coordinate +
navigation family ``open_apps.agent`` uses). We do **not** define our own.
Actions are BrowserGym action strings, e.g.::

    mouse_click(375, 292)        keyboard_type('Call Mom')
    keyboard_press('Enter')      scroll(0, 300)
    mouse_drag_and_drop(10, 20, 200, 40)        goto('/calendar')

``describe()`` (reused from BrowserGym) documents the space for agents.
Strings are parsed with the stdlib ``ast`` (no bespoke grammar). Execution
reproduces ``browsergym.core.action.functions`` one-to-one with
``demo_mode`` off — the same Playwright primitives OpenApps itself runs,
reimplemented for async (verified against BrowserGym in
``tests/test_mcp.py::TestActionParity``). The scroll *amount* is part of
the action (``delta_x, delta_y``) — set by the caller, never defaulted here.
"""

from __future__ import annotations

import ast

from browsergym.core.action.highlevel import HighLevelActionSet


# Canonical OpenApps action space: BrowserGym coordinate + navigation subsets.
_ACTION_SET = HighLevelActionSet(subsets=["coord", "nav"], strict=False)


def describe() -> str:
    """Human/agent-readable description of the action space (from BrowserGym)."""
    return _ACTION_SET.describe(with_long_description=True, with_examples=True)


def _parse(action: str) -> tuple[str, list, dict]:
    """Parse a BrowserGym action string into ``(name, args, kwargs)`` via ast."""
    try:
        expr = ast.parse(action.strip(), mode="eval").body
    except SyntaxError as e:
        raise ValueError(f"Invalid action syntax: {action!r} ({e})") from e
    if not isinstance(expr, ast.Call) or not isinstance(expr.func, ast.Name):
        raise ValueError(f"Action must be a single function call: {action!r}")
    try:
        args = [ast.literal_eval(a) for a in expr.args]
        kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in expr.keywords}
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"Invalid action arguments: {action!r} ({e})") from e
    return expr.func.id, args, kwargs


# --------------------------------------------------------------------------
# Async implementations of the BrowserGym coord+nav functions.
# Signatures mirror browsergym.core.action.functions exactly.


async def _mouse_click(page, x, y, button="left"):
    await page.mouse.click(x, y, button=button)


async def _mouse_dblclick(page, x, y, button="left"):
    await page.mouse.dblclick(x, y, button=button)


async def _mouse_move(page, x, y):
    await page.mouse.move(x, y)


async def _mouse_down(page, x, y, button="left"):
    await page.mouse.move(x, y)
    await page.mouse.down(button=button)


async def _mouse_up(page, x, y, button="left"):
    await page.mouse.move(x, y)
    await page.mouse.up(button=button)


async def _mouse_drag_and_drop(page, from_x, from_y, to_x, to_y):
    await page.mouse.move(from_x, from_y)
    await page.mouse.down()
    await page.mouse.move(to_x, to_y)
    await page.mouse.up()


async def _scroll(page, delta_x, delta_y):
    await page.mouse.wheel(delta_x, delta_y)


async def _keyboard_press(page, key):
    await page.keyboard.press(key)


async def _keyboard_down(page, key):
    await page.keyboard.down(key)


async def _keyboard_up(page, key):
    await page.keyboard.up(key)


async def _keyboard_type(page, text):
    await page.keyboard.type(text, delay=None)


async def _keyboard_insert_text(page, text):
    await page.keyboard.insert_text(text)


async def _goto(page, url):
    await page.goto(url)


async def _go_back(page):
    await page.go_back()


async def _go_forward(page):
    await page.go_forward()


async def _noop(page, wait_ms=1000):
    await page.wait_for_timeout(wait_ms)


_DISPATCH = {
    "mouse_click": _mouse_click,
    "mouse_dblclick": _mouse_dblclick,
    "mouse_move": _mouse_move,
    "mouse_down": _mouse_down,
    "mouse_up": _mouse_up,
    "mouse_drag_and_drop": _mouse_drag_and_drop,
    "scroll": _scroll,
    "keyboard_press": _keyboard_press,
    "keyboard_down": _keyboard_down,
    "keyboard_up": _keyboard_up,
    "keyboard_type": _keyboard_type,
    "keyboard_insert_text": _keyboard_insert_text,
    "goto": _goto,
    "go_back": _go_back,
    "go_forward": _go_forward,
    "noop": _noop,
}


async def execute(page, action: str) -> str:
    """Execute a BrowserGym action string on an async Playwright ``page``.

    Returns the action string (used as a human-readable description).
    Raises ``ValueError`` for unparseable or unsupported actions.
    """
    name, args, kwargs = _parse(action)
    fn = _DISPATCH.get(name)
    if fn is None:
        raise ValueError(
            f"Unsupported action {name!r}. Supported: {sorted(_DISPATCH)}"
        )
    await fn(page, *args, **kwargs)
    return action
