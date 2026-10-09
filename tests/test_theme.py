"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Unit tests for shared design-token theming (open_apps.theme).

Covers the two behaviours that are easy to regress when Hydra groups move
around: theme *precedence* (per-app override beats the global group) and
graceful *fallback* (an unknown theme name degrades to the default instead
of raising).
"""

from fasthtml.common import to_xml
from omegaconf import OmegaConf

from open_apps.theme import (
    load_theme,
    render_theme_tokens,
    resolve_theme,
    theme_style,
)


def css(style) -> str:
    """Render a ``Style`` FT element down to its CSS text."""
    return to_xml(style)


class TestLoadTheme:

    def test_loads_known_theme(self):
        theme = load_theme("solarized")
        assert theme["name"] == "solarized"
        assert theme["tokens"]["color-bg"] == "#fdf6e3"

    def test_unknown_theme_falls_back_to_default(self):
        theme = load_theme("does_not_exist")
        default = load_theme("default")
        assert theme["name"] == "default"
        assert theme["tokens"] == default["tokens"]

    def test_always_has_required_keys(self):
        theme = load_theme("dark")
        assert set(theme) >= {"name", "tokens", "import_url"}


class TestResolveTheme:

    def test_per_app_overrides_global(self):
        cfg = OmegaConf.create(
            {"theme": {"name": "dark", "tokens": {"color-bg": "#000000"}},
             "todo": {"theme": "solarized"}}
        )
        assert resolve_theme(cfg, "todo")["name"] == "solarized"

    def test_null_per_app_inherits_global(self):
        cfg = OmegaConf.create(
            {"theme": {"name": "dark", "tokens": {"color-bg": "#000000"}},
             "todo": {"theme": None}}
        )
        theme = resolve_theme(cfg, "todo")
        assert theme["name"] == "dark"
        assert theme["tokens"]["color-bg"] == "#000000"

    def test_unset_per_app_inherits_global(self):
        cfg = OmegaConf.create(
            {"theme": {"name": "dark", "tokens": {}}, "todo": {}}
        )
        assert resolve_theme(cfg, "todo")["name"] == "dark"

    def test_global_as_plain_string(self):
        """``apps.theme=solarized`` (dotted override) rather than a composed group."""
        cfg = OmegaConf.create({"theme": "solarized", "todo": {}})
        theme = resolve_theme(cfg, "todo")
        assert theme["name"] == "solarized"
        assert theme["tokens"]["color-bg"] == "#fdf6e3"

    def test_no_theme_anywhere_falls_back_to_default(self):
        cfg = OmegaConf.create({"todo": {}})
        assert resolve_theme(cfg, "default_probe") == load_theme("default")

    def test_unknown_app_falls_back_to_global(self):
        cfg = OmegaConf.create({"theme": "solarized"})
        assert resolve_theme(cfg, "no_such_app")["name"] == "solarized"

    def test_unknown_per_app_name_degrades_to_default(self):
        cfg = OmegaConf.create({"theme": "solarized", "todo": {"theme": "bogus"}})
        # A bad per-app override must not raise, and must not silently
        # inherit the global theme either -- it resolves to the default file.
        assert resolve_theme(cfg, "todo")["tokens"] == load_theme("default")["tokens"]

    def test_resolved_tokens_are_plain_dicts(self):
        """Callers index tokens directly; OmegaConf nodes must be coerced."""
        cfg = OmegaConf.create({"theme": {"name": "x", "tokens": {"radius": "4px"}}})
        theme = resolve_theme(cfg, "todo")
        assert isinstance(theme, dict) and isinstance(theme["tokens"], dict)
        assert theme["tokens"]["radius"] == "4px"

    def test_apps_config_isolated_per_app(self):
        cfg = OmegaConf.create(
            {"theme": "default",
             "todo": {"theme": "solarized"},
             "calendar": {}}
        )
        assert resolve_theme(cfg, "todo")["name"] == "solarized"
        assert resolve_theme(cfg, "calendar")["name"] == "default"


class TestRenderThemeTokens:

    def test_tokens_become_custom_properties(self):
        out = css(render_theme_tokens(
            {"tokens": {"color-bg": "#fff", "radius": "8px"}}
        ))
        assert ":root {" in out
        assert "--color-bg: #fff;" in out
        assert "--radius: 8px;" in out

    def test_empty_tokens_still_renders_valid_block(self):
        out = css(render_theme_tokens({}))
        assert ":root {" in out and "}" in out
        assert "--" not in out

    def test_unsafe_token_names_are_dropped(self):
        out = css(render_theme_tokens(
            {"tokens": {"ok-name": "1", "bad; }": "2", "": "3", "a b": "4"}}
        ))
        assert "--ok-name: 1;" in out
        assert "bad" not in out and "a b" not in out

    def test_newlines_in_values_are_flattened(self):
        out = css(render_theme_tokens({"tokens": {"font-family": "a\nb\rc"}}))
        assert "--font-family: a b c;" in out

    def test_import_url_is_emitted(self):
        out = css(render_theme_tokens(
            {"import_url": "https://example.com/f.css", "tokens": {}}
        ))
        assert '@import url("https://example.com/f.css");' in out

    def test_import_url_with_quotes_is_dropped(self):
        out = css(render_theme_tokens(
            {"import_url": 'https://x.test/f.css"); body{display:none', "tokens": {}}
        ))
        assert "@import" not in out

    def test_missing_import_url_emits_no_rule(self):
        assert "@import" not in css(render_theme_tokens({"tokens": {"a": "b"}}))


class TestThemeStyle:

    def test_resolves_and_renders_in_one_call(self):
        cfg = OmegaConf.create({"theme": "default", "todo": {"theme": "solarized"}})
        assert "--color-bg: #fdf6e3;" in css(theme_style(cfg, "todo"))
        assert "--color-bg: #ffffff;" in css(theme_style(cfg, "calendar"))
