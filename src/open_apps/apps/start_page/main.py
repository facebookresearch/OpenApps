"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
# assets come from https://html5up.net/story
from fasthtml.common import *
import json
import random
from datetime import datetime
try:
    from helper import (
        Wrapper,
        ItemContent,
        Gallery,
        PageWrapper,
        get_app,
        footer,
        serve,
        Modal,
        get_java_version,
        generate_random_colors,
    )
except ImportError:
    from open_apps.apps.start_page.helper import (
        Wrapper,
        ItemContent,
        Gallery,
        PageWrapper,
        get_app,
        footer,
        serve,
        Modal,
        get_java_version,
        generate_random_colors,
    )
from omegaconf import DictConfig, OmegaConf
from open_apps.theme import render_theme_css, resolve_theme, theme_asset


def icon_for(app_config, app_name):
    """Path to an app's start-page tile icon under the active theme.

    The icons are PNGs, so a CSS variable cannot recolor them; the theme picks
    a set via its `icon_set` asset and the repo ships a hand-made greyscale
    version alongside the color one. Falls back to the color icon when a theme
    asks for a set the config has no path for.
    """
    if theme_asset(app.config, "start_page", "icon_set", "color") == "bw":
        bw = app_config.get("bw_icon")
        if bw:
            return bw
    return app_config.get("icon", f"/assets/icons/real_icons/{app_name}.png")


def tile_colors(config):
    """Background fills for the app tiles, in position order.

    The default palette is six saturated iOS-style hues that carry no semantic
    meaning -- mapping them onto theme tokens would collapse six visually
    distinct targets into one. Non-light themes do collapse them, deliberately:
    that is the perturbation `dark` and `mono` are for.
    """
    if config.get("use_random_colors"):
        return generate_random_colors(10)
    tone = theme_asset(app.config, "start_page", "tone", "light")
    by_tone = config.get("tile_fill_by_tone") or {}
    if tone in by_tone:
        return [by_tone[tone]]
    return config.app_background_colors

from open_apps import device
from open_apps.theme import theme_style
from open_apps.wallpaper import ensure_wallpaper
from open_apps.ui import (
    AppTile,
    Clock,
    LauncherItem,
    LauncherMenu,
    ModeToggle,
    Text,
    Toolbar,
    WeatherChip,
    Wordmark,
    component_styles,
)

# ---------------------------------------------------------------------------
# Desktop shell state
#
# Two fields are scoreable and exposed at /desktop_all: the light/dark `mode`
# and the list of `pinned` app keys. `launcher_open` is deliberately not --
# whether a menu happens to be open is transient UI, and including it would
# make a task fail or pass on whether the agent left a popover showing.
#
# Held in memory rather than sqlite. The other apps persist because their state
# outlives a page load in ways an agent can navigate away from and back to; this
# is per-episode shell state, the server is restarted between episodes, and
# reset_all_apps() re-seeds it from config. A table here would be ceremony.
# ---------------------------------------------------------------------------
_DESKTOP_DEFAULTS = {
    "mode": "light",
    "pinned": [],
    "units": "celsius",
    "launcher_open": False,
}
_desktop_state = dict(_DESKTOP_DEFAULTS)


def _desktop_config(start_page_cfg):
    """The `desktop:` block from the layout config, as a plain dict."""
    raw = start_page_cfg.get("desktop") if hasattr(start_page_cfg, "get") else None
    if raw is None:
        return {}
    return OmegaConf.to_container(raw, resolve=True) if OmegaConf.is_config(raw) else dict(raw)


def reset_desktop_state(start_page_cfg=None) -> None:
    """Re-seed shell state from config. Called on app reset."""
    global _desktop_state
    _desktop_state = dict(_DESKTOP_DEFAULTS)
    cfg = _desktop_config(start_page_cfg) if start_page_cfg is not None else {}
    _desktop_state["mode"] = cfg.get("mode", "light")
    _desktop_state["pinned"] = list(cfg.get("pinned", []) or [])
    _desktop_state["units"] = cfg.get("units", "celsius")

