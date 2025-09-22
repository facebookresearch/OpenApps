"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fasthtml.common import *
import random
import os
import subprocess
from pathlib import Path

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
        description: Section description
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
    
    # Apply custom styling to title and description
    title_style = {}
    desc_style = {}
    
    if config:
        if config.get('heading_font'):
            title_style['font-family'] = config['heading_font']
        if config.get('heading_color'):
            title_style['color'] = config['heading_color']
        if config.get('font_family'):
            desc_style['font-family'] = config['font_family']
        if config.get('font_color'):
            desc_style['color'] = config['font_color']
    
    inner_content = [
        H2(title, style=";".join([f"{k}:{v}" for k, v in title_style.items()]) if title_style else None),
        P(description, style=";".join([f"{k}:{v}" for k, v in desc_style.items()]) if desc_style else None),
        content
    ]
    
    return Section(Div(*inner_content, cls="inner"), cls=wrapper_classes)


def ItemContent(title, description, color="primary", icon=None, xtra=None, href="#", config=None):
    """
    Create an item following the Story template pattern with extensive configurability.
    
    Args:
        title: The title of the item
        description: Description text
        color: Can be a hex color code or one of the Story theme colors
        icon: Icon name (for FontAwesome) or path to image
        xtra: Additional content to append
        href: Link URL when item is clicked
        config: Configuration dictionary with styling options
    """
    content = []
    
    # Set defaults if config is None
    if config is None:
        config = {}
    
    # Handle icon (either FontAwesome or image)
    if icon:
        if icon.endswith(('.png', '.jpg', '.jpeg', '.svg')):  # Check if icon is an image file
            # Create an image wrapper with proper scaling
            content.append(Span(
                Img(src=icon, alt=title),
                cls="image icon"
            ))
        else:
            content.append(Span(cls=f"icon style2 major fa-{icon}"))
    
    # Apply font styling to title and description if configured
    title_style = {}
    desc_style = {}
    
    if config.get('heading_font'):
        title_style['font-family'] = config['heading_font']
    if config.get('heading_color'):
        title_style['color'] = config['heading_color']
    if config.get('font_family'):
        desc_style['font-family'] = config['font_family']
    if config.get('font_color'):
        desc_style['color'] = config['font_color']
    if config.get('font_size'):
        base_size = int(config['font_size'])
        title_style['font-size'] = f"{base_size * 1.5}px"
        desc_style['font-size'] = f"{base_size}px"
    
    # Add title and description with any configured styling
    content.append(H3(title, style=";".join([f"{k}:{v}" for k, v in title_style.items()]) if title_style else None))
    content.append(P(description, style=";".join([f"{k}:{v}" for k, v in desc_style.items()]) if desc_style else None))
    
    # Add any extra content
    if xtra:
        content.append(xtra if isinstance(xtra, (list, tuple)) else [xtra])
    
    # Determine how to handle the color based on configuration
    style_attr = []
    
    # Use theme colors if configured, otherwise use custom color
    if config.get('use_theme_colors') and config.get('theme_color'):
        theme_class = f"color{config['theme_color']}"
    else:
        theme_class = None
        # Apply custom background color if provided
        if color and color.startswith('#'):
            style_attr.append(f"background-color: {color};")
    
    # Apply additional styling from config
    if config.get('item_text_align'):
        style_attr.append(f"text-align: {config['item_text_align']};")
    if config.get('item_border_radius'):
        style_attr.append(f"border-radius: {config['item_border_radius']}px;")
    if config.get('item_padding'):
        style_attr.append(f"padding: {config['item_padding']}em;")
        
    # Create the actual item content section with style
    inner_content = Section(
        *content,
        style=";".join(style_attr) if style_attr else None,
        cls=theme_class
    )
    
    # Build additional item classes
    item_classes = ["item"]
    
    # For the Story template, items are wrapped in links if href is provided
    if href and href != "#":
        return A(
            inner_content,
            href=href,
            cls=" ".join(item_classes),
            style=";".join(style_attr) if style_attr else None,
        )
    else:
        return inner_content


