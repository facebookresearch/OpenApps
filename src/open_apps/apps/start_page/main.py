"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
# assets come from https://html5up.net/story
from fasthtml.common import *
import random
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
    "openbanking": (
        "open_apps.apps.openbanking_app",
        "get_openbanking_routes",
    ),
}

APP_MODULE_TO_NAME = {
    "open_apps.apps.todo_app": "todo",
    "open_apps.apps.calendar_app": "calendar",
    "open_apps.apps.messenger_app": "messenger",
    "open_apps.apps.codeeditor_app": "code_editor",
    "open_apps.apps.map_app": "maps",
    "open_apps.apps.openbanking_app": "openbanking",
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


@rt("/environment_variables")
def get():
    # Prints or returns the environment variables dictionary
    # we can use this to score an envrionment and improve an agent
    return app.config


if __name__ == "__main__":
    serve(reload=False)