# Define available apps and their route getters
AVAILABLE_APPS = {
    "messages": (
        "open_apps.apps.messenger_app",
        "get_message_routes",
    ),
    "todo": ("open_apps.apps.todo_app", "get_todo_routes"),
    "calendar": (
        "open_apps.apps.calendar_app",
        "get_calendar_routes",
    ),
    "codeeditor": (
        "open_apps.apps.codeeditor_app",
        "get_codeeditor_routes",
    ),
    "map": (
        "open_apps.apps.map_app",
        "get_map_routes",
    ),
}

APP_MODULE_TO_NAME = {
    "open_apps.apps.todo_app": "todo",
    "open_apps.apps.calendar_app": "calendar",
    "open_apps.apps.messenger_app": "messenger",
    "open_apps.apps.codeeditor_app": "code_editor",
    "open_apps.apps.map_app": "maps",
}


def _drop_app_tables(module, apps_cfg) -> None:
    """Drop all SQLite tables for an app so set_environment can re-seed.

    set_environment loops `insert(...)` over the configured rows but
    does not wipe what's already there, so a second call hits unique
    constraint violations. Drop first, then re-seed.
    """
    cfg_key = APP_MODULE_TO_NAME.get(module.__name__)
    if cfg_key is None:
        return
    sub_cfg = getattr(apps_cfg, cfg_key, None)
    if sub_cfg is None or not hasattr(sub_cfg, "database_path"):
        return
    try:
        from fastlite import database as fl_database

        db = fl_database(sub_cfg.database_path)
        for table_name in db.table_names():
            db[table_name].drop()
    except Exception:
        # If the DB or fastlite is unavailable, fall through — the
        # set_environment call below will surface a clearer error.
        pass


def reset_all_apps(config: DictConfig):
    """Reset all app databases to their configured initial state.

    Drops sqlite/filesystem state per-app, then re-runs set_environment
    so the apps re-seed from the Hydra config. This is the generic
    reset mechanism used by ``open_apps.mcp.appserver.AppServer.reset()``.

    Args:
        config: The full OpenApps DictConfig (typically ``cfg.apps``).
    """
    import shutil
    from pathlib import Path

    # Code editor: clean filesystem; tables don't apply.
    if hasattr(config, "code_editor") and hasattr(config.code_editor, "database_path"):
        folder = Path(config.code_editor.database_path)
        if folder.exists():
            shutil.rmtree(folder)

    # Shell state is part of what a reset must restore: a task that scores on
    # pinned apps or theme mode would otherwise inherit the previous episode's.
    reset_desktop_state(getattr(config, "start_page", None))

    for app_name, (module_path, getter_func) in AVAILABLE_APPS.items():
        try:
            module = __import__(module_path, fromlist=[getter_func])
            if app_name != "codeeditor":
                _drop_app_tables(module, config)
            if hasattr(module, "set_environment"):
                module.set_environment(config)
        except Exception as e:
            print(f"Warning: failed to reset {app_name}: {e}")


def get_start_page_routes():
    return app.routes

def initialize_routes_and_configure_task(config: DictConfig = None):
    global app, rt
    """Initialize all apps and configure the app with provided config."""
    # Hydra should handle the config loading, see launch_experiment.py
    app.config = config  # Update the global app config
    # Seed the desktop shell from config. Harmless under the gallery layout --
    # the state simply goes unread.
    reset_desktop_state(getattr(config, "start_page", None))

    java_version_high_enough = get_java_version().startswith("21")
    if not app.config.onlineshop.enable:
        print("---> Online shop is disabled in the config.")
    else:
        print("Java version check:", get_java_version())
        if java_version_high_enough:
            print("---> Online shop turned on!!")
            AVAILABLE_APPS["onlineshop"] = (
                "open_apps.apps.onlineshop_app",
                "get_onlineshop_routes",
            )
    if java_version_high_enough:
        if app.config.maps.allow_planning:
            print("---> Map planning is not available without Java 21 or higher.")
            print("Turning off the planning feature for now...")
            app.config.maps.allow_planning = False

    for app_name, (module_path, getter_func) in AVAILABLE_APPS.items():
        try:
            module = __import__(module_path, fromlist=[getter_func])
            # Set environment variables for the module
            if hasattr(module, "set_environment"):
                print(f"Setting environment for {app_name}")
                module.set_environment(config)

            # Get fresh routes with new config
            route_getter = getattr(module, getter_func)
            routes = route_getter()
            app.routes.extend(routes)

        except ImportError as e:
            print(f"Failed to load routes for {app_name}: {e}")
        except AttributeError as e:
            print(f"Failed to find route getter for {app_name}: {e}")

    if getattr(app.config.start_page, "shuffle_icons", False):
        # Detach each icon from its app so the picture stops identifying the
        # tile. Both sets are permuted the same way, otherwise selecting a
        # `bw` theme would quietly undo the shuffle.
        apps_cfg = app.config.start_page.apps
        order = list(apps_cfg)
        shuffled = order[:]
        random.shuffle(shuffled)
        icons = {name: apps_cfg[name].get("icon") for name in order}
        bw_icons = {name: apps_cfg[name].get("bw_icon") for name in order}
        for name, source in zip(order, shuffled):
            apps_cfg[name].icon = icons[source]
            if bw_icons[source] is not None:
                apps_cfg[name].bw_icon = bw_icons[source]

    return app

