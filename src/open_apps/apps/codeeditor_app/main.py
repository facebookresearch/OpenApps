"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fasthtml.common import *
import os
import shutil
from typing import Dict
import json
from starlette.responses import Response
from src.open_apps.apps.start_page.helper import create_logo_header
from src.open_apps.frontend import local_hdrs
from src.open_apps.theme import _as_plain, load_theme, theme_style
from src.open_apps.icons import Icon, icon

# Global variables
_base_hdrs_no_highlight = (
    # Pico + htmx from apps/assets/vendor, not jsdelivr (see frontend.py).
    *local_hdrs(),
    Script(src="https://cdn.tailwindcss.com"),
    Link(
        rel="stylesheet",
        href="https://cdn.jsdelivr.net/npm/daisyui@4.11.1/dist/full.min.css",
    ),
    Script("""
        function getStorageKey(folderPath) {
            return `folder_state_${folderPath}`;
        }
    """),
)
current_dir = None
list_of_modes, list_of_themes = [], []
_base_hdrs = _base_hdrs_no_highlight
opened_files = {}
logo_title_container = None

# Initialize app with default headers
app = FastHTML(hdrs=[*local_hdrs(), *_base_hdrs], cls="p-4", default_hdrs=False)

import yaml
import os

def create_file_system(base_path, file_system):
    """
    Creates a file system based on the provided dictionary structure.

    Args:
        base_path (str): The root directory where the file system will be created.
        file_system (dict): A dictionary representing the file system.
    """
    if file_system is None:
        return
    for item in file_system:
        name = item['name']
        full_path = os.path.join(base_path, name)
        if item['type'] == 'folder':
            os.makedirs(full_path, exist_ok=True)
            create_file_system(full_path, item['content'])  # Recursive call for subfolders
        elif item['type'] == 'file':
            with open(full_path, 'w') as f:
                f.write(item['content'])
        else:
            print(f"Invalid type: {item['type']}")


def update_db_from_hydra(config):
    file_system = config.code_editor.filesystem
    create_file_system(current_dir, file_system)

