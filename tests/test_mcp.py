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
        # Per-app structure group.
        layouts = registry.list_variants("todo", "layout")
        assert layouts[0] == "default"
        assert "kanban_board" in layouts
        # Shared design-token theme group (app_name ignored).
        themes = registry.list_variants("todo", "theme")
        assert themes[0] == "default"
        assert "solarized" in themes

    def test_list_variants_rejects_removed_appearance_group(self):
        # Must not fall through to the missing-dir ``["default"]`` answer: a
        # caller sampling variations would silently get a single-variant sweep.
        with pytest.raises(ValueError, match="appearance group was removed"):
            registry.list_variants("todo", "appearance")


class TestAppearanceMigration:
    """The removed ``appearance`` group, kept bindable for existing clients."""

    def test_every_legacy_stem_maps_to_a_real_variant(self):
        themes = set(registry.list_variants("todo", "theme"))
        # Layouts are per-app and the legacy stems came from different apps
        # (kanban_board from todo, the logo ones from start_page), so pool them.
        layouts = {
            p.stem
            for group_dir in (config_dir() / "apps").glob("*/layout")
            for p in group_dir.glob("*.yaml")
        }
        for stem, (group, target) in registry.APPEARANCE_MIGRATION.items():
            pool = themes if group == "theme" else layouts
            assert target in pool, f"appearance={stem} -> {group}={target}"

    @pytest.mark.parametrize(
        "appearance,expected",
        [
            ("default", ("default", None)),
            ("dark_theme", ("dark", None)),
            ("black_and_white", ("mono", None)),
            ("challenging_font", ("challenging_font", None)),
            ("colorblind_access", ("colorblind", None)),
            ("kanban_board", (None, "kanban_board")),
            ("broken_logos", (None, "broken_logos")),
            ("clickable_logos", (None, "clickable_logos")),
        ],
    )
    def test_maps_onto_theme_or_layout(self, appearance, expected):
        with pytest.deprecated_call():
            assert registry.migrate_appearance(appearance) == expected

    def test_preserves_the_untouched_axis(self):
        with pytest.deprecated_call():
            assert registry.migrate_appearance("dark_theme", layout="kanban_board") == (
                "dark",
                "kanban_board",
            )

    def test_agreeing_value_is_not_a_conflict(self):
        with pytest.deprecated_call():
            assert registry.migrate_appearance("dark_theme", theme="dark") == (
                "dark",
                None,
            )

    def test_conflicting_value_raises(self):
        with pytest.raises(ValueError, match="conflicts with"):
            registry.migrate_appearance("dark_theme", theme="solarized")

    def test_unknown_stem_raises_with_the_known_values(self):
        with pytest.raises(ValueError, match="unknown appearance variant"):
            registry.migrate_appearance("neon")

    def test_reconfigure_tool_schema_still_binds_appearance(self):
        # The published tool schema is the contract clients validate against:
        # dropping the parameter fails their call before it reaches us.
        from open_apps.mcp import server

        tools = asyncio.run(server.mcp.list_tools())
        schema = next(t for t in tools if t.name == "reconfigure").inputSchema
        assert {"theme", "layout", "content", "seed", "extras"} <= set(
            schema["properties"]
        )
        assert "appearance" in schema["properties"]
        assert "appearance" not in schema.get("required", [])


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


class TestEndToEnd:
    """Drive the todo app through the MCP Session and check the task scores.

    The MCP analogue of ``test_apps.py::test_state_comparison_for_add_todo``:
    instead of comparing against a recorded state, it resets, clicks the
    new-todo field, types "Call Mom" and presses Enter, then asserts the
    bound ``AddToDoTask`` reaches reward 1.0 — a worked example for building
    on the MCP server. Skipped if a browser isn't available.
    """

    def test_add_todo_scores_reward(self):
        from open_apps.mcp.session import Session
        from open_apps.tasks.tasks import AddToDoTask

        async def run() -> float:
            session = Session("todo")
            try:
                await session.start()
            except Exception as e:  # e.g. chromium not installed
                await session.close()
                pytest.skip(f"browser unavailable: {e}")
            try:
                await session.reset()
                session.set_task(
                    AddToDoTask(
                        goal="Add 'Call Mom' to my todo list.",
                        todo_name="Call Mom",
                        is_done=False,
                    )
                )
                # Click the new-todo input at its real pixel centre, type, submit.
                await session.page.wait_for_selector("#new-title", timeout=5000)
                box = await session.page.locator("#new-title").bounding_box()
                cx, cy = int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)
                await session.act(f"mouse_click({cx}, {cy})")
                await session.act("keyboard_type('Call Mom')")
                obs = await session.act("keyboard_press('Enter')")
                return obs.reward
            finally:
                await session.close()

        assert asyncio.run(run()) == 1.0
