"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Tests for the OpenApps MCP layer: the action space (``open_apps.mcp.actions``)
and the app registry (``open_apps.mcp.registry``).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from browsergym.core.action import functions as bg_functions

from open_apps import config_dir
from open_apps.mcp import actions, registry


class FakePage:
    """Minimal async Playwright-page stand-in that records calls."""

    def __init__(self):
        self.calls = []
        self.mouse = self._Mouse(self.calls)
        self.keyboard = self._Keyboard(self.calls)

    class _Mouse:
        def __init__(self, calls):
            self._calls = calls

        async def click(self, x, y, button="left"):
            self._calls.append(("click", x, y, button))

        async def move(self, x, y, steps=1):
            self._calls.append(("move", x, y))

        async def wheel(self, delta_x, delta_y):
            self._calls.append(("wheel", delta_x, delta_y))

    class _Keyboard:
        def __init__(self, calls):
            self._calls = calls

        async def type(self, text, delay=None):
            self._calls.append(("type", text))

        async def press(self, key):
            self._calls.append(("press", key))


class TestActions:
    def test_parse_positional(self):
        assert actions._parse("mouse_click(375, 292)") == ("mouse_click", [375, 292], {})

    def test_parse_keyword(self):
        name, args, kwargs = actions._parse("mouse_click(1, 2, button='right')")
        assert name == "mouse_click"
        assert args == [1, 2]
        assert kwargs == {"button": "right"}

    def test_parse_rejects_non_call(self):
        with pytest.raises(ValueError):
            actions._parse("1 + 2")

    def test_execute_dispatches_to_page(self):
        """BrowserGym action strings drive the matching Playwright calls."""
        page = FakePage()
        asyncio.run(actions.execute(page, "mouse_click(375, 292)"))
        asyncio.run(actions.execute(page, "keyboard_type('Call Mom')"))
        asyncio.run(actions.execute(page, "keyboard_press('Enter')"))
        asyncio.run(actions.execute(page, "scroll(0, 300)"))
        assert ("click", 375, 292, "left") in page.calls
        assert ("type", "Call Mom") in page.calls
        assert ("press", "Enter") in page.calls
        assert ("wheel", 0, 300) in page.calls

    def test_execute_unsupported_action_raises(self):
        with pytest.raises(ValueError):
            asyncio.run(actions.execute(FakePage(), "fill('bid', 'x')"))

    def test_describe_lists_browsergym_actions(self):
        text = actions.describe()
        assert "mouse_click" in text
        assert "scroll" in text


class TestRegistry:
    def test_config_dir_points_at_config(self):
        cfg = config_dir()
        assert cfg.is_dir()
        assert (cfg / "tasks" / "all_tasks.yaml").is_file()

    def test_url_path_for(self):
        assert registry.url_path_for("map") == "/maps"
        assert registry.url_path_for("unknown") == "/unknown"

    def test_config_dir_for(self):
        assert registry.config_dir_for("messages") == "messenger"
        assert registry.config_dir_for("todo") == "todo"

    def test_list_variants_default_first(self):
        variants = registry.list_variants("todo", "appearance")
        assert variants[0] == "default"
        assert "dark_theme" in variants


def _browsergym_calls(fn, *args):
    """Playwright calls BrowserGym's (sync) action function makes, demo off."""
    page = MagicMock()
    bg_functions.page = page
    bg_functions.demo_mode = "off"
    fn(*args)
    return page.mock_calls


def _our_calls(action_str):
    """Playwright calls our (async) executor makes for the same action."""
    page = AsyncMock()
    asyncio.run(actions.execute(page, action_str))
    return list(page.mock_calls)


class TestActionParity:
    """Our async executor must drive the *same* Playwright calls as BrowserGym
    (which is how OpenApps itself executes actions), with ``demo_mode`` off."""

    def test_click(self):
        assert _our_calls("mouse_click(375, 292)") == _browsergym_calls(
            bg_functions.mouse_click, 375, 292
        )

    def test_drag(self):
        assert _our_calls("mouse_drag_and_drop(10, 20, 30, 40)") == _browsergym_calls(
            bg_functions.mouse_drag_and_drop, 10, 20, 30, 40
        )

    def test_scroll(self):
        assert _our_calls("scroll(0, 300)") == _browsergym_calls(bg_functions.scroll, 0, 300)

    def test_type(self):
        assert _our_calls("keyboard_type('hi')") == _browsergym_calls(
            bg_functions.keyboard_type, "hi"
        )

    def test_press(self):
        assert _our_calls("keyboard_press('Enter')") == _browsergym_calls(
            bg_functions.keyboard_press, "Enter"
        )

    def test_noop(self):
        assert _our_calls("noop(500)") == _browsergym_calls(bg_functions.noop, 500)
