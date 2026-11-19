"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Unit tests for the desktop shell's per-device compositions.

The shell renders two different documents from one config: a desktop (toolbar,
centred headline, no app icons -- those are the chrome dock's job) and a phone
or tablet home screen (status bar, wordmark widget, a scrollable grid of the
apps that are *not* pinned, a dock holding at most four that are, no launcher).
Which one is chosen comes from the device's form factor via the layout's
``variants:`` map.

What these cover is the seam between the three: that the config actually
reaches the renderer, that each composition contains what it claims to, and
that the parts an agent acts on -- test ids, hrefs, ``/desktop_all`` -- do not
drift between them. A layout that silently falls back to the desktop
composition on a phone is the failure this is here to catch, because the run
would still complete and still report a number.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fasthtml.common import to_xml
from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra

from open_apps.apps.start_page import main as start_page
from open_apps.ui import component_styles

CONFIG_DIR = str((Path(__file__).resolve().parent.parent / "config").resolve())


def compose_apps(device: str, *extra: str):
    """The apps config for a device, with the desktop-shell layout selected."""
    if GlobalHydra.instance().is_initialized():
        GlobalHydra.instance().clear()
    with initialize_config_dir(version_base=None, config_dir=CONFIG_DIR):
        cfg = compose(
            config_name="config",
            overrides=[
                f"device={device}",
                "apps/start_page/layout=desktop",
                # Rendering a wallpaper is a Pillow pass writing a PNG to disk;
                # it has its own tests and nothing here looks at the pixels.
                "apps.start_page.desktop.wallpaper.enabled=False",
                *extra,
            ],
        )
    return cfg.apps


def render(
    device: str, *extra: str, pinned=("todo", "calendar"), launcher_open: bool = False
) -> str:
    """Render the shell for a device and return its markup."""
    apps_cfg = compose_apps(device, *extra)
    start_page.app.config = apps_cfg
    start_page.reset_desktop_state(apps_cfg.start_page)
    start_page._desktop_state["pinned"] = list(pinned)
    start_page._desktop_state["launcher_open"] = launcher_open
    return to_xml(start_page.render_desktop_shell(apps_cfg.start_page))


def body(markup: str) -> str:
    """The markup minus the inline stylesheet.

    The shell carries its own <style> (the theme tokens have to come back with
    every htmx swap), so a naive substring search finds every class name in the
    CSS long before it finds the element.
    """
    return re.sub(r"<style>.*?</style>", "", markup, flags=re.S)


@pytest.fixture(autouse=True)
def restore_shell_state():
    """The shell keeps module-level state; give each test a clean one back."""
    previous = getattr(start_page.app, "config", None)
    yield
    start_page.app.config = previous
    start_page.reset_desktop_state(
        getattr(previous, "start_page", None) if previous is not None else None
    )


def ids_in(markup: str) -> set[str]:
    return set(re.findall(r'data-testid="([^"]+)"', markup))


class _Ancestors(HTMLParser):
    """Every class on every element enclosing the first match for a class."""

    def __init__(self, target: str):
        super().__init__(convert_charrefs=True)
        self.target = target
        self._stack: list[tuple[str, list[str]]] = []
        self.found: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if self.found is None and self.target in classes:
            self.found = [c for _, frame in self._stack for c in frame]
        self._stack.append((tag, classes))

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return


def ancestor_classes(markup: str, target: str) -> list[str]:
    """The classes of everything wrapping ``target``, outermost first.

    ``<style>`` is CDATA to the parser, so the shell's inline stylesheet does
    not have to be stripped first.
    """
    parser = _Ancestors(target)
    parser.feed(markup)
    return parser.found or []


def scroll_container_classes(css: str, absent: frozenset[str] = frozenset()) -> set[str]:
    """Classes whose rule makes them clip -- any overflow but ``visible``.

    Keyed on the selector's last compound, which is the element the rule
    actually applies to. One axis is enough: ``overflow-x`` alone computes the
    other axis from ``visible`` to ``auto``, which is exactly the trap this
    exists to catch.

    ``absent`` names classes the markup under test does not carry; a rule
    qualified by one cannot apply, so it is skipped -- the home screen's
    scroll container is not the desktop's.
    """
    found: set[str] = set()
    for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if not re.search(r"\boverflow(-[xy])?\s*:\s*(?!visible)\S", declarations):
            continue
        if absent & set(re.findall(r"\.([A-Za-z0-9_-]+)", selector)):
            continue
        found |= set(re.findall(r"\.([A-Za-z0-9_-]+)", selector.strip().split()[-1]))
    return found


class TestCompositionSelection:

    @pytest.mark.parametrize("device", ["phone", "tablet"])
    def test_phones_and_tablets_get_the_home_screen(self, device):
        markup = render(device)
        assert 'data-layout="home_screen"' in markup
        assert f'data-device="{device}"' in markup
        assert f"is-{device} is-home-screen" in markup

    @pytest.mark.parametrize("device", ["desktop", "laptop"])
    def test_everything_else_gets_the_desktop_shell(self, device):
        markup = render(device)
        assert 'data-layout="shell"' in markup
        # A form factor with no variant of its own must fall back rather than
        # render nothing -- a new device file should show a working page before
        # anyone writes a layout for it.
        assert "ui-desktop-surface" in markup

    def test_the_variant_map_is_overridable(self):
        # The control condition: the desktop composition, on a phone.
        markup = render("phone", "apps.start_page.desktop.variants.phone=shell")
        assert 'data-layout="shell"' in markup
        # Still tagged as a phone, so the touch rules apply -- but not as a
        # home screen, so none of the home-screen sizing reaches the desktop
        # markup and the narrow-window rules do.
        assert 'data-device="phone"' in markup
        assert "is-home-screen" not in body(markup)


@pytest.mark.parametrize("device", ["phone", "tablet"])
class TestHomeScreen:

    def test_grid_holds_the_unpinned_apps_and_the_dock_the_pinned(self, device):
        markup = render(device, pinned=("todo", "calendar"))
        ids = ids_in(markup)
        assert {"favorite-opentodos", "favorite-opencalendar"} <= ids
        assert {"shortcut-openmaps", "shortcut-openmessages"} <= ids
        # An app is in exactly one place: two nodes answering to the same test
        # id would make every selector ambiguous, for a test and for an agent.
        assert "shortcut-opentodos" not in ids
        assert "favorite-openmaps" not in ids

    def test_pinning_moves_an_icon_from_the_grid_into_the_dock(self, device):
        before = ids_in(render(device, pinned=()))
        after = ids_in(render(device, pinned=("maps",)))
        assert "shortcut-openmaps" in before and "favorite-openmaps" not in before
        assert "favorite-openmaps" in after and "shortcut-openmaps" not in after

    def test_there_is_no_launcher(self, device):
        # Every app is on the grid or in the dock; an all-apps menu would be a
        # second copy of the screen. Not even when the state says it is open --
        # a desktop episode's leftover state must not draw one here.
        for launcher_open in (False, True):
            markup = body(render(device, launcher_open=launcher_open))
            ids = ids_in(markup)
            assert not ids & {"launcher-button", "launcher-panel", "launcher-overlay"}
            assert "/desktop/launcher" not in markup

    def test_icons_are_the_apps_own_files(self, device):
        # The files the window chrome's dock draws, so an app looks the same on
        # the home screen, in the dock and in its window's title bar.
        markup = body(render(device, pinned=("todo",)))
        grid = markup[markup.index('data-testid="desktop-tiles"') : markup.index('data-testid="phone-dock"')]
        dock = markup[markup.index('data-testid="phone-dock"') :]
        assert '<img src="/assets/icons/real_icons/maps.svg"' in grid
        assert '<img src="/assets/icons/real_icons/todo.svg"' in dock

    def test_dock_icons_are_unlabelled_but_still_named(self, device):
        markup = body(render(device, pinned=("todo", "calendar")))
        dock = markup[markup.index('data-testid="phone-dock"') :]
        assert "ui-tile-label" not in dock
        assert 'aria-label="OpenTodos"' in dock

    def test_an_empty_dock_still_renders(self, device):
        # So unpinning everything does not move the grid's bottom edge.
        markup = render(device, pinned=())
        assert 'data-testid="phone-dock"' in markup
        assert not any(i.startswith("favorite-") for i in ids_in(markup))

    def test_the_grid_scrolls_under_a_dock_that_does_not(self, device):
        # The dock is a sibling of the scroll container, not inside it: with
        # more apps than fit, the grid pages and the dock stays put.
        markup = render(device)
        assert "ui-desktop-surface" not in ancestor_classes(markup, "ui-phone-dock")
        assert "ui-desktop-surface" in ancestor_classes(markup, "ui-tile-dock")

    def test_the_status_bar_leads_with_the_clock(self, device):
        markup = render(device)
        assert markup.index('data-testid="toolbar-clock"') < markup.index(
            'data-testid="toolbar-weather"'
        )

    def test_the_brand_moves_into_the_widget(self, device):
        markup = render(device)
        widget = markup[markup.index('data-testid="desktop-headline"') :]
        assert "ui-wordmark" in widget[: widget.index("ui-dock-row")]


class TestTheAppsMenuOpens:
    """The launcher panel has to be *on screen*, not merely in the DOM.

    This failed silently once and it is the worst shape a bug in here can
    take: the button toggled, ``aria-expanded`` flipped, the panel rendered
    with every app in it -- and a scrolling ancestor clipped the whole thing
    out of view. Nothing errors, no test that counts nodes or checks hrefs
    notices, and the run reports a number as if the apps were reachable.
    """

    def css(self) -> str:
        return re.sub(r"/\*.*?\*/", "", to_xml(component_styles()), flags=re.S)

    # Desktop only: the home screen has no launcher at all.
    @pytest.mark.parametrize("device", ["desktop"])
    def test_nothing_between_the_panel_and_the_shell_root_clips(self, device):
        markup = render(device, launcher_open=True)
        chain = ancestor_classes(markup, "ui-launcher-panel")
        assert chain, "the launcher panel is not in the markup at all"
        clipping = scroll_container_classes(self.css(), absent=frozenset({"is-home-screen"})) & set(chain)
        assert not clipping, f"{sorted(clipping)} clips the open launcher panel on {device}"


class TestDesktopShellIsUnchanged:

    def test_surface_draws_no_app_icons(self):
        # Pinned apps live in the window chrome's dock; the desktop itself is
        # wallpaper and headline only, whatever is pinned.
        for pinned in (("todo", "calendar"), ()):
            ids = ids_in(render("desktop", pinned=pinned))
            assert not any(i.startswith(("shortcut-", "favorite-")) for i in ids)
            assert "desktop-tiles" not in ids
            assert "phone-dock" not in ids

    def test_the_launcher_stays_in_the_toolbar(self):
        markup = body(render("desktop"))
        assert markup.index('data-testid="launcher-button"') < markup.index(
            'class="ui-desktop-surface"'
        )


class TestLauncherGlyphs:
    """Launcher rows show each app's own glyph, bare and in the text colour."""

    @pytest.mark.parametrize("device", ["desktop"])
    def test_rows_carry_the_app_glyph_not_the_generic_grid(self, device):
        markup = body(render(device, launcher_open=True))
        glyphs = re.findall(r'<div class="ui-launcher-glyph">(.*?)</div>', markup, re.S)
        assert glyphs, "launcher rendered no rows"
        for glyph in glyphs:
            assert 'viewBox="11 11 42 42"' in glyph
            assert 'stroke="currentColor"' in glyph
            assert "<img" not in glyph

    def test_glyph_box_has_no_fill_and_follows_the_text_colour(self):
        css = render("desktop", launcher_open=True)
        rule = re.search(r"\.ui-launcher-glyph \{([^}]*)\}", css).group(1)
        assert "background" not in rule
        assert "color: var(--color-fg)" in rule


class TestControlsDoNotDriftBetweenDevices:
    """Whatever the composition, the same things have to be actionable."""

    @pytest.mark.parametrize("device", ["desktop", "phone", "tablet"])
    def test_every_shell_control_is_present(self, device):
        ids = ids_in(render(device))
        assert {
            "mode-toggle",
            "toolbar-clock",
            "toolbar-weather",
            "desktop-headline",
        } <= ids
        # The launcher is the desktop's; the home screen has none by design.
        assert ("launcher-button" in ids) == (device == "desktop")

    @pytest.mark.parametrize("device", ["desktop", "phone", "tablet"])
    def test_every_app_is_reachable(self, device):
        # On the desktop the full app list is behind the launcher; on a home
        # screen it is the grid and the dock, with nothing open.
        markup = render(device, pinned=("todo",), launcher_open=device == "desktop")
        for href in ("/todo", "/calendar", "/messages", "/maps", "/codeeditor"):
            assert f'href="{href}"' in markup

    @pytest.mark.parametrize("device", ["desktop", "phone", "tablet"])
    def test_controls_still_swap_the_whole_shell(self, device):
        # Every control posts and replaces #desktop-shell; a composition that
        # forgot the id would render once and then break on first click.
        markup = render(device)
        assert 'id="desktop-shell"' in markup
        # The mode toggle and the unit switch, plus the launcher on a desktop.
        assert markup.count('hx-target="#desktop-shell"') >= (3 if device == "desktop" else 2)


class TestStylesheet:
    """CSS invariants that only surface as a wrong screenshot."""

    def css(self) -> str:
        return to_xml(component_styles())

    def strip_comments(self, text: str) -> str:
        return re.sub(r"/\*.*?\*/", "", text, flags=re.S)

    def rule(self, selector: str) -> str:
        css = self.strip_comments(self.css())
        match = re.search(rf"(?:^|\}}|\{{)\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", css, re.S)
        assert match, f"no rule for {selector}"
        return match.group(1)

    def test_home_screen_rules_are_not_behind_a_media_query(self):
        # The device is config, so the home screen has to hold at any window
        # size -- including whatever a screenshot harness picks.
        css = self.strip_comments(self.css())
        head, _, tail = css.partition("@media")
        assert ".is-home-screen" in head
        # The only mention inside a media query may be the narrow-window
        # block's exclusion of it.
        assert ".is-home-screen" not in tail.replace(":not(.is-home-screen)", "")

    def test_narrow_window_rules_cannot_reach_the_phone_markup(self):
        # The phone composition is already laid out for this width; desktop
        # fallback rules applied on top of it would fight with it.
        block = re.search(
            r"@media\s*\(max-width:\s*640px\)\s*\{(.*?)\n\}", self.strip_comments(self.css()), re.S
        )
        assert block, "no narrow-window block"
        selectors = re.findall(r"([^{}]+)\{", block.group(1))
        assert all(":not(.is-home-screen)" in s for s in selectors), selectors

    def test_the_home_grid_has_a_fixed_column_count_per_form_factor(self):
        declarations = self.rule(".is-home-screen .ui-tile-dock")
        assert "display: grid" in declarations
        # Not auto-fill: a column count that follows the width would put the
        # same app in a different place on two phones, and a grounded click
        # would stop transferring between them.
        assert "repeat(var(--ui-home-cols), minmax(0, 1fr))" in declarations
        assert "--ui-home-cols: 4" in self.rule(".ui-desktop.is-home-screen")
        assert "--ui-home-cols: 6" in self.rule(".ui-desktop.is-home-screen.is-tablet")

    def test_labels_span_their_whole_column(self):
        # No column gap, and a label as wide as its column: the spare width
        # between icons belongs to the labels, which is what keeps names
        # whole instead of ellipsized.
        assert re.search(r"gap:[^;]*\s0;", self.rule(".is-home-screen .ui-tile-dock"))
        assert "width: 100%" in self.rule(".is-home-screen .ui-tile-label")

    def test_the_home_screen_scrolls_inside_a_fixed_height_shell(self):
        assert "height: 100dvh" in self.rule(".ui-desktop.is-home-screen")
        assert "overflow-y: auto" in self.rule(".is-home-screen .ui-desktop-surface")

    def test_the_dock_is_in_flow(self):
        # It is the shell's last child, not an overlay, so nothing has to
        # reserve space for it and the grid simply gets what is left.
        declarations = self.rule(".ui-phone-dock")
        assert "position: fixed" not in declarations
        assert "position: absolute" not in declarations

    def test_touch_pointers_get_a_visible_pin(self):
        # focus-within does not rescue the touch case the way it rescues the
        # keyboard one -- there is nothing to tab with.
        block = re.search(
            r"@media\s*\(hover:\s*none\)\s*\{(.*?)\n\}", self.strip_comments(self.css()), re.S
        )
        assert block and "opacity: 1" in block.group(1)

    def test_tablets_get_touch_sized_targets(self):
        assert "44px" in self.rule(".is-tablet .ui-icon-btn")


class TestWallpaperTextIsThemeAware:
    """Ink over the wallpaper has to follow the theme, not be pinned white.

    The headline and the tile labels sit on a generated image, not on
    `--color-bg`. They used to hardcode `--color-on-primary`, which is white in
    *both* Meta themes -- correct over the dark one, white-on-pale over the
    light one. Nothing fails when this is wrong; it just renders illegibly.
    """

    def css(self) -> str:
        return to_xml(component_styles())

    def strip_comments(self, text: str) -> str:
        return re.sub(r"/\*.*?\*/", "", text, flags=re.S)

    def rule(self, selector: str) -> str:
        """Every block for `selector`, concatenated.

        A selector can legitimately appear more than once -- `.ui-tile` has a
        layout block and a colour block -- and taking only the first would make
        these assertions depend on declaration order.
        """
        css = self.strip_comments(self.css())
        blocks = re.findall(
            rf"(?:^|\}}|\{{)\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", css, re.S
        )
        assert blocks, f"no rule for {selector}"
        return "\n".join(blocks)

    def test_the_halo_is_derived_from_the_page_background(self):
        # --color-bg is light in a light theme and dark in a dark one, so the
        # backing needs no per-mode branch and holds for a non-Meta theme too.
        declarations = self.rule(".ui-desktop")
        assert "--ui-wallpaper-halo" in declarations
        assert "var(--color-bg)" in declarations

    def test_headline_uses_the_theme_foreground(self):
        declarations = self.rule(".ui-desktop-headline")
        assert "color: var(--color-fg)" in declarations
        assert "--color-on-primary" not in declarations
        assert "--ui-wallpaper-halo" in declarations

    def test_tile_labels_use_the_theme_foreground(self):
        declarations = self.rule(".ui-tile")
        assert "color: var(--color-fg)" in declarations
        assert "--ui-wallpaper-halo" in declarations

    def test_no_wallpaper_text_is_pinned_to_a_literal_colour(self):
        # A raw rgb()/#hex in a text-shadow is the same bug in another form:
        # it cannot follow the theme.
        for selector in (".ui-desktop-headline", ".ui-tile"):
            declarations = self.rule(selector)
            shadow = re.search(r"text-shadow:([^;]*);", declarations)
            assert shadow, selector
            assert "rgb(" not in shadow.group(1), selector
            assert "#" not in shadow.group(1), selector

    def test_the_phone_widget_opts_out_of_the_halo(self):
        # It sits on its own frosted card, not on the image.
        declarations = self.rule(".is-home-screen .ui-desktop-headline")
        assert "text-shadow: none" in declarations

    def test_tile_hover_lifts_a_surface_rather_than_tinting_with_the_ink(self):
        """Hover must not reduce contrast.

        A --color-fg wash is the same colour as the label sitting on top of it,
        so the label got harder to read on hover -- in both modes, since both
        the ink and the wash flip together. Washing toward --color-bg restores
        the fg-on-bg pairing the tokens are built around.
        """
        declarations = self.rule(".ui-tile:hover")
        assert "var(--color-bg)" in declarations
        assert "var(--color-fg)" not in declarations

    def test_the_mark_keeps_its_shape_in_dark_mode(self):
        # Only the ink changes between modes; a flip read as a different logo.
        css = self.strip_comments(self.css())
        assert not re.search(r"\.ui-wordmark-mark\s*\{[^}]*transform", css)

    def test_tile_hover_keeps_the_ink_and_only_firms_the_halo(self):
        """html5up's `a:hover { color: #47D3E5 }` outranks `.ui-tile`; left
        alone it turned the label cyan inside its halo, a heavy glow in light
        mode. Hover pins the ink and nudges the halo, still theme-derived."""
        declarations = self.rule(".ui-desktop .ui-tile:hover")
        assert "color: var(--color-fg)" in declarations
        shadow = re.search(r"text-shadow:([^;]*);", declarations).group(1)
        assert "--ui-wallpaper-halo" in shadow
        assert "rgb(" not in shadow and "#" not in shadow

    def test_the_home_screen_blurs_the_wallpaper_more_than_the_desktop(self):
        # A multiple of the configured blur, so `wallpaper.blur` still moves it.
        declarations = self.rule(".ui-desktop.is-home-screen::before")
        assert "blur(calc(var(--ui-wallpaper-blur, 4px) * 3))" in declarations

    def test_elevation_shadows_are_not_derived_from_the_foreground(self):
        """A --color-fg shadow inverts with the theme and becomes a white smear
        under every app icon on the dark one. Depth stays dark."""
        declarations = self.rule(".ui-desktop")
        assert "--ui-shadow-raised" in declarations
        shadow = re.search(r"--ui-shadow-raised:([^;]*);", declarations)
        assert shadow and "--color-fg" not in shadow.group(1)

    def test_the_phone_icon_shadow_uses_that_one_knob(self):
        # It was a literal rgb(0 0 0 / 28%), tuned against the dark wallpaper
        # where a black shadow all but disappears -- and far too heavy on the
        # pale one, where it is the only place it actually shows.
        declarations = self.rule(".is-home-screen .ui-tile-glyph")
        assert "box-shadow: var(--ui-shadow-raised)" in declarations

    def test_toolbar_chips_track_the_theme_foreground(self):
        """The dark theme's --color-muted is a mid grey on a frosted bar over a
        dark wallpaper, leaving the clock and temperature dimmer than the mode
        toggle beside them."""
        declarations = self.rule(".ui-desktop .ui-chip,\n.ui-desktop .ui-chip .ui-text")
        assert "var(--color-fg)" in declarations
        assert "--color-muted" not in declarations


class TestPinResolution:
    """`pinned: all` plus a subtractive `unpinned`.

    The launcher only exerts pressure on an agent when something it needs is
    behind it, so the ergonomic default is everything pinned and the
    experiment is naming the few to hide.
    """

    def resolve(self, overrides=(), variant="shell"):
        from open_apps.apps.start_page.main import _desktop_config, resolve_pinned

        apps_cfg = compose_apps("desktop", *overrides)
        # The inventory is filtered through the renderer's own enabled-apps
        # check, which reads the global online-shop gate off app.config.
        start_page.app.config = apps_cfg
        sp = apps_cfg.start_page
        return resolve_pinned(_desktop_config(sp), sp, variant)

    def test_all_expands_to_every_rendered_app(self):
        # Position order from the start page's inventory. `uilibrary` sits at
        # position 7 and is absent because its tile is disabled by default;
        # `passwords` is rendered but opts out of `all` (pinned_by_default).
        pinned = self.resolve()
        assert pinned == [
            "todo", "calendar", "messages", "maps", "codeeditor", "onlineshop", "openbanking",
            "files",
            "contacts",
        ]

    @pytest.mark.parametrize(
        "gate", ["apps.onlineshop.enable=False", "apps/onlineshop/content=default"]
    )
    def test_the_online_shop_is_not_pinned_while_it_is_gated_off(self, gate):
        """Switched off, or a content pack with no catalog: either way its
        routes are not registered. A pinned key with no tile would still reach
        /desktop_all, and a task could score on pinning an app that is not on
        the page."""
        assert "onlineshop" not in self.resolve([gate])

    def test_an_app_can_opt_out_of_all(self):
        """OpenPasswords is on the desktop's all-apps panel but not docked by
        default (`pinned_by_default: false`). Its opt-out survives an
        `unpinned` override, which replaces the list rather than extending
        it."""
        assert "passwords" not in self.resolve()
        assert "passwords" not in self.resolve(["apps.start_page.desktop.unpinned=[messages]"])

    def test_an_opted_out_app_is_still_pinnable_by_name(self):
        assert "passwords" in self.resolve(["apps.start_page.desktop.pinned=[passwords,todo]"])

    def test_an_opted_out_app_can_opt_back_in(self):
        """The one-key override docs/index.md gives for docking OpenPasswords."""
        pinned = self.resolve(["apps.start_page.apps.passwords.pinned_by_default=true"])
        assert "passwords" in pinned and "todo" in pinned

    def test_unpinned_subtracts(self):
        pinned = self.resolve(["apps.start_page.desktop.unpinned=[messages,maps]"])
        assert "messages" not in pinned and "maps" not in pinned
        assert "todo" in pinned and "codeeditor" in pinned

    def test_an_explicit_list_still_works(self):
        assert self.resolve(["apps.start_page.desktop.pinned=[maps]"]) == ["maps"]

    def test_order_follows_the_inventory_not_the_pin_list(self):
        # The dock renders in configured order, so a task's "third icon" must
        # not depend on the order someone wrote the yaml in.
        assert self.resolve(["apps.start_page.desktop.pinned=[maps,todo]"]) == [
            "todo",
            "maps",
        ]

    def test_the_phone_keeps_a_grid_to_show(self):
        """`all` on the home screen would dock every app and leave the grid
        empty, since the grid is exactly the unpinned set."""
        pinned = self.resolve(variant="home_screen")
        assert pinned == ["todo", "calendar", "messages", "maps"]
        assert len(pinned) < len(self.resolve())

    def test_the_home_screen_dock_holds_four(self):
        # Asking for every app still docks four -- the first four in inventory
        # order -- rather than a dock that scrolls.
        pinned = self.resolve(
            ["apps.start_page.desktop.pinned_by_variant.home_screen=all"], variant="home_screen"
        )
        assert pinned == ["todo", "calendar", "messages", "maps"]

    def test_the_dock_size_is_config(self):
        pinned = self.resolve(
            ["apps.start_page.desktop.dock_size_by_variant.home_screen=2"], variant="home_screen"
        )
        assert pinned == ["todo", "calendar"]


    def test_an_unknown_pinned_value_is_rejected(self):
        # Not silently treated as "none": a typo'd sentinel would empty the
        # desktop, which looks like a rendering bug rather than a config one.
        with pytest.raises(ValueError, match="not understood"):
            self.resolve(["apps.start_page.desktop.pinned=everything"])


class TestPinningIntoAFullDock:
    """The pin route refuses a fifth app on the home screen."""

    def configure(self, device):
        apps_cfg = compose_apps(device)
        start_page.app.config = apps_cfg
        start_page.reset_desktop_state(apps_cfg.start_page, start_page._configured_variant(apps_cfg))
        return start_page._desktop_state["pinned"]

    def test_a_full_dock_refuses_a_fifth_app(self):
        pinned = self.configure("phone")
        assert pinned == ["todo", "calendar", "messages", "maps"]
        start_page.toggle_pin("files")
        assert "files" not in start_page._desktop_state["pinned"]
        # Unpinning frees a slot, and the next pin takes it.
        start_page.toggle_pin("maps")
        start_page.toggle_pin("files")
        assert start_page._desktop_state["pinned"] == ["todo", "calendar", "messages", "files"]

    def test_the_desktop_dock_is_unlimited(self):
        pinned = self.configure("desktop")
        assert len(pinned) > 4


def dock_item(markup: str, testid: str) -> str:
    """The opening tag of the element carrying ``testid``."""
    return re.search(rf'<[^<>]*data-testid="{testid}"[^<>]*>', markup).group(0)


class TestChromeOverAHomeScreenApp:
    """Over an app on a phone or tablet, the dock is the home screen's."""

    def chrome(self, device, *extra):
        apps_cfg = compose_apps(device, *extra)
        start_page.app.config = apps_cfg
        start_page.reset_desktop_state(apps_cfg.start_page, start_page._configured_variant(apps_cfg))
        start_page.reset_chrome_state()
        start_page.render_chrome("/files")  # leaves `files` running
        return start_page.render_chrome("/todo")

    @pytest.mark.parametrize("device", ["phone", "tablet"])
    def test_home_plus_the_docked_apps_and_nothing_else(self, device):
        result = self.chrome(device)
        markup = result.body
        assert ids_in(markup) >= {"dock-desktop", "dock-todo", "dock-calendar", "dock-messages", "dock-maps"}
        # No launcher, and no running-app extras: an app is on the home screen
        # or in the dock, and the way to anything else is Home.
        assert "dock-launcher" not in ids_in(markup)
        assert "dock-files" not in ids_in(markup)
        assert 'title="Home"' in dock_item(markup, "dock-desktop")

    @pytest.mark.parametrize(
        ("content", "label"), [("german", "Startseite"), ("mandarin", "主屏幕")]
    )
    def test_home_speaks_the_start_page_language(self, content, label):
        result = self.chrome("phone", f"apps/start_page/content={content}")
        assert f'title="{label}"' in dock_item(result.body, "dock-desktop")

    @pytest.mark.parametrize("device", ["phone", "tablet"])
    def test_no_window_title_bar(self, device):
        result = self.chrome(device)
        assert 'id="oa-titlebar"' not in result.body
        assert "data-oa-titlebar" not in result.html_attrs

    def test_the_desktop_keeps_its_launcher_and_title_bar(self):
        result = self.chrome("desktop")
        assert {"dock-launcher", "window-close"} <= ids_in(result.body)
        assert 'title="Desktop"' in dock_item(result.body, "dock-desktop")