def Gallery(
    items,
    style=1,
    size="medium",
    lightbox=False,
    fade_in=False,
    random_tile_reoder: bool = False,
    config=None,
):
    """
    Create a gallery/items grid following the Story template pattern with extensive configurability.
    
    Args:
        items: List of ItemContent objects
        style: Style number (1, 2, or 3) as per Story template
        size: Size of items - "small", "medium", or "big"
        lightbox: Whether to enable lightbox for gallery images
        fade_in: Whether to fade in items on scroll
        random_tile_reoder: Whether to shuffle the items
        config: Configuration dictionary with styling options
    """
    # Override defaults with config if provided
    if config:
        style = config.get('style', style)
        size = config.get('size', size)
        lightbox = config.get('lightbox', lightbox)
        fade_in = config.get('fade_in', fade_in)
        random_tile_reoder = config.get('random_tile_reoder', random_tile_reoder)
    
    # Shuffle items if configured
    if random_tile_reoder:
        random.shuffle(items)
        
    # Build the appropriate class list according to Story template
    classes = []
    classes.append(f"items style{style}")
    
    # Add size modifier
    if size in ["small", "medium", "big"]:
        classes.append(size)
    
    # Add lightbox support if needed
    if lightbox:
        classes.append("lightbox")
    
    # Add fade-in effect if needed
    if fade_in:
        classes.append("onscroll-fade-in")
    
    # Additional styling based on config
    custom_style = []
    if config and config.get('item_hover_effect') is False:
        custom_style.append("--hover-effect: none;")
    
    return Div(
        *items,
        cls=" ".join(classes),
        style=";".join(custom_style) if custom_style else None,
    )


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

