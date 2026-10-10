"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fasthtml.common import *
import random
import os
from pathlib import Path

from open_apps.frontend import local_hdrs
from open_apps.ui.brand import Wordmark

# src/proficiency_playground/playground_server
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def generate_random_colors(num_colors):
    colors = set()
    while len(colors) < num_colors:
        # Generate a random color in hexadecimal format
        color = "#{:06x}".format(random.randint(0, 0xFFFFFF))
        colors.add(color)
    return list(colors)


def class_list(*args):
    return " ".join(
        f"{pre}{arg}" if arg is not True else pre
        for pre, arg in zip(args[::2], args[1::2])
        if arg
    )


def Wrapper(title, description, content, style=1, align=None, color=None, invert=False, config=None):
    """
    Create a wrapper section with configurable styling.

    Args:
        title: Section title
        description: Section description; omitted from the markup when empty
        content: Section content
        style: Wrapper style number
        align: Content alignment
        color: Theme color number
        invert: Whether to invert colors
        config: Configuration dictionary with styling options
    """
    # Update parameters from config if provided
    if config:
        style = config.get('wrapper_style', style)
        align = config.get('wrapper_align', align)
        color = config.get('wrapper_color', color)
        invert = config.get('wrapper_invert', invert)

    wrapper_classes = class_list(
        "wrapper style", style, "align-", align, "invert", invert, "color", color
    )

    # Typography is inherited from the shared theme's tokens (see the
    # stylesheet PageWrapper emits) rather than written as inline styles from
    # per-app font config, which is what the `appearance` group used to supply.
    # An empty sub-header is dropped rather than rendered as a blank <p>: it
    # only added a paragraph-sized gap between the headline and the grid.
    # The OpenApps wordmark heads the page, as a product's logo heads its
    # new-tab page. It is an inline SVG (role="img", labelled "OpenApps"), not
    # a link, so the page's set of links is unchanged.
    inner_content = [
        Div(Wordmark(height=40), cls="launcher-brand"),
        H2(title, cls="launcher-title"),
    ]
    if description and str(description).strip():
        inner_content.append(P(description, cls="launcher-subtitle"))
    inner_content.append(content)

    return Section(Div(*inner_content, cls="inner"), cls=wrapper_classes)


def ItemContent(title, description, color=None, icon=None, xtra=None, href="#", config=None):
    """
    One launcher entry: a colored tile holding the app's icon and name (and,
    when the content variant supplies one, its description).

    The whole entry is a single link, as on an OS start screen or a new-tab
    page, so the icon, the name and the description all open the app.

    Args:
        title: The app name shown on the tile
        description: Description text; omitted from the markup when empty
        color: Hex fill for the tile (from the layout's palette)
        icon: Icon name (for FontAwesome) or path to image
        xtra: Additional content to append
        href: Link URL when item is clicked
        config: Configuration dictionary with styling options
    """
    content = []

    # The fill is the one per-item color: it comes from the layout's palette
    # (or the theme tone's single fill), and reaches the stylesheet as a custom
    # property so every other declaration can stay in the token stylesheet.
    fill = f"--tile-fill: {color}" if color and str(color).startswith('#') else None

    # Handle icon (either FontAwesome or image)
    if icon:
        if icon.endswith(('.png', '.jpg', '.jpeg', '.svg')):  # Check if icon is an image file
            content.append(Span(Img(src=icon, alt=title), cls="tile-icon"))
        else:
            content.append(Span(cls=f"tile-icon icon style2 major fa-{icon}"))

    # Tile typography comes from the shared theme (the `.launcher` rules in
    # PageWrapper), not from inline per-app font config.
    content.append(H3(title))
    if description and str(description).strip():
        content.append(P(description))

    # Add any extra content
    if xtra:
        content.append(xtra if isinstance(xtra, (list, tuple)) else [xtra])

    if href and href != "#":
        return A(*content, href=href, cls="item", style=fill)
    return Div(*content, cls="item", style=fill)


