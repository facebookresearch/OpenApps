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

# Global variables
_base_hdrs_no_highlight = (
    picolink,
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
app = FastHTML(hdrs=_base_hdrs, cls="p-4")

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
        picolink,
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
            --custom-font-size: {config.code_editor.font_size}px;
            --custom-font-family: {config.code_editor.font};
            --custom-font-color: {config.code_editor.fontcolor};
            --main-bg-color: {main_background_color};
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
            background-color: {textarea_background_color};
            color: var(--custom-font-color);
            font-family: var(--custom-font-family);
        }}
        .btn-primary {{
            background-color: {primary_color} !important;
            border-color: {primary_color} !important;
            color: var(--custom-font-color) !important;
            font-family: var(--custom-font-family); !important;
        }}
        .btn-secondary {{
            background-color: {secondary_color} !important;
            border-color: {secondary_color} !important;
            color: var(--custom-font-color) !important;
            font-family: var(--custom-font-family); !important;
        }}
        .btn-error {{
            background-color: {danger_color} !important;
            border-color: {danger_color} !important;
            color: var(--custom-font-color) !important;
            font-family: var(--custom-font-family); !important;
        }}
    """
    )
    app.config = config
    # Update app headers by extending existing ones
    app.hdrs = (*_base_hdrs, env_styles)

    if config.code_editor.sort_feature:
        list_of_modes = sorted(list_of_modes)
        list_of_themes = sorted(list_of_themes)

    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.codeeditor,
        base_url="/codeeditor",
        current_file_path=__file__
    )

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
                cls=f"pl-4 py-1 hover:bg-gray-700 cursor-pointer {'bg-blue-800' if is_current else ''}"
            )(
                A(
                    item['name'],
                    href=f"/codeeditor/{file_path}",
                    cls="text-white hover:text-white no-underline"
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
                    cls=f"flex items-center pl-2 py-1 hover:bg-gray-700 cursor-pointer {('bg-blue-800' if is_current else '')}",
                    **{
                        "data-path": folder_path,
                        "onclick": f"""
                            const container = this.closest('.folder-container');
                            const content = container.querySelector('.folder-content');
                            const icon = this.querySelector('.folder-icon');
                            const isVisible = content.style.display === 'block';
                            content.style.display = isVisible ? 'none' : 'block';
                            icon.textContent = isVisible ? '▶' : '▼';
                            
                            const storageKey = getStorageKey('{folder_path}');
                            localStorage.setItem(storageKey, (!isVisible).toString());
                            
                            window.location = '/codeeditor/{folder_path}';
                        """
                    }
                )(
                    Button(cls="folder-icon mr-1", onclick="")(Span("▶")),
                    Button(item['name'], cls="folder-name text-white", onclick=""),
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
        cls="main-content w-1/6 p-4 rounded-lg overflow-y-auto",
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
                    const folderHeader = container.querySelector('.flex');
                    const icon = container.querySelector('.folder-icon');
                    const content = container.querySelector('.folder-content');
                    const folderPath = folderHeader.getAttribute('data-path');
                    
                    // Set initial state from localStorage, default to collapsed (false)
                    const storageKey = getStorageKey(folderPath);
                    const isExpanded = localStorage.getItem(storageKey) === 'true';
                    
                    // Always start collapsed unless explicitly set to expanded in localStorage
                    content.style.display = isExpanded ? 'block' : 'none';
                    icon.textContent = isExpanded ? '▼' : '▶';
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
                    var editor = {'CodeMirror.fromTextArea' if app.config.code_editor.highlight else ''} (document.getElementById('editor'), {{
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
                    }});
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
    return Div(logo_title_container, page)


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
                    var editor = {'CodeMirror.fromTextArea' if app.config.code_editor.highlight else ''} (document.getElementById('editor'), {{
                        mode: '{app.config.code_editor.mode}',
                        theme: '{app.config.code_editor.theme}',
                        lineNumbers: true,
                        readOnly: true
                    }});
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
    return Div(logo_title_container, page)

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
                    var editor = {'CodeMirror.fromTextArea' if app.config.code_editor.highlight else ''} (document.getElementById('editor'), {{
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
                    }});
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
    return Div(logo_title_container, page)

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