def set_environment(config):
    """Set environment variables for the code editor app"""
    # Create styles with environment variables
    global app, _base_hdrs, list_of_modes, list_of_themes, current_dir, logo_title_container
    if getattr(config.code_editor, 'no_css', False):
        app.hdrs = ()
        app.config = config
        current_dir = config.code_editor.database_path + '/'
        logo_title_container = create_logo_header(
            app_config=config.start_page.apps.codeeditor,
            base_url="/codeeditor",
            current_file_path=__file__
        )
        return
    list_of_modes = config.code_editor.list_of_modes
    list_of_themes = config.code_editor.list_of_themes
    current_dir = config.code_editor.database_path + '/'
    if os.path.exists(current_dir):
        # alert the user
        print("- Code editor folder already exists. This is undesired!!! Please double check.")
        print("######## ########")
        return
    os.makedirs(current_dir, exist_ok=True)
    update_db_from_hydra(config)
    print(f"- Code editor filesystem created under {current_dir}")
    _base_hdrs_with_highlight = (
        # Pico + htmx from apps/assets/vendor, not jsdelivr (see frontend.py).
        *local_hdrs(),
        Script(src="https://cdn.tailwindcss.com"),
        Link(rel="stylesheet", href="https://cdn.jsdelivr.net/npm/daisyui@4.11.1/dist/full.min.css"),
        Link(rel="stylesheet", href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/codemirror.min.css"),
    )
    for theme_name in list_of_themes:
        _base_hdrs_with_highlight += (
            Link(rel="stylesheet", href=f"https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/theme/{theme_name}.min.css"),
        )

    _base_hdrs_with_highlight += (
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/codemirror.min.js"),
    )
    for mode in list_of_modes:
        _base_hdrs_with_highlight += (
            Script(src=f"https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/mode/{mode}/{mode}.min.js"),
        )
    _base_hdrs_with_highlight += (Script("""
        function getStorageKey(folderPath) {
            return `folder_state_${folderPath}`;
        }
    """),)
    _base_hdrs = _base_hdrs_with_highlight if config.code_editor.highlight else _base_hdrs_no_highlight

    # Drop every third-party stylesheet/script. The inline blocks below carry
    # the design tokens, the layout utilities this app uses and base control
    # styling, so the page renders identically with no outbound network.
    #
    # Worth knowing which way the risk runs: on a host that already has no
    # egress those CDN tags fail silently and the inline CSS is what renders
    # anyway, so setting this makes the result deterministic rather than
    # network-dependent. On a host *with* egress it is a visible change,
    # because Tailwind and DaisyUI stop contributing.
    if getattr(config.code_editor, 'no_egress', False):
        _base_hdrs = (
            Script("""
                function getStorageKey(folderPath) {
                    return `folder_state_${folderPath}`;
                }
            """),
        )
    
    # Colours and typography come from the shared design tokens
    # (config/apps/theme/<name>.yaml), emitted as a :root block by
    # theme_style() and consumed here via var(). The legacy appearance keys
    # are kept as the fallback arm of each var() so the old appearance presets
    # still render, and so a theme that omits an editor-specific token
    # degrades to a sensible general one instead of to nothing.
    primary_color = getattr(config.code_editor, 'primary_button_color', '#4A90E2')
    secondary_color = getattr(config.code_editor, 'secondary_button_color', '#50E3C2')
    danger_color = getattr(config.code_editor, 'danger_button_color', '#D0021B')
    # grey 800 background as default
    textarea_background_color = getattr(config.code_editor, 'textarea_background_color', '#2d2d2d')
    # grey 900 background as default
    main_background_color = getattr(config.code_editor, 'main_background_color', '#1a202c')

    env_styles = Style(
        f"""
        :root {{
            --custom-font-size: var(--font-size-base, {config.code_editor.font_size}px);
            --custom-font-family: var(--font-family, {config.code_editor.font});
            --custom-font-color: var(--color-fg, {config.code_editor.fontcolor});
            --main-bg-color: var(--color-bg, {main_background_color});
            /* Editor-specific tokens degrade to general ones, so a theme that
               predates them (default, dark, mono, solarized) still works. */
            --sidebar-bg-color: var(--color-surface, {main_background_color});
            --border-color: var(--color-border, transparent);
            --accent-color: var(--color-accent, {primary_color});
            --row-hover-color: var(--color-row-hover, var(--color-surface, rgba(127,127,127,0.2)));
            --row-active-color: var(--color-row-active, var(--color-primary, {primary_color}));
            --editor-bg-color: var(--color-editor-bg, var(--color-bg, {textarea_background_color}));
            --editor-fg-color: var(--color-editor-fg, var(--color-fg, {textarea_background_color}));
        }}
        .main-content {{
            background-color: var(--main-bg-color);
        }}
        .styled-content {{
            font-size: var(--custom-font-size);
            font-family: var(--custom-font-family);
            color: var(--custom-font-color);
        }}
        textarea.styled-content {{
            font-family: var(--custom-font-family), monospace;
            color: var(--custom-font-color);
        }}
        textarea {{
            background-color: var(--editor-bg-color);
            color: var(--editor-fg-color);
            font-family: var(--custom-font-family);
        }}
        .btn-primary {{
            background-color: var(--color-primary, {primary_color}) !important;
            border-color: var(--color-primary, {primary_color}) !important;
            color: var(--color-on-primary, var(--custom-font-color)) !important;
            font-family: var(--custom-font-family); !important;
        }}
        .btn-secondary {{
            background-color: var(--color-neutral, {secondary_color}) !important;
            border-color: var(--color-neutral, {secondary_color}) !important;
            color: var(--color-btn-fg, var(--custom-font-color)) !important;
            font-family: var(--custom-font-family); !important;
        }}
        .btn-error {{
            background-color: var(--color-danger, {danger_color}) !important;
            border-color: var(--color-danger, {danger_color}) !important;
            color: var(--color-btn-fg, var(--custom-font-color)) !important;
            font-family: var(--custom-font-family); !important;
        }}

        /* ---- Editor chrome -------------------------------------------
           The sidebar and the editor pane both carry .main-content, so by
           default they render as one undifferentiated slab. Giving the file
           explorer its own surface and a divider is what makes the layout
           read as "editor" at a glance. Inert for presets that do not set
           sidebar_background_color, since it then equals the main colour. */
        .sidebar {{
            background-color: var(--sidebar-bg-color);
            border-right: 1px solid var(--border-color);
        }}
        /* File and folder rows: a visible hit area with a real hover and a
           clearly marked current file. Previously the only cue was Tailwind's
           hover:bg-gray-700, which vanishes if the CDN is unreachable. */
        .sidebar .file-row, .sidebar .folder-row {{
            border-radius: 3px;
            color: var(--custom-font-color);
        }}
        .sidebar .file-row:hover, .sidebar .folder-row:hover {{
            background-color: var(--row-hover-color);
        }}
        .sidebar .file-row.is-current {{
            background-color: var(--row-active-color);
        }}
        .sidebar a {{
            color: inherit;
            text-decoration: none;
            display: block;
            width: 100%;
        }}

        /* ---- File tree icons -------------------------------------------
           Inline SVG from open_apps.icons (see that module for why they are
           not an icon font). They inherit colour via currentColor, so a live
           theme swap recolours them with everything else. */
        .sidebar .row-link {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .sidebar .folder-row {{
            gap: 6px;
        }}
        .sidebar .row-icon {{
            display: inline-flex;
            align-items: center;
            flex: none;
            color: var(--color-muted, var(--custom-font-color));
        }}
        .sidebar .row-label {{
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        /* The name inside a folder row is a <button>; strip the control
           chrome so it reads as a tree label like the file links do. */
        .sidebar .folder-name {{
            background: none;
            border: none;
            padding: 0;
            margin: 0;
            font: inherit;
            color: inherit;
            text-align: left;
            cursor: pointer;
        }}
        /* One chevron for both states: rotated a quarter turn when open,
           rather than swapping one glyph for another. */
        .sidebar .folder-icon {{
            display: inline-flex;
            align-items: center;
            flex: none;
            color: var(--color-muted, var(--custom-font-color));
            transition: transform 0.12s ease;
        }}
        .sidebar .folder-row.is-expanded .folder-icon {{
            transform: rotate(90deg);
        }}
        /* Files sit one chevron-width in, so names line up with folder names
           instead of hanging left of them. */
        .sidebar .file-row {{
            padding-left: calc(1rem + 12px + 6px);
        }}
        #editor, .CodeMirror {{
            border: 1px solid var(--border-color);
            font-family: var(--custom-font-family), monospace;
            font-size: var(--custom-font-size);
        }}

        /* ---- Layout fallback ------------------------------------------
           Tailwind, DaisyUI and CodeMirror are all CDN fetches. On a host
           without egress none of them arrive and the page collapses to
           unstyled HTML — no sidebar, no widths, no spacing. These few rules
           reproduce only the layout utilities this app actually relies on,
           using Tailwind's own values, so the page is identical whether or
           not the CDN resolved. */
        .flex {{ display: flex; }}
        .items-center {{ align-items: center; }}
        .justify-between {{ justify-content: space-between; }}
        .justify-center {{ justify-content: center; }}
        .w-1\\/6 {{ width: 16.666667%; }}
        .w-5\\/6 {{ width: 83.333333%; }}
        .p-4 {{ padding: 1rem; }}
        .pl-2 {{ padding-left: 0.5rem; }}
        .pl-4 {{ padding-left: 1rem; }}
        .py-1 {{ padding-top: 0.25rem; padding-bottom: 0.25rem; }}
        .ml-2 {{ margin-left: 0.5rem; }}
        .mt-4 {{ margin-top: 1rem; }}
        .rounded-lg {{ border-radius: 0.5rem; }}
        .overflow-y-auto {{ overflow-y: auto; }}
        .cursor-pointer {{ cursor: pointer; }}
        body {{
            background-color: var(--main-bg-color);
            color: var(--custom-font-color);
            font-family: var(--custom-font-family);
        }}
        /* Base control styling, normally supplied by DaisyUI/pico over the
           network. Only shape and spacing here -- the colours are set by the
           .btn-* rules above from design tokens. */
        .btn {{
            display: inline-block;
            padding: 0.5rem 1rem;
            border-radius: var(--radius, 4px);
            border: 1px solid transparent;
            cursor: pointer;
            text-decoration: none;
            line-height: 1.2;
        }}
        select, .select {{
            background-color: var(--sidebar-bg-color);
            color: var(--custom-font-color);
            border: 1px solid var(--border-color);
            border-radius: var(--radius, 4px);
            padding: 0.25rem 0.5rem;
            font-family: var(--custom-font-family);
        }}
        textarea {{
            border-radius: var(--radius, 4px);
            padding: 0.5rem;
            width: 100%;
        }}
    """
    )
    app.config = config
    # Order matters: theme tokens define the :root custom properties that
    # env_styles consumes via var(), so they must come first. Both are inline
    # <style> blocks, so the whole look survives an unreachable CDN.
    app.hdrs = (*_base_hdrs, theme_style(config, "code_editor"), env_styles,
                theme_switcher_script(config))

    if config.code_editor.sort_feature:
        list_of_modes = sorted(list_of_modes)
        list_of_themes = sorted(list_of_themes)

    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.codeeditor,
        base_url="/codeeditor",
        current_file_path=__file__
    )

