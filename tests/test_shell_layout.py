"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Unit tests for the desktop shell's per-device compositions.

The shell renders two different documents from one config: a desktop (toolbar,
centred headline, shortcuts docked bottom-right) and a phone home screen
(status bar, wordmark widget, a grid of the apps that are *not* pinned, a dock
holding the ones that are). Which one is chosen comes from the device's form
factor via the layout's ``variants:`` map.

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


def scroll_container_classes(css: str) -> set[str]:
    """Classes whose rule makes them clip -- any overflow but ``visible``.

    Keyed on the selector's last compound, which is the element the rule
    actually applies to. One axis is enough: ``overflow-x`` alone computes the
    other axis from ``visible`` to ``auto``, which is exactly the trap this
    exists to catch.
    """
    found: set[str] = set()
    for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if not re.search(r"\boverflow(-[xy])?\s*:\s*(?!visible)\S", declarations):
            continue
        found |= set(re.findall(r"\.([A-Za-z0-9_-]+)", selector.strip().split()[-1]))
    return found


class TestCompositionSelection:

    def test_phone_gets_the_home_screen(self):
        markup = render("phone")
        assert 'data-layout="home_screen"' in markup
        assert 'data-device="phone"' in markup
        assert "is-phone" in markup

    @pytest.mark.parametrize("device", ["desktop", "laptop", "tablet"])
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
        # Still tagged as a phone, so the touch and narrow-window rules apply.
        assert 'data-device="phone"' in markup


class TestPhoneHomeScreen:

    def test_grid_holds_the_unpinned_apps_and_the_dock_the_pinned(self):
        markup = render("phone", pinned=("todo", "calendar"))
        ids = ids_in(markup)
        assert {"favorite-opentodos", "favorite-opencalendar"} <= ids
        assert {"shortcut-openmaps", "shortcut-openmessages"} <= ids
        # An app is in exactly one place: two nodes answering to the same test
        # id would make every selector ambiguous, for a test and for an agent.
        assert "shortcut-opentodos" not in ids
        assert "favorite-openmaps" not in ids

    def test_pinning_moves_an_icon_from_the_grid_into_the_dock(self):
        before = ids_in(render("phone", pinned=()))
        after = ids_in(render("phone", pinned=("maps",)))
        assert "shortcut-openmaps" in before and "favorite-openmaps" not in before
        assert "favorite-openmaps" in after and "shortcut-openmaps" not in after

    def test_the_dock_holds_the_launcher(self):
        markup = render("phone")
        dock = markup[markup.index('data-testid="phone-dock"') :]
        assert 'data-testid="launcher-button"' in dock

    def test_the_launcher_sits_beside_the_scrolling_strip_not_inside_it(self):
        # The strip of pinned icons scrolls, because enough pinned apps will
        # not fit across 390px. The launcher has to stay out of it: a scroll
        # container clips what opens out of it, and the panel opens upward.
        chain = ancestor_classes(render("phone", pinned=("todo", "calendar")), "ui-launcher")
        assert "ui-phone-dock" in chain
        assert "ui-phone-dock-apps" not in chain

    def test_no_empty_strip_when_nothing_is_pinned(self):
        # An unnamed empty <div> is a node an agent reading the accessibility
        # tree has to wonder about; the dock renders the launcher alone.
        markup = render("phone", pinned=())
        assert "ui-phone-dock-apps" not in body(markup)

    def test_the_status_bar_leads_with_the_clock(self):
        markup = render("phone")
        assert markup.index('data-testid="toolbar-clock"') < markup.index(
            'data-testid="toolbar-weather"'
        )

    def test_the_brand_moves_into_the_widget(self):
        markup = render("phone")
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

    # Phone is excluded: it no longer uses an anchored panel, and the sheet has
    # its own stronger guarantee above (fixed to the viewport, so no ancestor
    # can clip it at all).
    @pytest.mark.parametrize("device", ["desktop"])
    def test_nothing_between_the_panel_and_the_shell_root_clips(self, device):
        markup = render(device, launcher_open=True)
        chain = ancestor_classes(markup, "ui-launcher-panel")
        assert chain, "the launcher panel is not in the markup at all"
        clipping = scroll_container_classes(self.css()) & set(chain)
        assert not clipping, f"{sorted(clipping)} clips the open launcher panel on {device}"

    def test_the_phone_menu_is_a_full_screen_sheet(self):
        """Not an anchored popover -- see LauncherSheet for why."""
        declarations = re.search(
            r"\.ui-launcher-overlay\s*\{([^}]*)\}", self.css()
        )
        assert declarations, "no rule for the phone launcher overlay"
        assert "position: fixed" in declarations.group(1)
        assert "inset: 0" in declarations.group(1)

    def test_the_phone_sheet_is_not_nested_in_a_filtered_ancestor(self):
        """`position: fixed` resolves against the nearest filtered ancestor.

        `.ui-phone-dock` sets `backdrop-filter`, and filter/backdrop-filter/
        transform all make an element the containing block for fixed
        descendants. Nested in the dock, the sheet's `inset: 0` would resolve
        to the dock's own ~88px box instead of the viewport and render as a
        sliver behind the icons -- with correct markup, as always.
        """
        markup = render("phone", launcher_open=True)
        chain = ancestor_classes(markup, "ui-launcher-overlay")
        assert chain, "the launcher sheet is not in the phone markup"
        filtered = {
            selector.lstrip(".")
            for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", self.css())
            if "backdrop-filter" in body or re.search(r"[^-]transform:", body)
        }
        offenders = {c for c in chain if c in filtered}
        assert not offenders, (
            f"{sorted(offenders)} would become the containing block for the "
            f"sheet's position: fixed"
        )

    def test_the_phone_dock_still_holds_the_button(self):
        """The button stays in the dock even though the menu moved out of it."""
        markup = render("phone", launcher_open=False)
        chain = ancestor_classes(markup, "ui-launcher-btn")
        assert "ui-phone-dock" in chain, chain

    def test_the_sheet_can_be_dismissed_without_the_button(self):
        """Tapping outside is the expected gesture; it must be a real control."""
        markup = render("phone", launcher_open=True)
        assert 'data-testid="launcher-scrim"' in markup
        assert 'data-testid="launcher-close"' in markup

    def test_closed_phone_menu_adds_nothing_to_the_dom(self):
        # By testid, not class name -- the class appears in the inlined
        # stylesheet, which is part of the swapped markup.
        assert 'data-testid="launcher-overlay"' not in render("phone", launcher_open=False)