app, rt = get_app()


@rt("/")
def get():
    # Get configuration from app.config
    config = app.config.start_page

    # Layout group selects the landing page. `gallery` (default) keeps the
    # html5up tile grid so existing tasks, prompts and the reference screenshot
    # are untouched; `desktop` renders the shell instead.
    if config.get("layout") == "desktop":
        return PageWrapper("main-page", render_desktop_shell(config), config=config)
    
    colors = tile_colors(config)

    # Build items based on app configurations
    items = []
    
    # Gather configured apps
    if hasattr(config, 'apps'):
        # Get enabled apps and sort by position
        enabled_apps = [(app_name, app_config) for app_name, app_config in config.apps.items() if app_config.get('enabled', True)]
        enabled_apps.sort(key=lambda x: x[1].get('position', 999))
        
        # Add items for each enabled app
        for index, (app_name, app_config) in enumerate(enabled_apps):
            # Skip the shopping app if disabled
            if app_name == "onlineshop" and not app.config.onlineshop.enable:
                continue
            # Get the app URL
            app_url = f"/{app_name}" if app_name != "vault" else "/todo"
            
            # Get the color (use index to cycle through available colors if needed)
            color_index = index % len(colors)
            color = colors[color_index]
            
            # Create the item
            items.append(
                ItemContent(
                    app_config.get('title', f"Open{app_name.capitalize()}"),
                    app_config.get('description', f"Description for {app_name}"),
                    icon=icon_for(app_config, app_name),
                    color=color,
                    href=app_url,
                    config=config,
                )
            )
    else:
        # Fallback to hardcoded items if no app configuration is available
        items = [
            ItemContent(
                "OpenTodos",
                "Manage your tasks and to-dos efficiently",
                icon="/assets/icons/real_icons/todo.png",
                color=colors[0] if colors else "#fdc891",
                href="/todo",
                config=config,
            ),
            ItemContent(
                "OpenCalendar",
                "Keep track of your appointments and events",
                color=colors[1] if len(colors) > 1 else "#fdc891",
                icon="/assets/icons/real_icons/calendar.png",
                href="/calendar",
                config=config,
            ),
            ItemContent(
                "OpenMessages",
                "Chat with friends and colleagues",
                icon="/assets/icons/real_icons/messages.png",
                color=colors[2] if len(colors) > 2 else "#fdc891",
                href="/messages",
                config=config,
            ),
            ItemContent(
                "OpenMaps",
                "Navigate and explore locations",
                icon="/assets/icons/real_icons/maps.png",
                color=colors[3] if len(colors) > 3 else "#fdc891",
                href="/maps",
                config=config,
            ),
            ItemContent(
                "OpenCodeEditor",
                "Write and edit code seamlessly",
                icon="/assets/icons/real_icons/code.png",
                color=colors[4] if len(colors) > 4 else "#fdc891",
                href="/codeeditor",
                config=config,
            ),
            ItemContent(
                "OpenShop",
                "Browse and purchase items online",
                color=colors[5] if len(colors) > 5 else "#fdc891",
                icon="/assets/icons/real_icons/shop.png",  
                href="/onlineshop",
                config=config,
            ),
            #ItemContent(
            #    "ClosedVault",
            #    "Securely store your files and data",
            #    icon="/assets/icons/real_icons/wallet.png",
            #    color=colors[6] if len(colors) > 6 else "#aad5cf",
            #    href="/todo",
            #    config=config,
            #),
        ]

    # Create pop-ups
    welcome_modals = []
    if hasattr(app.config, 'pop_ups') and app.config.pop_ups:
        for key, item in app.config.pop_ups.items():
            if item.url_extension == "":
                modal_content = []
                if item.content:
                    modal_content.append(P(item.content))
                if item.image_url:
                    modal_content.append(Img(src=item.image_url, cls="modal-image"))
                welcome_modals.append(
                    Modal(
                    id=f"welcome-modal-{key}",  # unique ID for each modal
                    content=Div(*modal_content) if modal_content else None,
                    title=item.title,
                    button_title=item.button_title,
                    link_button=item.link_button_title,
                    link_url=item.link_button_url,
                    cls=f"welcome-modal {item.position}"
                    )
                )

    # Create the gallery with configuration
    gallery = Gallery(
        items,
        style=config.get('style', 1),
        size=config.get('size', 'small'),
        random_tile_reoder=config.get('random_tile_reoder', False),
        fade_in=config.get('fade_in', True),
        lightbox=config.get('lightbox', False),
        config=config,
    )

    # Create the wrapper with configuration
    wrapper = Wrapper(
        config.headline,
        config.sub_header,
        Div(*welcome_modals, gallery),
        config=config,
    )
    
    # Return the page with configuration
    return PageWrapper(
        "main-page",
        wrapper,
        footer(),
        config=config,
        # Resolved per-request so live `reconfigure` theme swaps take effect.
        theme_css=render_theme_css(resolve_theme(app.config, "start_page")),
    )