def Gallery(
    items,
    size="small",
    fade_in=False,
    random_tile_reoder: bool = False,
    has_descriptions: bool = False,
    light_glyphs: bool = False,
    config=None,
):
    """
    The launcher grid.

    Args:
        items: List of ItemContent objects
        size: Size of the tiles - "small", "medium", or "big"
        fade_in: Whether to fade in items on scroll
        random_tile_reoder: Whether to shuffle the items
        has_descriptions: At least one item carries a description. Switches the
            grid from icon-and-label tiles to wider cards, the way a store or
            settings listing lays out apps it describes -- a paragraph squeezed
            under a launcher icon is unreadable.
        light_glyphs: The icons are black line art on a dark single fill (the
            `bw` icon set under a non-light tone), so draw them inverted.
        config: Configuration dictionary with styling options
    """
    # Override defaults with config if provided
    if config:
        size = config.get('size', size)
        fade_in = config.get('fade_in', fade_in)
        random_tile_reoder = config.get('random_tile_reoder', random_tile_reoder)

    # Shuffle items if configured
    if random_tile_reoder:
        random.shuffle(items)

    classes = ["items", "launcher"]
    if size in ["small", "medium", "big"]:
        classes.append(size)
    if has_descriptions:
        classes.append("has-descriptions")
    if light_glyphs:
        classes.append("glyphs-light")
    if fade_in:
        classes.append("onscroll-fade-in")
    if config and config.get('item_hover_effect') is False:
        classes.append("no-hover")

    # Label ink on the tile fill belongs with the fill palette (the layout's
    # `tile_label_*` keys), not the theme: no theme token means "text on an
    # arbitrary saturated hue", and `--color-on-primary` is black under `dark`.
    ink = []
    if config and config.get('tile_label_color'):
        ink.append(f"--tile-ink: {config['tile_label_color']}")
    if config and config.get('tile_label_shadow'):
        ink.append(f"--tile-ink-shadow: {config['tile_label_shadow']}")

    return Div(*items, cls=" ".join(classes), style="; ".join(ink) or None)


scr_fns = [
    "jquery.min",
    "jquery.scrollex.min",
    "jquery.scrolly.min",
    "browser.min",
    "breakpoints.min",
    "util",
    "main",
]

scripts = [
    Script(src=f"/assets/js/{js}.js")
    for js in scr_fns
]

def Modal(content, id="modal", title="Notice", button_title="Close", link_button=None, link_url=None, cls=None):
    modal_classes = f"modal {cls or ''}".strip()

    # Create footer buttons with improved styling
    footer_buttons = [Button(button_title, cls="close-modal",
                           onclick="closeModal()",
                           style="min-width: 100px; padding: 8px 16px; margin: 5px; white-space: nowrap;")]

    # Add link button if specified
    if link_button and link_url:
        footer_buttons.append(
            Button(link_button, cls="link-button",
                  onclick=f"window.location.href='{link_url}'",
                  style="min-width: 100px; padding: 8px 16px; margin: 5px; white-space: nowrap;")
        )

    # Wrap content in a scrollable div
    content_wrapper = Div(
        content,
        style="max-height: 60vh; overflow-y: auto; padding-right: 16px;"
    )

    return Div(
        Div(
            Div(
                H2(title, style="margin-bottom: 16px;"),
                content_wrapper,
                Div(
                    *footer_buttons,
                    cls="modal-footer",
                    style="margin-top: 16px; display: flex; justify-content: flex-end; gap: 10px; flex-wrap: wrap;"
                ),
                cls="modal-content"
            ),
            cls="modal-dialog"
        ),
        id=id,
        cls=modal_classes
    )

class Raw:
    def __init__(self, content):
        self.content = content

    def __str__(self):
        return self.content