def live_theme_style() -> Style:
    """Re-emit the design tokens for the *current* config, per request.

    The token block in ``app.hdrs`` is built once in ``set_environment``, so it
    freezes whatever theme was configured at startup. Selecting a theme updates
    ``app.config.code_editor.theme`` (via /codeeditor/update_config) and
    repaints the live page, but the next navigation used to re-render from
    those frozen headers and snap the look back to the startup theme -- while
    the dropdown, which reads the config, still showed the chosen one.

    Emitting the block again per request fixes that: it appears after the
    header copy, so its :root wins, and it always reflects the current config.
    theme.py's own guidance is to call theme_style per request for exactly
    this reason.
    """
    return theme_style(app.config, "code_editor")


def theme_switcher_script(config) -> Script:
    """Embed every selectable theme's tokens so the selector can swap live.

    The tokens for all of ``list_of_themes`` are inlined as JSON, so changing
    theme is a set of ``style.setProperty`` calls on :root -- no page reload,
    no server round-trip, and nothing fetched from a CDN. That is what makes
    the dropdown a real-time surface rather than a form control.

    The server is still notified (``/codeeditor/update_config``) so the choice
    survives a reload and shows up in the config an eval records, but the
    visual change does not wait on that request.
    """
    names = list(getattr(config.code_editor, "list_of_themes", []) or [])
    palettes = {name: _as_plain(load_theme(name).get("tokens", {})) for name in names}
    return Script(f"""
        window.OPENAPPS_THEMES = {json.dumps(palettes)};
        window.applyTheme = function(name) {{
            var tokens = window.OPENAPPS_THEMES[name];
            if (!tokens) {{ return false; }}
            var root = document.documentElement;
            Object.keys(tokens).forEach(function(k) {{
                root.style.setProperty('--' + k, tokens[k]);
            }});
            return true;
        }};
    """)


def return_to_index():
    return A("Code Editor Index Page", href="/codeeditor", cls="btn btn-primary")


def return_to_home():
    return A("Return to List of Apps", href="/", cls="btn btn-primary")

def newfile_index(current_path):
    # files_root = os.path.join(current_dir, "files")
    files_root = current_dir
    # Use current_path directly as it now represents either a file or folder path
    target_dir = os.path.join(files_root, current_path if current_path else "")
    if os.path.isfile(target_dir):
        target_dir = os.path.dirname(target_dir)
    i = 1
    while os.path.exists(os.path.join(target_dir, f"Untitled-{i}")):
        i += 1
    return i

def editor_binding(options_js: str) -> str:
    """JS that binds the page-global ``editor`` used by Save / the selectors.

    With ``code_editor.highlight`` on, ``editor`` is a CodeMirror instance
    wrapping the ``#editor`` textarea. With it off there is no CodeMirror on
    the page (the CDN scripts are only added in the highlight branch of
    ``set_environment``), so bind a small shim over the plain textarea that
    exposes the handful of methods the page calls: ``getValue`` (Save),
    ``setValue``, ``setOption`` (mode/theme selectors), and ``setSize``.

    Without the shim the emitted JS was
    ``var editor = (document.getElementById('editor'), {...});`` — the comma
    operator, which bound ``editor`` to the *options object*. Every
    ``editor.getValue()`` then threw a TypeError, so the Save button silently
    did nothing: no POST, no reload, no error modal.

    ``setOption`` used to be a no-op, which is why the theme selector appeared
    to do nothing whenever highlight was off (its default). It now applies the
    local ``theme-*`` classes defined in ``set_environment``'s stylesheet, so
    theme swapping works without reaching CodeMirror's CDN.
    """
    if app.config.code_editor.highlight:
        return (
            "var editor = CodeMirror.fromTextArea(document.getElementById('editor'), "
            f"{options_js});"
        )
    return """
                    var editorTextarea = document.getElementById('editor');
                    var editor = {
                        getValue: function() { return editorTextarea.value; },
                        setValue: function(value) { editorTextarea.value = value; },
                        getOption: function() { return null; },
                        setOption: function(name, value) {
                            // Themes are design tokens now, applied to :root by
                            // window.applyTheme, so this works with no CodeMirror
                            // on the page and nothing fetched from a CDN.
                            if (name === 'theme' && window.applyTheme) {
                                window.applyTheme(value);
                            }
                        },
                        setSize: function() {},
                        refresh: function() {},
                        focus: function() { editorTextarea.focus(); }
                    };"""