def _enabled_apps(config):
    """(key, app_config) for every enabled app, in configured order."""
    if not hasattr(config, "apps"):
        return []
    items = [
        (name, cfg)
        for name, cfg in config.apps.items()
        if cfg.get("enabled", True)
        and not (name == "onlineshop" and not app.config.onlineshop.enable)
    ]
    items.sort(key=lambda kv: kv[1].get("position", 999))
    return items


def clock_text(desktop_cfg) -> str:
    """The toolbar time: real clock, unless config pins it.

    Live by default. A fixed ``time:`` in the layout config freezes it, which
    is what an eval wants -- ``tests/save_screenshots.py`` pixel-compares the
    start page, and a ticking clock changes that image on every run.
    """
    frozen = desktop_cfg.get("time")
    if frozen:
        return str(frozen)
    now = datetime.now()
    # %-I is glibc/BSD-only, so strip the pad by hand rather than relying on it.
    return f"{now.strftime('%I').lstrip('0') or '12'}:{now.strftime('%M %p')}"


def temperature_text(weather_cfg, units: str) -> str:
    """Render the configured temperature in the requested units.

    Config carries Celsius as the single source of truth and this converts,
    rather than config holding both -- two numbers that can disagree is a bug
    waiting to happen, and the task answer should not depend on which unit the
    agent happened to leave the toolbar in.
    """
    raw = weather_cfg.get("celsius", weather_cfg.get("temperature"))
    try:
        celsius = float(str(raw).rstrip("°CF").strip())
    except (TypeError, ValueError):
        return str(raw or "")
    if units == "fahrenheit":
        return f"{round(celsius * 9 / 5 + 32)}°F"
    return f"{round(celsius)}°C"


def _layout_variant(desktop_cfg, factor: str) -> str:
    """Which composition this form factor gets: ``shell`` | ``home_screen``.

    The mapping is config (``variants:`` in the layout file), not code, so a
    run can compare compositions on one device -- ``apps.start_page.desktop.
    variants.phone=shell`` renders the desktop shell in a 390px window, which
    is the control condition for "does the phone layout actually help".

    An unlisted form factor falls back to the desktop composition rather than
    to nothing: a new device file should render a working page before anyone
    has written a layout for it.
    """
    variants = desktop_cfg.get("variants") or {}
    return str(variants.get(factor, "shell"))