class TestDesktopShellIsUnchanged:

    def test_surface_holds_the_pinned_shortcuts(self):
        ids = ids_in(render("desktop", pinned=("todo", "calendar")))
        assert {"shortcut-opentodos", "shortcut-opencalendar"} <= ids
        assert not any(i.startswith("favorite-") for i in ids)
        assert "phone-dock" not in ids

    def test_empty_state_survives(self):
        assert "Nothing pinned yet" in render("desktop", pinned=())

    def test_the_launcher_stays_in_the_toolbar(self):
        markup = body(render("desktop"))
        assert markup.index('data-testid="launcher-button"') < markup.index(
            'class="ui-desktop-surface"'
        )


class TestControlsDoNotDriftBetweenDevices:
    """Whatever the composition, the same things have to be actionable."""

    @pytest.mark.parametrize("device", ["desktop", "phone"])
    def test_every_shell_control_is_present(self, device):
        ids = ids_in(render(device))
        assert {
            "launcher-button",
            "mode-toggle",
            "toolbar-clock",
            "toolbar-weather",
            "desktop-headline",
            "desktop-tiles",
        } <= ids

    @pytest.mark.parametrize("device", ["desktop", "phone"])
    def test_every_app_is_reachable(self, device):
        # With the launcher open, because that is where the full app list
        # lives on both compositions -- the surface only ever shows some of it.
        markup = render(device, pinned=("todo",), launcher_open=True)
        for href in ("/todo", "/calendar", "/messages", "/maps", "/codeeditor"):
            assert f'href="{href}"' in markup

    @pytest.mark.parametrize("device", ["desktop", "phone"])
    def test_controls_still_swap_the_whole_shell(self, device):
        # Every control posts and replaces #desktop-shell; a composition that
        # forgot the id would render once and then break on first click.
        markup = render(device)
        assert 'id="desktop-shell"' in markup
        # The launcher, the mode toggle and the unit switch, at a minimum.
        assert markup.count('hx-target="#desktop-shell"') >= 3


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

    def test_phone_rules_are_not_behind_a_media_query(self):
        # The device is config, so the phone rendering has to hold at any
        # window size -- including whatever a screenshot harness picks.
        css = self.strip_comments(self.css())
        head, _, tail = css.partition("@media")
        assert ".is-phone" in head
        # The only mention inside a media query may be the narrow-window
        # block's exclusion of it.
        assert ".is-phone" not in tail.replace(":not(.is-phone)", "")

    def test_narrow_window_rules_cannot_reach_the_phone_markup(self):
        # The phone composition is already laid out for this width; desktop
        # fallback rules applied on top of it would fight with it.
        block = re.search(
            r"@media\s*\(max-width:\s*640px\)\s*\{(.*?)\n\}", self.strip_comments(self.css()), re.S
        )
        assert block, "no narrow-window block"
        selectors = re.findall(r"([^{}]+)\{", block.group(1))
        assert all(":not(.is-phone)" in s for s in selectors), selectors

    def test_the_home_grid_is_a_fixed_four_column_grid(self):
        declarations = self.rule(".is-phone .ui-tile-dock")
        assert "display: grid" in declarations
        # Not auto-fill: a column count that follows the width would put the
        # same app in a different place on two phones, and a grounded click
        # would stop transferring between them.
        assert "repeat(4, minmax(0, 1fr))" in declarations

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
        declarations = self.rule(".is-phone .ui-desktop-headline")
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
        declarations = self.rule(".is-phone .ui-tile-glyph")
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
        # position 7 and is absent because its tile is disabled by default.
        pinned = self.resolve()
        assert pinned == ["todo", "calendar", "messages", "maps", "codeeditor", "onlineshop"]

    @pytest.mark.parametrize(
        "gate", ["apps.onlineshop.enable=False", "apps/onlineshop/content=default"]
    )
    def test_the_online_shop_is_not_pinned_while_it_is_gated_off(self, gate):
        """Switched off, or a content pack with no catalog: either way its
        routes are not registered. A pinned key with no tile would still reach
        /desktop_all, and a task could score on pinning an app that is not on
        the page."""
        assert "onlineshop" not in self.resolve([gate])

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
        assert pinned == ["todo", "calendar"]
        assert len(pinned) < len(self.resolve())

    def test_an_unknown_pinned_value_is_rejected(self):
        # Not silently treated as "none": a typo'd sentinel would empty the
        # desktop, which looks like a rendering bug rather than a config one.
        with pytest.raises(ValueError, match="not understood"):
            self.resolve(["apps.start_page.desktop.pinned=everything"])