def get_file_tree(path: str) -> Dict:
    """Recursively build a file tree structure"""
    # base_path = os.path.join(current_dir, "files")
    base_path = current_dir
    tree = {'type': 'folder', 'name': os.path.basename(path), 'children': []}
    try:
        for item in sorted(os.listdir(path)):
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                tree['children'].append(get_file_tree(item_path))
            else:
                # Remove the 'files/' prefix from the path
                relative_path = os.path.relpath(item_path, base_path)
                tree['children'].append({
                    'type': 'file',
                    'name': item,
                    'path': relative_path,
                    'content': open(item_path).read()
                })
    except OSError:
        pass
    return tree

def create_sidebar(current_path: str = None) -> Div:
    """Create the sidebar with file tree"""
    # files_root = os.path.join(current_dir, "files")
    files_root = current_dir
    file_tree = get_file_tree(files_root)

    def render_tree_item(item, path=''):
        if item['type'] == 'file':
            file_path = item['path']
            is_current = current_path == file_path
            return Div(
                # Colours come from the inline stylesheet, not Tailwind: the
                # CDN injects its rules at runtime and would otherwise win the
                # cascade, making the editor look different on a host with
                # egress than on one without.
                cls=f"file-row pl-4 py-1 cursor-pointer "
                    f"{'is-current' if is_current else ''}"
            )(
                A(
                    # Icon inside the link so the whole row, glyph included, is
                    # one hit target. The svg is aria-hidden, so it adds no
                    # AXTree node and the link's accessible name stays the
                    # bare filename.
                    Span(icon(Icon.FILE, size=14), cls="row-icon"),
                    Span(item['name'], cls="row-label"),
                    href=f"/codeeditor/{file_path}",
                    cls="row-link no-underline"
                )
            )
        else:
            folder_path = os.path.join(path, item['name'])
            # is_current = current_path and current_path.startswith(folder_path)
            is_current = (current_path and (
                current_path == folder_path or
                current_path.startswith(folder_path + '/')
            ))
            return Div(cls="folder-container")(
                # Merge span elements into a single clickable div
                Div(
                    cls=f"folder-row flex items-center pl-2 py-1 cursor-pointer {('is-current' if is_current else '')}",
                    **{
                        "data-path": folder_path,
                        "onclick": f"""
                            const container = this.closest('.folder-container');
                            const content = container.querySelector('.folder-content');
                            const isVisible = content.style.display === 'block';
                            content.style.display = isVisible ? 'none' : 'block';
                            // One chevron, rotated by CSS -- no glyph swap.
                            this.classList.toggle('is-expanded', !isVisible);

                            const storageKey = getStorageKey('{folder_path}');
                            localStorage.setItem(storageKey, (!isVisible).toString());
                            
                            window.location = '/codeeditor/{folder_path}';
                        """
                    }
                )(
                    # Chevron and folder glyph are spans, not buttons. They
                    # used to be Button(onclick="") wrappers, which put an
                    # extra *unnamed* button in the accessibility tree for
                    # every folder -- pure noise in the agent's observation.
                    # The name stays a Button so the folder keeps one named,
                    # clickable node to target.
                    Span(icon(Icon.CHEVRON, size=12), cls="folder-icon"),
                    Span(icon(Icon.FOLDER, size=14), cls="row-icon"),
                    Button(item['name'], cls="folder-name row-label", onclick=""),
                ),
                Div(
                    cls="folder-content ml-2",
                    style="display: none"
                )(
                    *[render_tree_item(child, folder_path) for child in item['children']]
                )
            )

    next_index = newfile_index(current_path)
    # files_root = os.path.join(current_dir, "files")
    files_root = current_dir
    # Handle relative paths for folder creation
    if current_path is None:
        folder_path = ""
    elif os.path.isfile(os.path.join(files_root, current_path)):
        folder_path = os.path.dirname(current_path)
    # it is also possible that current_path is not a folder path nor a file path
    # like, it points to a to-be-saved new file, but the file has not been saved yet
    elif not os.path.exists(os.path.join(files_root, current_path)):
        folder_path = os.path.dirname(current_path)
    else:
        folder_path = current_path
    return Div(
        cls="sidebar main-content w-1/6 p-4 rounded-lg overflow-y-auto",
        style="max-height: calc(100vh - 2rem)"
    )(
        Div(cls="mb-4")(
            Div(
                cls="flex justify-center gap-2",
                style="width: 100%"
            )(
                Button(
                    "New File",
                    cls="btn btn-sm btn-secondary",
                    onclick=f"""
                        const path = '{folder_path or ""}';
                        // Check if current path is already an unsaved Untitled file
                        if (path.includes('Untitled-') && !path.includes('/')) {{
                            showErrorModal('Please save the current new file first');
                            return;
                        }}
                        const newPath = path ? path + '/Untitled-{next_index}' : 'Untitled-{next_index}';
                        window.location = '/codeeditor/' + newPath;
                    """
                ),
                Button(
                    "New Folder",
                    cls="btn btn-sm btn-secondary",
                    onclick=f"""
                        const path = '{folder_path or ""}';
                        // Create a modal dynamically
                        const modal = document.createElement('div');
                        modal.className = 'modal modal-open'; // daisyUI classes to open the modal
                        modal.innerHTML = `
                            <div class="modal-box">
                                <h3 class="font-bold text-lg">Enter Folder Name</h3>
                                <input type="text" id="folderNameInput" class="input input-bordered w-full max-w-xs" placeholder="Folder Name">
                                <div class="modal-action">
                                    <button class="btn btn-primary" onclick="createFolder(this.closest('.modal'))">Create</button>
                                    <button class="btn" onclick="this.closest('.modal').remove()">Cancel</button>
                                </div>
                            </div>
                        `;
                        document.body.appendChild(modal);

                        // Function to handle folder creation
                        window.createFolder = (modalElement) => {{
                            const folderNameInput = modalElement.querySelector('#folderNameInput');
                            const folderName = folderNameInput.value;

                            if (folderName) {{
                                if (folderName.includes('Untitled-')) {{
                                    modalElement.remove();
                                    showErrorModal('Cannot create folders with "Untitled-" in the name. This prefix is reserved for new files.');
                                    return;
                                }}
                                const newPath = path ? path + '/' + folderName : folderName;
                                fetch('/codeeditor/create_folder/' + newPath, {{
                                    method: 'POST'
                                }})
                                .then(r => r.json())
                                .then(data => {{
                                    modalElement.remove(); // Close the modal
                                    if (data.success) window.location.reload();
                                    else {{
                                        showErrorModal('Failed to create folder: ' + data.error);
                                    }};
                                }});
                            }} else {{
                                modalElement.remove();
                            }}
                        }};
                    """
                )
            )
        ),
        Div(cls="text-white")(
            *[render_tree_item(child) for child in file_tree['children']]
        ),
        Script("""
            document.addEventListener('DOMContentLoaded', () => {
                document.querySelectorAll('.folder-container').forEach(container => {
                    const folderHeader = container.querySelector('.folder-row');
                    const content = container.querySelector('.folder-content');
                    const folderPath = folderHeader.getAttribute('data-path');

                    // Set initial state from localStorage, default to collapsed (false)
                    const storageKey = getStorageKey(folderPath);
                    const isExpanded = localStorage.getItem(storageKey) === 'true';

                    // Always start collapsed unless explicitly set to expanded in localStorage
                    content.style.display = isExpanded ? 'block' : 'none';
                    // Rotate the single chevron rather than swapping glyphs.
                    folderHeader.classList.toggle('is-expanded', isExpanded);
                });
            });
            function showErrorModal(message) {
                const modal = document.createElement('div');
                modal.className = 'modal modal-open';
                modal.innerHTML = `
                    <div class="modal-box">
                        <h3 class="font-bold text-lg text-error">Error</h3>
                        <p class="py-4">${message}</p>
                        <div class="modal-action">
                            <button class="btn" onclick="this.closest('.modal').remove()">Close</button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modal);
            }
        """)
    )

