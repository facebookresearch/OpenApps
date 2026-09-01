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
