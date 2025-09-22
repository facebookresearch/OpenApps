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
        # randomize icons and app names if specified
        icons = [getattr(app.config.start_page.apps[app_name], "icon") for app_name in app.config.start_page.apps]
        random.shuffle(icons)
        print(app.config.start_page.apps)
        for app_name in app.config.start_page.apps:
            app.config.start_page.apps[app_name].icon = icons.pop()

    return app

app, rt = get_app()


@rt("/")
def get():
    # Get configuration from app.config
    config = app.config.start_page
    
    # Check if we should use random colors
    colors = []
    if hasattr(config, 'use_random_colors') and config.use_random_colors:
        colors = generate_random_colors(10)  # Generate more than we need
    else:
        colors = config.app_background_colors
    
    # Build items based on app configurations
    items = []
    
    # Gather configured apps
    if hasattr(config, 'apps'):
        # Get enabled apps and sort by position
        enabled_apps = [(app_name, app_config) for app_name, app_config in config.apps.items() if app_config.get('enabled', True)]
        enabled_apps.sort(key=lambda x: x[1].get('position', 999))
        
        # Add items for each enabled app
        for index, (app_name, app_config) in enumerate(enabled_apps):
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
                    icon=app_config.get('icon', f"/assets/icons/real_icons/{app_name}.png"),
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
    return PageWrapper("main-page", wrapper, footer(), config=config)


@rt("/environment_variables")
def get():
    # Prints or returns the environment variables dictionary
    # we can use this to score an envrionment and improve an agent
    return app.config


if __name__ == "__main__":
    serve(reload=False)