@app.get("/codeeditor/")
def index():
    side_bar = create_sidebar()
    # files_root = f"{current_dir}/files/"
    files_root = current_dir
    file_tree = get_file_tree(files_root)
    editor_options = f"""{{
                        mode: '{app.config.code_editor.mode}',
                        theme: '{app.config.code_editor.theme}',
                        lineNumbers: true,
                        indentUnit: 4,
                        tabSize: 4,
                        indentWithTabs: false,
                        smartIndent: true,
                        lineWrapping: true,
                        extraKeys: {{
                            "Tab": function(cm) {{
                                if (cm.somethingSelected()) {{
                                    cm.indentSelection("add");
                                }} else {{
                                    cm.replaceSelection("    ", "end", "+input");
                                }}
                            }},
                            "Shift-Tab": function(cm) {{
                                cm.indentSelection("subtract");
                            }}
                        }}
                    }}"""
    # by default, the main screen should display an empty code editor
    main_screen = Div(cls="w-5/6")(
        Div(cls="main-content p-4 rounded-lg styled-content")(
            Div(cls="flex justify-between items-center")(
                H2(f"No file selected", cls="text-white"),
                Div(cls="flex space-x-4")(
                    Div(cls="flex items-center")(
                        Label("Language: ", cls="text-white mr-2"),
                        Select(
                            id="mode-selector",
                            cls="bg-gray-800 text-white p-2 rounded",
                            onchange="""
                                editor.setOption('mode', this.value);
                                fetch('/codeeditor/update_config', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({
                                        type: 'mode',
                                        value: this.value
                                    })
                                })
                                .then(r => r.json())
                                .then(data => {
                                    if (!data.success) {
                                        showErrorModal('Failed to update mode: ' + data.error);
                                    }
                                });
                            """
                        )(
                            *[Option(mode, value=mode, selected=(mode == app.config.code_editor.mode)) for mode in list_of_modes]
                        ),
                    ),
                    Div(cls="flex items-center")(
                        Label("Theme: ", cls="text-white mr-2"),
                        Select(
                            id="theme-selector",
                            cls="bg-gray-800 text-white p-2 rounded",
                            onchange="""
                                window.applyTheme(this.value);
                                editor.setOption('theme', this.value);
                                fetch('/codeeditor/update_config', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({
                                        type: 'theme',
                                        value: this.value
                                    })
                                })
                            .then(r => r.json())
                            .then(data => {
                                if (!data.success) {
                                    showErrorModal('Failed to update theme: ' + data.error);
                                }
                            });
                            """
                        )(
                            *[Option(theme, value=theme, selected=(theme == app.config.code_editor.theme)) for theme in list_of_themes]
                        ),
                    ),
                ),
            ),
            Div(cls="mt-4")(
                Textarea(
                    app.config.code_editor.welcome_message or "Welcome! Happy coding everyday!",
                    id="editor",
                    cls="w-full h-[calc(100vh-12rem)] p-4 rounded-lg styled-content",
                    disabled="disabled"
                ),
                Script(f"""
                    {editor_binding(editor_options)}
                    {f'editor.setSize("100%", "calc(100vh - 12rem)");' if app.config.code_editor.highlight else ''}
                """),
            ),
            # make sure the buttons are not too close to each other
            Div(cls="mt-4 flex space-x-4")(
                return_to_index(),
                return_to_home(),
            ),
        ),
    )
    page = Div(cls="flex space-x-2")(side_bar, main_screen)
    return Div(live_theme_style(), logo_title_container, page)