# The launcher's stylesheet. Every color, font, radius and gap is a `var()`
# into the shared theme tokens (or a `color-mix()` of them); the only literal
# per-item value is the tile fill, passed in as `--tile-fill`. Selectors are
# anchored on `#wrapper` so they outrank the Story template (main.css) and Pico,
# which both still load on this page, without `!important`.
LAUNCHER_CSS = """
    html, body {
        background-color: var(--color-bg);
    }
    body {
        font-family: var(--font-family);
        font-size: var(--font-size-base);
        color: var(--color-fg);
    }
    h1, h2, h3, h4, h5, h6 {
        font-family: var(--font-heading);
        color: var(--color-fg);
    }

    /* Page: the grid fills the viewport and the footer sits at the bottom,
       like a new-tab page, instead of floating under a fixed 7rem gap. */
    #wrapper {
        display: flex;
        flex-direction: column;
        min-height: 100vh;
        background-color: var(--color-bg);
    }
    #wrapper > .wrapper {
        background-color: var(--color-bg);
        box-shadow: none;
    }
    #wrapper > section.wrapper {
        flex: 1 0 auto;
    }
    #wrapper > section.wrapper > .inner {
        width: auto;
        max-width: calc(var(--space) * 120);
        margin: 0 auto;
        padding: calc(var(--space) * 12) calc(var(--space) * 4) calc(var(--space) * 8);
    }

    /* Header: the wordmark, then the welcome line beneath it. The mark and
       word draw in currentColor, so pinning `color` is what keeps the lockup
       on the theme's text colour rather than Pico's heading colour. */
    #wrapper .launcher-brand {
        display: flex;
        justify-content: center;
        margin: 0 0 calc(var(--space) * 2.5);
        color: var(--color-fg);
    }
    #wrapper .launcher-brand .ui-wordmark {
        display: block;
        width: auto;
        height: calc(var(--space) * 5);
    }
    #wrapper .launcher-title {
        margin: 0 0 calc(var(--space) * 1.5);
        font-family: var(--font-heading);
        font-size: calc(var(--font-size-heading) * 1.5);
        font-weight: 400;
        line-height: 1.2;
        letter-spacing: normal;
        color: var(--color-fg);
        text-align: center;
    }
    #wrapper .launcher-subtitle {
        max-width: 70ch;
        margin: 0 auto;
        font-size: var(--font-size-base);
        line-height: 1.6;
        color: color-mix(in srgb, var(--color-muted) 50%, var(--color-fg));
        text-align: center;
        text-wrap: pretty;
    }

    /* Grid: fixed-width square tiles, centered as a block, rows filled left
       to right so a short last row stays on the column lines -- a start
       screen's tile grid. Three columns keeps the row breaks the page has
       always had. */
    #wrapper .launcher {
        --tile-width: calc(var(--space) * 20);
        --icon-size: calc(var(--space) * 7);
        display: grid;
        grid-template-columns: repeat(3, minmax(0, var(--tile-width)));
        justify-content: center;
        gap: calc(var(--space) * 2);
        width: auto;
        margin: calc(var(--space) * 6) auto 0;
        padding: 0;

        .item {
            &:hover {
                opacity: 100%;
            }
            opacity: 80%;
            .inner {
                display: flex;
                align-items: center;
                flex-direction: column;
            }
        }
    }
    #wrapper .launcher.medium {
        --tile-width: calc(var(--space) * 23);
        --icon-size: calc(var(--space) * 8);
    }
    #wrapper .launcher.big {
        --tile-width: calc(var(--space) * 27);
        --icon-size: calc(var(--space) * 10);
    }

    /* Tile: the colored square is the link, with the icon and the name
       centred in it as one group.

       In a browser, the Story template's main.js wraps each tile's children
       in a `<div class="inner">` (`$('.items').children().wrapInner(...)`) --
       it is what the optional `fade_in` scroll reveal animates. That wrapper
       never appears in server-rendered markup, which is how an unstyled one
       shrink-wrapped to the label and pushed every icon off-centre without a
       markup test noticing. So the tile and the wrapper share one centred
       column rule: identical with or without JS, and the fade still has its
       element to animate. */
    #wrapper .launcher > .item,
    #wrapper .launcher > .item > .inner {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: var(--space);
        text-align: center;
    }
    #wrapper .launcher > .item > .inner {
        width: 100%;
        height: 100%;
        margin: 0;
        padding: 0;
    }
    #wrapper .launcher > .item {
        aspect-ratio: 1;
        min-width: 0;
        margin: 0;
        padding: calc(var(--space) * 1.5);
        border: 0;
        border-radius: calc(var(--radius) * 1.5);
        background-color: var(--tile-fill, var(--color-surface));
        box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-fg) 8%, transparent);
        color: var(--tile-ink, var(--color-fg));
        text-decoration: none;
        transition: all 0.8s cubic-bezier(0.16, 1, 0.3, 1);
    }
    /* Hover is signalled by the opacity lift on `.item` above, not a fill
       shift, so the tile keeps its theme colour. */
    #wrapper .launcher > .item:hover {
        color: var(--tile-ink, var(--color-fg));
        text-decoration: none;
    }
    #wrapper .launcher.no-hover > .item:hover {
        background-color: var(--tile-fill, var(--color-surface));
    }
    #wrapper .launcher > .item:focus-visible {
        outline: 2px solid var(--color-primary);
        outline-offset: 3px;
        box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-fg) 8%, transparent);
    }
    #wrapper .launcher > .item:active {
        transform: scale(0.97);
    }

    /* Icon: sized in steps of the spacing token. */
    #wrapper .launcher .tile-icon {
        display: grid;
        place-items: center;
        width: var(--icon-size);
        max-width: 100%;
        aspect-ratio: 1;
        margin: 0;
    }
    #wrapper .launcher .tile-icon img {
        display: block;
        width: 100%;
        height: auto;
        margin: 0;
        object-fit: contain;
        border-radius: 0;
    }
    #wrapper .launcher.glyphs-light .tile-icon img {
        filter: invert(1);
    }

    /* Label and description */
    #wrapper .launcher > .item h3 {
        width: 100%;
        margin: 0;
        font-family: var(--font-family);
        font-size: var(--font-size-sm);
        font-weight: 600;
        line-height: 1.25;
        letter-spacing: normal;
        color: inherit;
        text-shadow: 0 1px 2px var(--tile-ink-shadow, transparent);
        overflow-wrap: break-word;
    }
    #wrapper .launcher > .item h3,
    #wrapper .launcher > .item p {
        text-align: inherit;
    }
    #wrapper .launcher > .item p {
        margin: 0;
        font-size: var(--font-size-sm);
        line-height: 1.5;
        color: color-mix(in srgb, var(--color-muted) 50%, var(--color-fg));
    }

    /* Cards: when the apps come with descriptions, each app gets a listing
       card -- colored icon and name on one line, description underneath -- in
       as many columns as fit, down to one on a phone. A paragraph of white
       text across a saturated tile is unreadable, so the fill moves onto the
       icon here. */
    #wrapper .launcher.has-descriptions {
        --icon-size: calc(var(--space) * 6);
        grid-template-columns: repeat(auto-fill, minmax(min(100%, calc(var(--space) * 32)), 1fr));
        justify-content: stretch;
        max-width: calc(var(--space) * 108);
    }
    #wrapper .launcher.has-descriptions > .item,
    #wrapper .launcher.has-descriptions > .item > .inner {
        display: grid;
        grid-template-columns: var(--icon-size) minmax(0, 1fr);
        justify-items: stretch;
        align-items: center;
        align-content: start;
        gap: calc(var(--space) * 1.5);
        text-align: left;
    }
    #wrapper .launcher.has-descriptions > .item > .inner {
        grid-column: 1 / -1;
        height: auto;
    }
    #wrapper .launcher.has-descriptions > .item {
        aspect-ratio: auto;
        padding: calc(var(--space) * 2.5);
        border: 1px solid var(--color-border);
        background-color: var(--color-surface);
        box-shadow: none;
        color: var(--color-fg);
    }
    #wrapper .launcher.has-descriptions > .item:hover {
        background-color: color-mix(in srgb, var(--color-fg) 5%, var(--color-surface));
        color: var(--color-fg);
    }
    #wrapper .launcher.has-descriptions.no-hover > .item:hover {
        background-color: var(--color-surface);
    }
    #wrapper .launcher.has-descriptions > .item:active {
        transform: none;
    }
    #wrapper .launcher.has-descriptions .tile-icon {
        border-radius: calc(var(--radius) * 1.5);
        background-color: var(--tile-fill, var(--color-surface));
        box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-fg) 8%, transparent);
    }
    #wrapper .launcher.has-descriptions .tile-icon img {
        width: 70%;
    }
    #wrapper .launcher.has-descriptions > .item h3 {
        font-size: var(--font-size-base);
        text-shadow: none;
    }
    #wrapper .launcher.has-descriptions > .item p {
        grid-column: 1 / -1;
    }

    /* Footer */
    #wrapper > footer.wrapper {
        flex: none;
        border-top: 1px solid var(--color-border);
    }
    #wrapper > footer.wrapper > .inner {
        width: auto;
        padding: calc(var(--space) * 2.5) calc(var(--space) * 4);
        font-size: var(--font-size-sm);
    }
    #wrapper > footer.wrapper a {
        color: var(--color-muted);
        text-decoration: none;
        border-radius: var(--radius);
    }
    #wrapper > footer.wrapper a:hover {
        color: var(--color-fg);
        text-decoration: underline;
    }
    #wrapper > footer.wrapper a:focus-visible {
        outline: 2px solid var(--color-primary);
        outline-offset: 2px;
        box-shadow: none;
    }

    @media screen and (max-width: 736px) {
        #wrapper > section.wrapper > .inner {
            padding: calc(var(--space) * 6) calc(var(--space) * 2) calc(var(--space) * 4);
        }
        #wrapper .launcher-title {
            font-size: calc(var(--font-size-heading) * 1.25);
        }
        #wrapper .launcher {
            margin-top: calc(var(--space) * 4);
            gap: calc(var(--space) * 1.5);
        }
    }
    /* Phones: two tiles per row, as phone start screens lay out medium
       tiles, rather than three squeezed until the names break. */
    @media screen and (max-width: 480px) {
        #wrapper .launcher:not(.has-descriptions) {
            grid-template-columns: repeat(2, minmax(0, var(--tile-width)));
        }
    }
    @media (prefers-reduced-motion: reduce) {
        #wrapper .launcher > .item {
            transition: none;
        }
        #wrapper .launcher > .item:active {
            transform: none;
        }
    }
"""