def PageWrapper(title, *content, config=None):
    """
    Create a page wrapper with custom styling from configuration.
    
    Args:
        title: Page title
        *content: Content elements
        config: Configuration dictionary with styling options
    """
    # Set defaults if config is None
    if config is None:
        config = {}
    
    # Generate custom CSS based on configuration
    custom_css = ""
    
    # Font settings
    if config.get('font_family'):
        custom_css += f"body {{ font-family: {config['font_family']}; }}\n"
    if config.get('font_color'):
        custom_css += f"body {{ color: {config['font_color']}; }}\n"
    if config.get('heading_font'):
        custom_css += f"h1, h2, h3, h4, h5, h6 {{ font-family: {config['heading_font']}; }}\n"
    if config.get('heading_color'):
        custom_css += f"h1, h2, h3, h4, h5, h6 {{ color: {config['heading_color']}; }}\n"
    if config.get('background_color'):
        custom_css += f"body {{ background-color: {config['background_color']}; }}\n"
    
    # Add iOS-style typography for items
    custom_css += """
        .item h3 {
            font-size: 0.9em !important;
            font-weight: 600 !important;
            margin: 0.5em 0 0.2em 0 !important;
            color: white !important;
            text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3) !important;
        }
        
        .item p {
            font-size: 0.7em !important;
            margin: 0 !important;
            opacity: 0.9 !important;
            color: rgba(255, 255, 255, 0.9) !important;
            text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3) !important;
            line-height: 1.2 !important;
        }
    """
    
    # Item styling
    item_styles = []
    if config.get('item_border_radius') is not None:
        item_styles.append(f"border-radius: {config['item_border_radius']}px")
    if config.get('item_padding') is not None:
        item_styles.append(f"padding: {config['item_padding']}em")
    if config.get('item_text_align'):
        item_styles.append(f"text-align: {config['item_text_align']}")
    
    # Add iPhone-style box shadow and modern styling
    item_styles.append("box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1), 0 1px 3px rgba(0, 0, 0, 0.2)")
    item_styles.append("transition: all 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94)")
    item_styles.append("min-height: 120px")
    item_styles.append("display: flex")
    item_styles.append("flex-direction: column")
    item_styles.append("justify-content: center")
    item_styles.append("align-items: center")
    
    if item_styles:
        custom_css += f".item {{ {'; '.join(item_styles)}; }}\n"
    
    # Hover effects
    hover_effects = []
    if config.get('hover_shadow') is True:
        hover_effects.append("box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15), 0 2px 5px rgba(0, 0, 0, 0.3)")
    if config.get('hover_scale') is True:
        hover_effects.append("transform: scale(1.05)")
    if config.get('hover_brightness') is True:
        hover_effects.append("filter: brightness(1.1)")
    if hover_effects:
        custom_css += f".item:hover {{ {'; '.join(hover_effects)}; }}\n"
    elif config.get('item_hover_effect') is False:
        custom_css += ".item:hover { background-color: inherit; box-shadow: none; transform: none; }\n"
    
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
            
            .modal-content {{
                background-color: #fefefe;
                padding: 20px;
                padding-bottom: 80px;
                border-radius: 5px;
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
                background-color: #4CAF50;
            }}
            
            .link-button:hover {{
                background-color: #45a049;
            }}
            
            /* Story template item scaling customization */
            .items {{
                display: flex;
                flex-wrap: wrap;
                margin: -1rem 0 0 -1rem;
                width: calc(100% + 1rem);
            }}
            
            .items > * {{
                margin: 1rem 0 0 1rem;
                width: calc(50% - 1rem);
            }}
            
            /* Small items - 4 per row on desktop for more compact iPhone-like grid */
            .items.small > * {{
                width: calc(25% - 1rem);
            }}
            
            /* Medium items - 3 per row */
            .items.medium > * {{
                width: calc(33.33333% - 1rem);
            }}
            
            /* Big items - 2 per row */
            .items.big > * {{
                width: calc(50% - 1rem);
            }}
            
            /* Item icon image scaling - smaller for iPhone app look */
            .image.icon {{
                display: flex;
                align-items: center;
                justify-content: center;
                margin-bottom: 0.8em;
            }}
            
            .image.icon img {{
                max-width: 3em;
                max-height: 3em;
                border-radius: 8px;
            }}
            
            /* Media queries for responsive scaling */
            @media screen and (max-width: 1024px) {{
                .items.small > * {{
                    width: calc(33.33333% - 1rem);
                }}
            }}
            
            @media screen and (max-width: 736px) {{
                .items > * {{
                    width: calc(50% - 1rem);
                }}
                
                .items.small > * {{
                    width: calc(50% - 1rem);
                }}
            }}
            
            @media screen and (max-width: 480px) {{
                .items.small > * {{
                    width: calc(50% - 1rem);
                }}
            }}
            
            /* iPhone-style app tile styling */
            .item {{
                border-radius: 22px;
                text-align: center;
                padding: 1.2em;
                transition: all 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94);
                background: linear-gradient(135deg, rgba(255, 255, 255, 0.1), rgba(255, 255, 255, 0.05));
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1), 0 1px 3px rgba(0, 0, 0, 0.2);
                display: block;
                text-decoration: none;
                color: white;
                position: relative;
                overflow: hidden;
                min-height: 120px;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
            }}
            
            .item h3 {{
                font-size: 0.9em;
                font-weight: 600;
                margin: 0.5em 0 0.2em 0;
                color: white;
                text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
            }}
            
            .item p {{
                font-size: 0.7em;
                margin: 0;
                opacity: 0.9;
                color: rgba(255, 255, 255, 0.9);
                text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
                line-height: 1.2;
            }}
            
            .item:hover {{
                transform: scale(1.05);
                box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15), 0 2px 5px rgba(0, 0, 0, 0.3);
            }}
            
            .item:active {{
                transform: scale(0.98);
            }}
            
            {custom_css}
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
    app = FastHTML(hdrs=hdrs, *args, **kwargs)

    @app.get("/{fname:path}.{ext:static}")
    def static(fname: str, ext: str):
        full_path = os.path.join(BASE_DIR, f"{fname}.{ext}")
        if os.path.exists(full_path):
            return FileResponse(full_path)

    return app, app.route


def footer():
    links = A("main-page", href="/")
    return Footer(Div(links, cls="inner"), cls="wrapper style1 align-center")

def get_java_version():
    try:
        result = subprocess.run(
            ['java', '-version'],
            capture_output=True,
            text=True
        )
        # Java version info is usually in stderr
        output = result.stderr.strip().split('\n')[0]
        # Extract the version number
        version = output.split('"')[1] if '"' in output else output.split()[2]
        return version
    except FileNotFoundError:
        return "Java is not installed or not in PATH."

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