def _phone_home(*, status_bar, widget, grid, dock):
    """The phone composition: status bar, widget, icon grid, dock.

    Different markup from the desktop, not the same markup reflowed -- that is
    the whole reason the device is a config axis rather than a media query.
    The grid holds the apps that are *not* pinned and the dock holds the ones
    that are, so pinning moves an icon from the grid into the dock the way it
    moves an app onto the desktop on a laptop. Same route, same
    ``/desktop_all``, same reward; a different thing to look at and a
    different distance to travel.
    """
    return (
        status_bar,
        Div(widget, grid, cls="ui-desktop-surface"),
        dock,
    )


def _desktop_composition(*, toolbar, widget, dock_row):
    """The desktop composition: toolbar, centred headline, bottom-right dock."""
    return (
        toolbar,
        Div(widget, dock_row, cls="ui-desktop-surface"),
    )


def render_desktop_shell(config):
    """The whole desktop, as one swappable element.

    Every control in the shell targets ``#desktop-shell`` and swaps it
    outerHTML. That is why the theme's ``:root`` block is rendered *inside*
    this element rather than in the page head: toggling light/dark has to
    change the tokens, and a head-level block would not come back with the
    swap. Same reason the component stylesheet lives here.

    The composition is chosen by the configured device's form factor -- see
    ``_layout_variant`` and ``config/device/``.
    """
    desktop_cfg = _desktop_config(config)
    accents = desktop_cfg.get("accents", {}) or {}
    weather = desktop_cfg.get("weather", {}) or {}
    mode = _desktop_state["mode"]
    pinned = _desktop_state["pinned"]

    # The mode toggle picks which theme resolves, so the tokens in this block
    # change with it. Falls back to the app's configured theme when the two
    # Meta themes are not the ones in play.
    theme_name = {"light": "meta", "dark": "meta_dark"}.get(mode)
    theme_cfg = OmegaConf.create({"theme": theme_name}) if theme_name else app.config

    # Resolve the wallpaper. Returns None if every backend failed, in which
    # case we set no custom property and the CSS gradient fallback applies.
    wallpaper_cfg = desktop_cfg.get("wallpaper", {}) or {}
    shell_style = None
    if wallpaper_cfg.get("enabled", True):
        url = ensure_wallpaper(
            variant=int(wallpaper_cfg.get("variant", 0)),
            force=bool(wallpaper_cfg.get("regenerate", False)),
        )
        if url:
            shell_style = (
                f"--ui-wallpaper:url('{url}');"
                f"--ui-wallpaper-blur:{wallpaper_cfg.get('blur', '4px')};"
                f"--ui-wallpaper-fade:{wallpaper_cfg.get('fade', 0.7)};"
            )

    apps = _enabled_apps(config)
    launcher_items = [
        LauncherItem(
            title=cfg.get("title", key.capitalize()),
            href=f"/{key}",
            app_key=key,
            pinned=key in pinned,
        )
        for key, cfg in apps
    ]

    def tile(key, cfg, slot):
        return AppTile(
            title=cfg.get("title", key.capitalize()),
            href=f"/{key}",
            accent=accents.get(key),
            slot=slot,
        )

    factor = device.form_factor(app.config)
    variant = _layout_variant(desktop_cfg, factor)

    launcher = LauncherMenu(*launcher_items, open=_desktop_state["launcher_open"])
    weather_chip = WeatherChip(
        weather.get("condition", "clear"),
        temperature_text(weather, _desktop_state["units"]),
        units=_desktop_state["units"],
    )
    clock = Clock(clock_text(desktop_cfg))
    mode_toggle = ModeToggle(mode)
    headline = desktop_cfg.get("headline") or config.get("headline", "")

    if variant == "home_screen":
        # Grid: everything not pinned. Dock: everything pinned. Both follow the
        # configured app order rather than pin order, so the home screen does
        # not reshuffle every time something is pinned.
        grid_tiles = [tile(k, c, "shortcut") for k, c in apps if k not in pinned]
        dock_tiles = [tile(k, c, "favorite") for k, c in apps if k in pinned]
        body = _phone_home(
            # Time on the left, indicators on the right -- a status bar, not a
            # scaled-down toolbar. The brand moves into the widget below,
            # where there is room for it.
            status_bar=Toolbar(left=[clock], right=[weather_chip, mode_toggle]),
            widget=Div(
                Div(Wordmark(height=20), cls="ui-brand"),
                Text(headline, variant="body"),
                cls="ui-desktop-headline",
                data_testid="desktop-headline",
            ),
            grid=Div(
                Div(*grid_tiles, cls="ui-tile-dock", data_testid="desktop-tiles"),
                cls="ui-dock-row",
            ),
            # The pinned icons go in their own strip, with the launcher outside
            # it. The strip is what scrolls when enough apps are pinned to
            # overflow a 390px screen, and a scroll container clips what opens
            # out of it -- with the launcher inside, the panel was clipped to
            # the dock and the apps menu simply never appeared.
            dock=Div(
                Div(*dock_tiles, cls="ui-phone-dock-apps") if dock_tiles else None,
                launcher,
                cls="ui-phone-dock",
                data_testid="phone-dock",
            ),
        )
    else:
        tiles = [tile(k, c, "shortcut") for k, c in apps if k in pinned]
        body = _desktop_composition(
            toolbar=Toolbar(
                left=[launcher, Div(Wordmark(height=34), cls="ui-brand")],
                right=[weather_chip, clock, mode_toggle],
            ),
            widget=Div(
                Text(headline, variant="title"),
                cls="ui-desktop-headline",
                data_testid="desktop-headline",
            ),
            dock_row=Div(
                Div(*tiles, cls="ui-tile-dock", data_testid="desktop-tiles")
                if tiles
                else Text("Nothing pinned yet. Open the launcher to pin an app.", variant="caption"),
                cls="ui-dock-row",
            ),
        )

    return Div(
        theme_style(theme_cfg, "start_page"),
        component_styles(),
        *body,
        id="desktop-shell",
        # The form factor is on the root as a class *and* an attribute: the
        # class is what the stylesheet keys off, the attribute is what a test
        # or an agent can read without inspecting computed styles -- the same
        # reasoning as data-mode and data-pinned.
        cls=f"ui-desktop is-{factor}",
        style=shell_style,
        data_mode=mode,
        data_pinned=",".join(pinned),
        data_units=_desktop_state["units"],
        data_device=factor,
        data_layout=variant,
    )