def PageWrapper(title, *content, config=None, theme_css=""):
    """
    Create a page wrapper with custom styling from configuration.

    Args:
        title: Page title
        *content: Content elements
        config: Configuration dictionary with styling options
        theme_css: Shared design-token CSS from ``open_apps.theme``. Emitted
            last; every color and font in ``LAUNCHER_CSS`` is a ``var()`` into it.
    """
    modal_styles = f"""
        <style>
            .modal {{
                display: none;
                position: fixed;
                z-index: 1000;
                left: 0;
                top: 0;
                width: 100%;
                height: 100%;
                background-color: rgba(0,0,0,0.5);
            }}
            .modal-dialog {{
                position: relative;
                margin: auto;
                width: 80%;
                max-width: 500px;
            }}
            /* Position variants */
            .modal.top .modal-dialog {{ margin-top: 5%; }}
            .modal.center .modal-dialog {{ margin-top: 15%; }}
            .modal.bottom .modal-dialog {{ margin-top: 25%; }}
            .modal.left .modal-dialog {{ margin-left: 5%; }}
            .modal.right .modal-dialog {{ margin-left: auto; margin-right: 5%; }}

            /* Themed like the rest of the page on purpose: a pop-up that
               stayed near-white on a dark theme would be trivially easy to
               spot, which confounds the adversarial-pop-up variation. */
            .modal-content {{
                background-color: var(--color-surface);
                color: var(--color-fg);
                padding: 20px;
                padding-bottom: 80px;
                border-radius: var(--radius);
                box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            }}
            .close-modal {{
                float: right;
                cursor: pointer;
            }}
            .modal-footer {{
                display: flex;
                justify-content: flex-end;
                gap: 10px;
                margin-top: 20px;
            }}

            .link-button {{
                background-color: var(--color-accent);
                color: var(--color-btn-fg);
            }}

            .link-button:hover {{
                background-color: var(--color-primary-hover);
            }}

            {LAUNCHER_CSS}

            {theme_css}
        </style>
        <script>
            function showModal(id) {{
                document.getElementById(id).style.display = "block";
            }}
            function closeModal() {{
                document.querySelectorAll('.modal').forEach(modal => {{
                    modal.style.display = "none";
                }});
            }}
        </script>
    """

    auto_show_modal = """
        <script>
            let modalIndex = 0;
            const showNextModal = () => {
                const modals = document.querySelectorAll('.welcome-modal');
                if (modalIndex < modals.length) {
                    modals[modalIndex].style.display = "block";
                }
            };

            function closeModal() {
                document.querySelectorAll('.modal').forEach(modal => {
                    modal.style.display = "none";
                });
                modalIndex++;
                showNextModal();
            }

            window.onload = function() {
                showNextModal();
            }
        </script>
    """
    return (
        Title(title),
        Raw(modal_styles + auto_show_modal),
        Div(*content, id="wrapper", cls="divided"),
        *scripts
    )