@app.get("/codeeditor/{path:path}")
def get(path: str):
    # Check if the path is a directory
    # full_path = os.path.join(current_dir, "files", path)
    full_path = os.path.join(current_dir, path)
    if os.path.isdir(full_path):
        # If it's a directory, show the folder view
        return get_folder(path)
    else:
        # If it's a file, show the file editor
        return get_file(path)

def get_folder(folder: str):
    """Handle folder view with empty editor"""
    side_bar = create_sidebar(folder)
    editor_options = f"""{{
                        mode: '{app.config.code_editor.mode}',
                        theme: '{app.config.code_editor.theme}',
                        lineNumbers: true,
                        readOnly: true
                    }}"""
    main_screen = Div(cls="w-5/6")(
        Div(cls="main-content  p-4 rounded-lg styled-content")(
            Div(cls="flex justify-between items-center")(
                H2(f"Folder: {folder}", cls="text-white"),
                Div(cls="flex space-x-4")(
                    Div(cls="flex items-center")(
                        Label("Language: ", cls="text-white mr-2"),
                        Select(
                            id="mode-selector",
                            cls="bg-gray-800 text-white p-2 rounded",
                            onchange="""
                                editor.setOption('mode', this.value);
                                fetch('/codeeditor/update_config', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({
                                        type: 'mode',
                                        value: this.value
                                    })
                                });
                            """
                        )(
                            *[Option(mode, value=mode, selected=(mode == app.config.code_editor.mode)) for mode in list_of_modes]
                        ),
                    ),
                    Div(cls="flex items-center")(
                        Label("Theme: ", cls="text-white mr-2"),
                        Select(
                            id="theme-selector",
                            cls="bg-gray-800 text-white p-2 rounded",
                            onchange="""
                                window.applyTheme(this.value);
                                editor.setOption('theme', this.value);
                                fetch('/codeeditor/update_config', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({
                                        type: 'theme',
                                        value: this.value
                                    })
                                });
                            """
                        )(
                            *[Option(theme, value=theme, selected=(theme == app.config.code_editor.theme)) for theme in list_of_themes]
                        ),
                    ),
                ),
            ),
            Div(cls="mt-4")(
                Textarea(
                    "Select a file to edit or create a new one.",
                    id="editor",
                    cls="w-full h-[calc(100vh-12rem)] p-4 rounded-lg styled-content",
                    disabled="disabled"
                ),
                Script(f"""
                    {editor_binding(editor_options)}
                    {f'editor.setSize("100%", "calc(100vh - 12rem)");' if app.config.code_editor.highlight else ''}
                """),
            ),
            Div(cls="mt-4 flex space-x-4")(
                return_to_index(),
                return_to_home(),
                Button(
                    "Delete Folder",
                    cls="btn btn-error", # Using error class for danger/delete actions
                    onclick=f"""
                        fetch('/codeeditor/delete/{folder}', {{
                            method: 'POST'
                        }})
                        .then(r => r.json())
                        .then(data => {{
                            if (data.success) {{
                                window.location = '/codeeditor/';
                            }} else {{
                                showErrorModal('Failed to delete folder: ' + data.error);
                            }}
                        }});
                    """
                ),
            ),
        ),
    )
    page = Div(cls="flex space-x-2")(side_bar, main_screen)
    return Div(live_theme_style(), logo_title_container, page)