@rt("/desktop/mode", methods=["POST"])
def toggle_mode():
    """Flip light/dark. Scoreable -- see /desktop_all."""
    _desktop_state["mode"] = "dark" if _desktop_state["mode"] == "light" else "light"
    return render_desktop_shell(app.config.start_page)


@rt("/desktop/pin/{app_key}", methods=["POST"])
def toggle_pin(app_key: str):
    """Pin or unpin an app. Scoreable -- see /desktop_all.

    Unknown keys are ignored rather than 404'd: the shell is re-rendered either
    way, so a stale button never leaves the page in a broken state.
    """
    known = {key for key, _ in _enabled_apps(app.config.start_page)}
    if app_key in known:
        pinned = _desktop_state["pinned"]
        if app_key in pinned:
            pinned.remove(app_key)
        else:
            pinned.append(app_key)
    return render_desktop_shell(app.config.start_page)


@rt("/desktop/units", methods=["POST"])
def toggle_units():
    """Switch the toolbar between Celsius and Fahrenheit. Scoreable."""
    _desktop_state["units"] = (
        "fahrenheit" if _desktop_state["units"] == "celsius" else "celsius"
    )
    return render_desktop_shell(app.config.start_page)


@rt("/desktop/launcher", methods=["POST"])
def toggle_launcher():
    """Open/close the launcher panel. Not scoreable -- transient UI."""
    _desktop_state["launcher_open"] = not _desktop_state["launcher_open"]
    return render_desktop_shell(app.config.start_page)


@app.get("/desktop_all")
def desktop_all():
    """Scoreable shell state, for reward computation.

    Only the two durable fields. `launcher_open` is excluded on purpose: a task
    should not pass or fail on whether a popover was left showing.
    """
    payload = {
        "mode": _desktop_state["mode"],
        "pinned": list(_desktop_state["pinned"]),
        "units": _desktop_state["units"],
    }
    return Response(json.dumps(payload), headers={"Content-Type": "application/json"})


@rt("/environment_variables")
def get():
    # Prints or returns the environment variables dictionary
    # we can use this to score an envrionment and improve an agent
    return app.config


if __name__ == "__main__":
    serve(reload=False)