def get_app(hdrs=None, *args, **kwargs):
    if hdrs is None:
        hdrs = []
    assets_dir = Path(__file__).parent.parent / "assets"
    hdrs.append(
        Link(
            rel="stylesheet",
            href="/assets/css/main.css",
        )
    )
    # This is the app that actually serves every route -- the other apps' routes
    # are mounted onto it -- so its headers are what the browser sees. htmx and
    # Pico come from apps/assets/vendor via the static route below rather than
    # from jsdelivr; default_hdrs=False is what stops FastHTML prepending the
    # CDN copies. See src/open_apps/frontend.py for why that matters.
    hdrs = local_hdrs() + hdrs
    app = FastHTML(hdrs=hdrs, *args, default_hdrs=False, **kwargs)

    @app.get("/{fname:path}.{ext:static}")
    def static(fname: str, ext: str):
        full_path = os.path.join(BASE_DIR, f"{fname}.{ext}")
        if os.path.exists(full_path):
            return FileResponse(full_path)

    return app, app.route


def footer():
    links = A("main-page", href="/")
    return Footer(Div(links, cls="inner"), cls="wrapper style1 align-center")

def create_logo_header(app_config, base_url: str, current_file_path: str):
    """
    Creates a reusable, clickable logo and title header component.

    :param app_config: The configuration object for the specific app (e.g., config.start_page.apps.codeeditor).
    :param base_url: The base URL for the link (e.g., '/codeeditor').
    :param current_file_path: The path of the calling script (__file__).
    :return: A fasthtml component (A tag).
    """
    current_dir = os.path.dirname(os.path.abspath(current_file_path))
    parent_dir = os.path.dirname(current_dir)

    file_path = os.path.join(parent_dir, app_config.icon.lstrip('/'))

    logo = ""
    if os.path.exists(file_path):
        # logo = Img(src=app_config.icon, cls="h-10 mr-3")
        logo = Img(src=app_config.icon, style="height: 2.5rem; margin-right: 0.75rem;")
    else:
        logger.error(f"Logo file not found at: {file_path}")

    if app_config.clickable_logo:
        return A(
            logo,
            H1(app_config.title, style="font-size: 1.5rem; font-weight: bold; margin: 0; font-family: inherit;"),
            style="display: flex; align-items: center; margin-bottom: 1rem; text-decoration: none; color: inherit;",
            href=base_url,
        )
    else:
        return Div(
        logo,
            H1(app_config.title, style="font-size: 1.5rem; font-weight: bold; margin: 0; font-family: inherit;"),
            style="display: flex; align-items: center; margin-bottom: 1rem; text-decoration: none; color: inherit;",
        )

def DelayedContent(content, delay_ms=2000):
    """
    A component that shows a loading spinner for a specified delay,
    then reveals the actual content.
    """
    spinner_id = "loading-spinner-container"
    content_id = "delayed-page-content"

    return Div(
        # The loading spinner, shown by default
        Div(
            Span(cls="loading loading-spinner loading-lg"),
            id=spinner_id,
            cls="h-screen flex flex-col justify-center items-center"
        ),
        # The actual page content, hidden by default
        Div(
            content,
            id=content_id,
            style="display: none;"
        ),
        # The script that handles the switch
        Script(f"""
            setTimeout(() => {{
                const spinner = document.getElementById('{spinner_id}');
                const content = document.getElementById('{content_id}');
                if (spinner) spinner.style.display = 'none';
                if (content) content.style.display = 'block';
            }}, {delay_ms});
        """)
    )