def get_file(file: str):
    side_bar = create_sidebar(file)
    # read the content of the file and display it in the editor
    try:
        # file_path = os.path.join(current_dir, "files", file)
        file_path = os.path.join(current_dir, file)
        with open(file_path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    # files_root = f"{current_dir}/files/"
    files_root = current_dir
    file_tree = get_file_tree(files_root)
    editor_options = f"""{{
                        mode: '{app.config.code_editor.mode}',
                        theme: '{app.config.code_editor.theme}',
                        lineNumbers: true,
                        indentUnit: 4,
                        tabSize: 4,
                        indentWithTabs: false,
                        smartIndent: true,
                        lineWrapping: true,
                        screenReaderLabel: 'Code editor',
                        inputStyle: 'contenteditable',
                        role: 'textbox',
                        'aria-multiline': true,
                        'aria-atomic': true,
                        'aria-live': 'off',
                        announceMultiline: true,
                        extraKeys: {{
                            "Tab": function(cm) {{
                                if (cm.somethingSelected()) {{
                                    cm.indentSelection("add");
                                }} else {{
                                    cm.replaceSelection("    ", "end", "+input");
                                }}
                            }},
                            "Shift-Tab": function(cm) {{
                                cm.indentSelection("subtract");
                            }}
                        }}
                    }}"""
    # same layout and sidebar as the main screen
    side_bar = create_sidebar(file)
    tab_bar = Div(cls="flex overflow-x-auto bg-gray-800 border-b border-gray-700")(
        Div(
            id="tab-container",
            cls="flex"
        )(
            Script("""
                // Use sessionStorage to track if a session is active
                const SESSION_KEY = 'editor_session_active';
                const TABS_KEY = 'opened_files';

                // Check if this is a fresh session
                if (!sessionStorage.getItem(SESSION_KEY)) {
                    // Clear localStorage tabs when starting a new session
                    localStorage.clear();
                    // Mark session as active
                    sessionStorage.setItem(SESSION_KEY, 'true');
                }

                // Store opened files in localStorage
                function updateOpenedFiles(files) {
                    localStorage.setItem(TABS_KEY, JSON.stringify(files));
                }

                // Get opened files from localStorage
                function getOpenedFiles() {
                    const files = localStorage.getItem(TABS_KEY);
                    return files ? JSON.parse(files) : [];
                }

                // Update tab name when file is renamed
                function updateTabOnRename(oldPath, newPath) {
                    let openedFiles = getOpenedFiles();
                    openedFiles = openedFiles.map(file => file === oldPath ? newPath : file);
                    updateOpenedFiles(openedFiles);
                }

                // Initialize opened files
                let openedFiles = getOpenedFiles();
                const currentFile = '""" + file + """';
                
                if (!openedFiles.includes(currentFile)) {
                    openedFiles.push(currentFile);
                    updateOpenedFiles(openedFiles);
                }

                // Render tabs
                function renderTabs() {
                    const container = document.getElementById('tab-container');
                    container.innerHTML = '';
                    
                    openedFiles.forEach(file => {
                        const tab = document.createElement('div');
                        tab.className = `flex items-center px-4 py-2 cursor-pointer ${
                            file === currentFile ? 'bg-gray-700 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                        }`;
                        
                        const fileName = document.createElement('span');
                        fileName.textContent = file.split('/').pop();
                        fileName.onclick = () => {
                            if (file !== currentFile) {
                                window.location = '/codeeditor/' + file;
                            }
                        };
                        
                        const closeBtn = document.createElement('button');
                        closeBtn.className = 'ml-2 text-gray-500 hover:text-white focus:outline-none focus:ring-0 focus:ring-offset-0 focus:border-0 focus-visible:outline-none focus-visible:ring-0';
                        closeBtn.innerHTML = '×';
                        closeBtn.onclick = (e) => {
                            e.stopPropagation();
                            openedFiles = openedFiles.filter(f => f !== file);
                            updateOpenedFiles(openedFiles);
                            
                            if (file === currentFile) {
                                // Navigate to the next available tab or index
                                if (openedFiles.length > 0) {
                                    window.location = '/codeeditor/' + openedFiles[0];
                                } else {
                                    window.location = '/codeeditor/';
                                }
                            } else {
                                renderTabs();
                            }
                        };
                        
                        tab.appendChild(fileName);
                        tab.appendChild(closeBtn);
                        container.appendChild(tab);
                    });
                }

                // Initial render
                renderTabs();
            """)
        )
    )
    main_screen = Div(cls="w-5/6 flex flex-col")(
        tab_bar,
        Div(cls="flex-grow main-content p-4 rounded-lg styled-content")(
            Div(cls="flex justify-between items-center")(
                Div(cls="text-white text-2xl group")(
                    Div(
                        cls="flex items-center",
                        ondblclick="""
                            this.nextElementSibling.classList.remove('hidden');
                            this.classList.add('hidden');
                            const input = this.nextElementSibling.querySelector('input');
                            input.focus();
                            input.select();
                        """,
                        role="button",
                        tabindex="0",
                        **{'aria-label': f"File name: {file}. Double-click to rename."}
                    )(
                        file,
                        Span(
                            cls="ml-2 text-sm text-gray-400 opacity-0 group-hover:opacity-100"
                        )("Double-click to rename"),
                    ),
                    Div(cls="hidden")(
                        Input(
                            type="text",
                            value=file,
                            cls="bg-gray-800 text-white px-2 py-1 rounded w-full",
                            onblur=f"""
                                const newName = this.value;
                                if (newName !== '{file}') {{
                                    // First save the current content
                                    const content = document.querySelector('textarea').value;
                                    fetch('/codeeditor/save/{file}', {{
                                        method: 'POST',
                                        headers: {{'Content-Type': 'application/json'}},
                                        body: JSON.stringify({{content: content}})
                                    }})
                                    .then(r => r.json())
                                    .then(data => {{
                                        if (data.success) {{
                                            // After successful save, proceed with rename
                                            return fetch('/codeeditor/rename/{file}?new_file=' + encodeURIComponent(newName), {{method: 'POST'}});
                                        }} else {{
                                            showErrorModal('Failed to save file: ' + data.error);
                                        }}
                                    }})
                                    .then(r => r.json())
                                    .then(data => {{
                                        if (data.success) {{
                                            // Update tab name before navigation
                                            updateTabOnRename('{file}', newName);                                            
                                            window.location = '/codeeditor/' + newName;
                                        }} else {{
                                            showErrorModal('Failed to rename: ' + data.error);
                                        }}
                                    }})
                                    .catch(error => showErrorModal(error.message));
                                }}
                                this.parentElement.classList.add('hidden');
                                this.parentElement.previousElementSibling.classList.remove('hidden');
                            """,
                            onkeydown="if(event.key==='Enter')this.blur();if(event.key==='Escape'){this.value='"
                            + file
                            + "';this.blur();}",
                        ),
                    ),
                ),
                Div(cls="flex space-x-4")(
                    Div(cls="flex items-center")(
                        Label("Language: ", cls="text-white mr-2"),
                        Select(
                            id="mode-selector",
                            cls="bg-gray-800 text-white p-2 rounded",
                            onchange="""
                                editor.setOption('mode', this.value);
                                fetch('/codeeditor/update_config', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({
                                        type: 'mode',
                                        value: this.value
                                    })
                                });
                            """
                        )(
                            *[Option(mode, value=mode, selected=(mode == app.config.code_editor.mode)) for mode in list_of_modes]
                        ),
                    ),
                    Div(cls="flex items-center")(
                        Label("Theme: ", cls="text-white mr-2"),
                        Select(
                            id="theme-selector",
                            cls="bg-gray-800 text-white p-2 rounded",
                            onchange="""
                                window.applyTheme(this.value);
                                editor.setOption('theme', this.value);
                                fetch('/codeeditor/update_config', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({
                                        type: 'theme',
                                        value: this.value
                                    })
                                });
                            """
                        )(
                            *[Option(theme, value=theme, selected=(theme == app.config.code_editor.theme)) for theme in list_of_themes]
                        ),
                    ),
                ),
            ),
            Div(cls="mt-4")(
                Textarea(
                    content,  # or "" for index() function
                    id="editor",
                    cls="w-full h-[calc(100vh-12rem)] p-4 rounded-lg styled-content",
                    role="textbox",
                    spellcheck="false",
                    wrap="off",
                    **{
                        "aria-label": f"Code editor - {file}",
                        "aria-multiline": "true",
                        "aria-describedby": "editor-description",
                        "aria-atomic": "true",
                        "aria-live": "off"
                    }
                ),
                Div(
                    id="editor-description",
                    cls="sr-only"
                )(f"Code editor for editing {file}"),
                Script(f"""
                    {editor_binding(editor_options)}
                    {f'editor.setSize("100%", "calc(100vh - 12rem)");' if app.config.code_editor.highlight else ''}
                """),
            ),
            # refresh the page after saving the file
            # return to the index page after deleting the file
            Div(cls="mt-4 flex space-x-4")(
                Button(
                    "Save",
                    cls="btn btn-primary",
                    onclick=f"""
                        const content = editor.getValue();
                        fetch('/codeeditor/save/{file}', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            body: JSON.stringify({{content: content}})
                        }})
                        .then(r => r.json())
                        .then(data => {{
                            if (data.success) {{
                                window.location.reload();
                            }} else {{
                                showErrorModal('Failed to save file: ' + data.error);
                            }}
                        }});
                    """,
                ),
                Button(
                    "Delete",
                    cls="btn btn-error",
                    onclick=f"""
                        fetch('/codeeditor/delete/{file}', {{method: 'POST'}})
                            .then(r => r.json())
                            .then(data => {{
                                if (data.success) {{
                                    // Remove the deleted file from openedFiles array
                                    openedFiles = openedFiles.filter(f => f !== '{file}');
                                    updateOpenedFiles(openedFiles);
                                    // Navigate to the index page after deleting
                                    window.location = '/codeeditor/';
                                }}
                                else showErrorModal('Failed to delete file: ' + data.error);
                            }});
                    """,
                ),
                return_to_index(),
                return_to_home(),
            ),
        ),
    )
    page = Div(cls="flex space-x-2")(side_bar, main_screen)
    return Div(live_theme_style(), logo_title_container, page)

@app.post("/codeeditor/create_folder/{folder:path}")
def create_folder(folder: str):
    try:
        # Prevent creating folders with "Untitled-" prefix
        folder_name = os.path.basename(folder)
        # folder_path = os.path.join(current_dir, "files", folder)
        folder_path = os.path.join(current_dir, folder)
        # Be cautious! Path traversal attack prevention
        if not os.path.abspath(folder_path).startswith(os.path.abspath(current_dir)):
            return {"success": False, "error": "Invalid folder path."}
        # check whether the name has been occupied by another folder or file
        if os.path.exists(folder_path):
            return {"success": False, "error": "Name already occupied."}

        os.makedirs(folder_path, exist_ok=True)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/codeeditor/save/{file:path}")
def save_file(file: str, content: dict):
    try:
        # file_path = os.path.join(current_dir, "files", file)
        file_path = os.path.join(current_dir, file)
        # Be cautious! Path traversal attack prevention
        if not os.path.abspath(file_path).startswith(os.path.abspath(current_dir)):
            return {"success": False, "error": "Invalid file path."}
        # check if the parent directory exists: if not, create it
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(content["content"])
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/codeeditor/rename/{old_file:path}")
def rename_file(old_file: str, new_file: str):
    try:
        # old_path = os.path.join(current_dir, "files", old_file)
        # new_path = os.path.join(current_dir, "files", new_file)
        # Be cautious! Path traversal attack prevention
        old_path = os.path.join(current_dir, old_file)
        new_path = os.path.join(current_dir, new_file)
        if not os.path.abspath(old_path).startswith(os.path.abspath(current_dir)):
            return {"success": False, "error": "Invalid file path."}
        if not os.path.abspath(new_path).startswith(os.path.abspath(current_dir)):
            return {"success": False, "error": "Invalid file path."}
        if os.path.exists(new_path):
            return {"success": False, "error": "File already exists."}
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        shutil.move(old_path, new_path)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/codeeditor/delete/{file:path}")
def delete_file(file: str):
    try:
        # path = os.path.join(current_dir, "files", file)
        path = os.path.join(current_dir, file)
        # Be cautious! Path traversal attack prevention
        if not os.path.abspath(path).startswith(os.path.abspath(current_dir)):
            return {"success": False, "error": "Invalid file path."}
        if os.path.isdir(path):
            shutil.rmtree(path)
            # make sure the file exists
        elif os.path.exists(path):
            os.remove(path)
        else:
            return {"success": False, "error": "File not found. Are you trying to delete an unsaved file?"}
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/codeeditor/update_config")
async def update_config(request):
    try:
        data = await request.json()
        if data["type"] == "mode":
            app.config.code_editor.mode = data["value"]
        elif data["type"] == "theme":
            app.config.code_editor.theme = data["value"]
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/codeeditor_all")
def get_all():
    """Used for rewards"""
    # return the file tree of the code editor
    files_root = current_dir
    file_tree = get_file_tree(files_root)
    # convert the file tree to a JSON object
    file_tree_json = json.dumps(file_tree, indent=4)
    # return the file tree as a JSON object
    return Response(content=file_tree_json, headers={"Content-Type": "application/json"})

def get_codeeditor_routes():
    return app.routes

if __name__ == "__main__":
    app.routes = get_codeeditor_routes()
    serve()