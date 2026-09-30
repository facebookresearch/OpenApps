"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fasthtml.common import *
from dataclasses import dataclass
import json
from typing import List
from src.open_apps.apps.start_page.helper import create_logo_header
from src.open_apps.frontend import local_hdrs
from src.open_apps.theme import theme_style


@dataclass
class Todo:
    id: int
    title: str
    done: bool


app, rt = fast_app(default_hdrs=False, hdrs=local_hdrs())
logo_title_container = None

# Static, theme-agnostic component styles. All colors/fonts are design tokens
# resolved per-request via `theme_style()` (see the `:root` block it emits), so
# this block never needs rebuilding when the theme or app config changes.
styles = Style("""
    /* Page frame. Pico paints <html> itself, so without this the canvas
       below short content stays white under a dark theme. */
    html, body {
        background-color: var(--color-bg);
    }
    body {
        font-family: var(--font-family);
        font-size: var(--font-size-base);
        color: var(--color-fg);
    }
    /* Bridge Pico's own variables onto the theme tokens inside the app, so
       headings, form fields and focus rings stop using Pico's fixed palette. */
    .todo-page {
        --pico-color: var(--color-fg);
        --pico-muted-color: var(--color-muted);
        --pico-h1-color: var(--color-fg);
        --pico-h2-color: var(--color-fg);
        --pico-h3-color: var(--color-fg);
        --pico-font-family: var(--font-family);
        --pico-border-radius: var(--radius);
        --pico-primary: var(--color-primary);
        --pico-primary-background: var(--color-primary);
        --pico-primary-border: var(--color-primary);
        --pico-primary-focus: color-mix(in srgb, var(--color-primary) 35%, transparent);
        --pico-form-element-background-color: var(--color-bg);
        --pico-form-element-selected-background-color: var(--color-bg);
        --pico-form-element-border-color: var(--color-border);
        --pico-form-element-color: var(--color-fg);
        --pico-form-element-placeholder-color: var(--color-muted);
        --pico-form-element-active-background-color: var(--color-bg);
        --pico-form-element-active-border-color: var(--color-primary);
        --pico-form-element-focus-color: color-mix(in srgb, var(--color-primary) 35%, transparent);
        --pico-card-background-color: var(--color-bg);
        --pico-card-sectioning-background-color: var(--color-surface);
        color: var(--color-fg);
        padding: calc(var(--space) * 3) calc(var(--space) * 3) calc(var(--space) * 4);
    }
    /* The list reads as a single centred column (Todoist / Things); the
       board uses the full width and scrolls sideways (Trello / Projects). */
    .todo-page--list {
        max-width: 760px;
        margin: 0 auto;
    }
    .todo-page h1, .kanban-column-title {
        font-family: var(--font-heading);
    }
    .todo-page input:not([type=checkbox]) {
        font-family: inherit;
        font-size: var(--font-size-base);
    }
    a {
        color: var(--color-fg);
        text-decoration: none;
    }

    /* ---- Buttons ------------------------------------------------------ */
    /* Primary: the add action, filled with the theme's primary colour. */
    .add-btn {
        background-color: var(--color-primary);
        border: 1px solid var(--color-primary);
        color: var(--color-on-primary);
        font-weight: 600;
    }
    /* Mixed toward the foreground rather than `--color-primary-hover`: the
       dark theme pairs a near-black hover fill with black on-primary text. */
    .add-btn:hover, .add-btn:focus-visible {
        background-color: color-mix(in srgb, var(--color-primary) 85%, var(--color-fg));
        border-color: color-mix(in srgb, var(--color-primary) 85%, var(--color-fg));
        color: var(--color-on-primary);
    }
    /* Secondary row actions are quiet outlines at rest and take their
       semantic fill (neutral for edit, danger for remove) on hover/focus. */
    .todo-btn {
        width: auto;
        margin: 0;
        padding: calc(var(--space) * 0.375) calc(var(--space) * 1.25);
        font-size: var(--font-size-sm);
        font-weight: 500;
        line-height: 1.25;
        border-radius: var(--radius);
        background-color: transparent;
        border: 1px solid var(--color-border);
        color: var(--color-fg);
        box-shadow: none;
        cursor: pointer;
    }
    .todo-btn:focus-visible {
        outline: 2px solid var(--color-primary);
        outline-offset: 1px;
        box-shadow: none;
    }
    .todo-btn:hover, .todo-btn:focus-visible {
        background-color: var(--color-neutral);
        border-color: var(--color-neutral);
        color: var(--color-btn-fg);
    }
    .todo-btn.remove-btn:hover, .todo-btn.remove-btn:focus-visible {
        background-color: var(--color-danger);
        border-color: var(--color-danger);
        color: var(--color-btn-fg);
    }
    .todo-btn.save-btn {
        background-color: var(--color-accent);
        border-color: var(--color-accent);
        color: var(--color-btn-fg);
        font-weight: 600;
    }
    .todo-btn.save-btn:hover, .todo-btn.save-btn:focus-visible {
        background-color: color-mix(in srgb, var(--color-accent) 85%, var(--color-fg));
    }

    /* ---- List layout -------------------------------------------------- */
    .todo-general {
        padding: 0;
        margin-bottom: calc(var(--space) * 2);
        background-color: var(--color-bg);
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        box-shadow: none;
    }
    .todo-general > header {
        margin: 0;
        padding: calc(var(--space) * 1.5) calc(var(--space) * 2);
        background-color: var(--color-surface);
        border-bottom: 1px solid var(--color-border);
        border-radius: var(--radius) var(--radius) 0 0;
    }
    .todo-general > header form, .todo-general > header fieldset {
        margin: 0;
    }
    .todo-general > footer {
        margin: 0;
        padding: calc(var(--space) * 1.5) calc(var(--space) * 2);
        background-color: var(--color-surface);
        border-top: 1px solid var(--color-border);
        border-radius: 0 0 var(--radius) var(--radius);
    }
    /* The footer only hosts the inline edit form; hide the empty strip. */
    .todo-general > footer:not(:has(#current-todo > *)) {
        display: none;
    }
    .todo-general > footer form, .todo-general > footer fieldset {
        margin-bottom: calc(var(--space) * 0.75);
    }
    #todo-list {
        margin: 0;
        padding: 0;
    }
    #todo-list li {
        list-style: none;
        margin: 0;
    }
    /* Each row is two lines -- checkbox + title, then Edit/Remove beneath,
       indented to the title. The actions stay *beneath* the title (not at
       the row's right edge) and the checkbox stays square: both are what
       the UI-question set built from the default screenshot asserts
       ("Edit and Remove beneath each item", "Checkbox", not "Radio button").

       The 80px min-height is load-bearing too. Those questions count over
       the first 12 rows (MAX_VISIBLE_TODOS) of a 1440x1100 screenshot; at
       this pitch row 12's title ends ~30px above the fold and row 13 starts
       ~25px below it, as in the original 82px layout. */
    .todo-row {
        display: flex;
        flex-direction: column;
        justify-content: center;
        gap: calc(var(--space) * 0.75);
        min-height: 80px;
        padding: var(--space) calc(var(--space) * 2);
        border-bottom: 1px solid var(--color-border);
    }
    .todo-row:last-child {
        border-bottom: 0;
    }
    .todo-row:hover, .todo-row:focus-within {
        background-color: color-mix(in srgb, var(--color-surface) 70%, transparent);
    }
    /* The inline edit form swaps a fresh row *into* the old one (innerHTML);
       flatten the nested row so it doesn't render double padding. */
    .todo-row > .todo-row {
        min-height: 0;
        padding: 0;
        border: 0;
        background: none;
    }
    .todo-item {
        display: flex;
        align-items: center;
        gap: calc(var(--space) * 1.5);
        min-width: 0;
        line-height: 1.5;
    }
    .todo-title {
        overflow-wrap: anywhere;
    }
    /* Completed tasks recede (Things-style) but are neither struck through
       nor recoloured red; see the note on `__ft__`. */
    .todo-row.is-done > .todo-item .todo-title {
        color: var(--color-muted);
    }
    .todo-page input[type=checkbox] {
        border-color: var(--color-muted);
        background-color: transparent;
        /* Pico's radius now follows --radius; 8px on a 20px box reads as
           a radio button, so cap it. */
        border-radius: min(var(--radius), 4px);
        cursor: pointer;
    }
    .todo-page input[type=checkbox]:hover {
        border-color: var(--color-primary);
    }
    .todo-page input[type=checkbox]:checked {
        background-color: var(--color-primary);
        border-color: var(--color-primary);
    }
    .todo-page input[type=checkbox]:focus {
        box-shadow: none;
    }
    .todo-page input[type=checkbox]:focus-visible {
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-primary) 35%, transparent);
    }
    .todo-item input[type=checkbox] {
        flex: none;
        width: 1.25em;
        height: 1.25em;
        margin: 0;
        border-width: 2px;
    }
    .todo-controls {
        display: flex;
        gap: calc(var(--space) * 0.75);
        /* checkbox width + the title gap, so actions line up under the title */
        padding-left: calc(1.25em + var(--space) * 1.5);
    }
    /* Secondary actions stay visible (screenshot agents and the UI questions
       rely on seeing them) but sit back until the row is hovered or focused. */
    .todo-row .todo-controls .todo-btn:not(:hover):not(:focus-visible) {
        color: var(--color-muted);
    }
    .todo-row:hover .todo-controls .todo-btn:not(:hover):not(:focus-visible),
    .todo-row:focus-within .todo-controls .todo-btn:not(:hover):not(:focus-visible) {
        color: var(--color-fg);
    }
    .todo-empty {
        display: none;
        margin: 0;
        padding: calc(var(--space) * 4) calc(var(--space) * 2);
        text-align: center;
        color: var(--color-muted);
    }
    .todo-general:not(:has(#todo-list > *)) .todo-empty {
        display: block;
    }

    /* ---- Kanban layout ------------------------------------------------ */
    .kanban-board {
        width: 100%;
    }
    .kanban-columns {
        display: flex;
        gap: calc(var(--space) * 1.5);
        align-items: flex-start;
        overflow-x: auto;
        padding-bottom: var(--space);
    }
    .kanban-column {
        flex: 0 0 300px;
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: var(--space);
        background-color: var(--color-surface);
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        padding: calc(var(--space) * 1.25);
        min-height: 120px;
    }
    .kanban-column-header {
        display: flex;
        align-items: center;
        gap: var(--space);
        padding: calc(var(--space) * 0.25) calc(var(--space) * 0.25) calc(var(--space) * 0.5);
    }
    .kanban-column-title {
        margin: 0;
        font-size: var(--font-size-base);
        font-weight: 600;
        color: var(--color-fg);
    }
    h3.kanban-column-title:hover {
        text-decoration: underline;
        text-decoration-color: var(--color-muted);
    }
    .kanban-count {
        min-width: 1.5em;
        padding: 0 calc(var(--space) * 0.75);
        border-radius: 999px;
        background-color: color-mix(in srgb, var(--color-fg) 10%, transparent);
        color: var(--color-muted);
        font-size: var(--font-size-sm);
        line-height: 1.6;
        text-align: center;
    }
    .kanban-card {
        background-color: var(--color-bg);
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        box-shadow: 0 1px 0 color-mix(in srgb, var(--color-fg) 8%, transparent);
        padding: var(--space) calc(var(--space) * 1.25);
        margin: 0;
    }
    .kanban-card:hover, .kanban-card:focus-within {
        border-color: color-mix(in srgb, var(--color-fg) 30%, var(--color-border));
    }
    .kanban-card-title {
        margin-bottom: var(--space);
        overflow-wrap: anywhere;
    }
    .kanban-card-controls {
        display: flex;
        gap: calc(var(--space) * 0.5);
        flex-wrap: wrap;
    }
    .kanban-card-controls .todo-btn:not(:hover):not(:focus-visible) {
        color: var(--color-muted);
    }
    .kanban-card:hover .kanban-card-controls .todo-btn:not(:hover):not(:focus-visible),
    .kanban-card:focus-within .kanban-card-controls .todo-btn:not(:hover):not(:focus-visible) {
        color: var(--color-fg);
    }
    .kanban-edit input:not([type=checkbox]) {
        margin-bottom: var(--space);
    }
    .kanban-edit label {
        margin-bottom: var(--space);
    }
    .kanban-add, .kanban-add fieldset {
        margin: 0;
    }
    .kanban-add input, .kanban-add .add-btn {
        padding-top: calc(var(--space) * 0.75);
        padding-bottom: calc(var(--space) * 0.75);
        font-size: var(--font-size-sm);
    }
    .kanban-header-edit {
        display: flex;
        gap: calc(var(--space) * 0.5);
        flex: 1;
        margin: 0;
    }
    .kanban-header-edit input {
        margin: 0;
    }

    /* ---- Chrome ------------------------------------------------------- */
    /* Same text/href/role as before; it is navigation, not an action, so it
       is styled as a quiet link rather than a filled button. */
    a.todo-home-link[role=button] {
        display: inline-block;
        width: auto;
        margin-top: calc(var(--space) * 2);
        padding: calc(var(--space) * 0.5) 0;
        background: none;
        border: 0;
        box-shadow: none;
        color: var(--color-muted);
        font-size: var(--font-size-sm);
        font-weight: 400;
    }
    a.todo-home-link[role=button]::before {
        content: "\\2190" / "";
        margin-right: calc(var(--space) * 0.75);
    }
    a.todo-home-link[role=button]:hover,
    a.todo-home-link[role=button]:focus-visible {
        color: var(--color-fg);
        text-decoration: underline;
    }
    a.todo-home-link[role=button]:focus-visible {
        outline: 2px solid var(--color-primary);
        outline-offset: 2px;
    }
""")

def set_environment(config):
    """Set environment variables for the todo app"""
    global app, logo_title_container
    app.config = config
    db = database(config.todo.database_path)
    global todos, kanban_status
    # create a new table if it doesn't exist
    todos = db.create(Todo, pk="id")

    print("Populating initial todos from config") # config.todo.init_todos should be a list of (title, done) tuples
    for idx, (title, done) in enumerate(config.todo.init_todos):
        todos.insert(Todo(id=idx, title=title, done=done))

    # Spread existing todos across the kanban columns so every column is
    # populated (column membership is tracked in memory, not in the db).
    kanban_status = {
        t.id: kanban_columns[i % len(kanban_columns)]
        for i, t in enumerate(todos())
    }

    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.todo,
        base_url="/todo",
        current_file_path=__file__
    )


def todo_theme():
    """The active theme's `:root` token block, resolved per-request so live
    `reconfigure` theme swaps take effect."""
    return theme_style(app.config, "todo")


id_curr = "current-todo"


def tid(id):
    return f"todo-{id}"


@patch
def __ft__(self: Todo):
    checkbox = Input(
        type="checkbox",
        # fastlite returns ``done`` as int 0/1 from sqlite; fasthtml's
        # ``Input(checked=0)`` then renders ``checked="0"``, which browsers
        # treat as checked (the attribute's presence is what matters, not
        # its value). Coerce to bool so checked=False omits the attribute.
        checked=bool(self.done),
        hx_put=f"/todo/toggle/{self.id}",
        target_id=tid(self.id),
        hx_swap="outerHTML",
        # The title sits beside the checkbox rather than in a <label> (the
        # row structure is part of the observation agents have always seen),
        # so name the control explicitly.
        aria_label=self.title,
    )
    # Done rows are muted, not struck through or red: the UI-question set
    # (tests/ui_questions) offers "The item is struck through" and "The item
    # is shown in red" as *distractors* for checkbox-state questions, so
    # either treatment would make a wrong answer visually true.
    show = Span(self.title, cls="todo-title")
    edit = Button(
        "Edit",
        hx_get=f"/todo/edit/{self.id}",
        target_id=id_curr,
        hx_swap="innerHTML",
        cls="todo-btn edit-btn",
    )
    remove = Button(
        "Remove",
        hx_delete=f"/todo/todos/{self.id}",
        target_id=tid(self.id),
        hx_swap="outerHTML",
        cls="todo-btn remove-btn",
    )
    return Div(
        Li(checkbox, show, cls="todo-item"),
        Li(edit, remove, cls="todo-controls"),
        id=tid(self.id),
        cls="todo-row is-done" if self.done else "todo-row",
    )


def mk_input(**kw):
    return Input(id="new-title", name="title", placeholder="New Todo", **kw)


def current_layout():
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return getattr(config.todo, "layout", "default")


kanban_columns = ["todo", "in_progress", "review", "done"]
kanban_titles = {
    "todo": "To Do",
    "in_progress": "In Progress",
    "review": "Review",
    "done": "Done",
}
kanban_status = {}


def kanban_col_of(todo):
    col = kanban_status.get(todo.id)
    if col in kanban_titles:
        return col
    return "done" if todo.done else "todo"


def kanban_card(todo):
    move_label = "Reopen" if kanban_col_of(todo) == "done" else "Mark Done"
    return Div(
        Div(todo.title, cls="kanban-card-title"),
        Div(
            Button(
                move_label,
                hx_put=f"/todo/toggle/{todo.id}",
                target_id="todo-board",
                hx_swap="outerHTML",
                cls="todo-btn",
            ),
            Button(
                "Edit",
                hx_get=f"/todo/edit/{todo.id}",
                target_id="todo-board",
                hx_swap="outerHTML",
                cls="todo-btn edit-btn",
            ),
            Button(
                "Remove",
                hx_delete=f"/todo/todos/{todo.id}",
                target_id="todo-board",
                hx_swap="outerHTML",
                cls="todo-btn remove-btn",
            ),
            cls="kanban-card-controls",
        ),
        cls="kanban-card",
        id=tid(todo.id),
    )


def kanban_edit_form(todo):
    return Form(
        Input(id="title", name="title", value=todo.title),
        Hidden(id="id", value=str(todo.id)),
        CheckboxX(id="done", label="Done", checked=bool(todo.done)),
        Button("Save", cls="todo-btn save-btn", id="save-button"),
        hx_put="/todo",
        target_id="todo-board",
        hx_swap="outerHTML",
        cls="kanban-card kanban-edit",
    )


def kanban_column_header(col, editing, count):
    if editing:
        return Form(
            Input(name="title", value=kanban_titles[col]),
            Button("Save", cls="todo-btn save-btn"),
            hx_put=f"/todo/kanban/header/{col}",
            target_id="todo-board",
            hx_swap="outerHTML",
            cls="kanban-column-title kanban-header-edit",
        )
    title = H3(
        kanban_titles[col],
        hx_get=f"/todo/kanban/header/{col}",
        target_id="todo-board",
        hx_swap="outerHTML",
        cls="kanban-column-title",
        style="cursor: pointer;",
    )
    # The count lives beside the <h3>, not inside it, so the heading's text
    # (the column name agents read and rename) is unchanged.
    return Div(title, Span(str(count), cls="kanban-count"), cls="kanban-column-header")


def kanban_add_form(col):
    return Form(
        Group(
            Input(id=f"new-title-{col}", name="title", placeholder="Add task"),
            Button("Add", cls="add-btn"),
        ),
        hx_post=f"/todo/kanban/add/{col}",
        target_id="todo-board",
        hx_swap="outerHTML",
        cls="kanban-add",
    )


def kanban_column(col, cards, edit_header):
    return Div(
        kanban_column_header(col, editing=(edit_header == col), count=len(cards)),
        *cards,
        kanban_add_form(col),
        cls="kanban-column",
    )


def render_kanban_board(edit_id=None, edit_header=None):
    def render_card(t):
        if edit_id is not None and t.id == edit_id:
            return kanban_edit_form(t)
        return kanban_card(t)

    buckets = {col: [] for col in kanban_columns}
    for t in todos():
        buckets[kanban_col_of(t)].append(t)
    columns = Div(
        *[
            kanban_column(col, [render_card(t) for t in buckets[col]], edit_header)
            for col in kanban_columns
        ],
        cls="kanban-columns",
    )
    return Div(columns, id="todo-board", cls="kanban-board")


def home_link():
    """Back-to-launcher chrome. Text, href and ``role`` are unchanged from the
    original filled button (agents locate it by them); only the styling is
    demoted to a quiet link."""
    return A("Return to List of Apps", href="/", role="button", cls="todo-home-link")


@rt("/todo")
def get():
    if current_layout() == "kanban_board":
        return Div(
            todo_theme(),
            styles,
            logo_title_container,
            render_kanban_board(),
            home_link(),
            cls="todo-page todo-page--board",
        )
    add = Form(
        Group(
            mk_input(),
            Button("Add", cls="add-btn", id="submit-button"),
        ),
        hx_post="/todo",  # Update this path
        target_id="todo-list",
        hx_swap="beforeend",
    )
    card = Card(
        Ul(*todos(), id="todo-list"),
        # Shown by CSS only while #todo-list has no rows, so it tracks htmx
        # adds/removes without a server round-trip.
        P("No tasks yet. Add your first one above.", cls="todo-empty"),
        header=add,
        footer=Div(id=id_curr),
        cls="todo-general",
    )
    return Div(
        todo_theme(),
        styles,
        logo_title_container,
        card,
        home_link(),
        cls="todo-page todo-page--list",
    )


@rt("/todo/todos/{id}")
def delete(id: int):
    todos.delete(id)
    kanban_status.pop(id, None)
    if current_layout() == "kanban_board":
        return render_kanban_board()
    return clear(id_curr)


@rt("/todo")
def post(title: str):
    # server assigns unique id
    new_id = max([t.id for t in todos()], default=-1) + 1
    todos.upsert(Todo(id=new_id, title=title, done=False))
    return todos[-1], mk_input(hx_swap_oob="true")


@rt("/todo/kanban/add/{col}")
def post(col: str, title: str):
    if col not in kanban_titles or not title.strip():
        return render_kanban_board()
    new_id = max((t.id for t in todos()), default=-1) + 1
    todos.insert(Todo(id=new_id, title=title.strip(), done=(col == "done")))
    kanban_status[new_id] = col
    return render_kanban_board()


@rt("/todo/kanban/header/{col}")
def get(col: str):
    return render_kanban_board(edit_header=col)


@rt("/todo/kanban/header/{col}")
def put(col: str, title: str):
    if col in kanban_titles and title.strip():
        kanban_titles[col] = title.strip()
    return render_kanban_board()


@rt("/todo/edit/{id}")
def get(id: int):
    if current_layout() == "kanban_board":
        return render_kanban_board(edit_id=id)
    res = Form(
        Group(Input(id="title"), Button("Save", cls="todo-btn save-btn", id="save-button")),
        Hidden(id="id"),
        CheckboxX(id="done", label="Done"),
        hx_put="/todo",
        target_id=tid(id),
        id="edit",
    )
    return fill_form(res, todos.get(id))


@rt("/todo")
def put(todo: Todo):
    result = todos.upsert(todo)
    if current_layout() == "kanban_board":
        return render_kanban_board()
    return result, clear(id_curr)


@rt("/todo/toggle/{id}")
def put(id: int):
    todo = todos.get(id)
    if current_layout() == "kanban_board":
        if kanban_col_of(todo) == "done":
            todo.done = False
            kanban_status[id] = "todo"
        else:
            todo.done = True
            kanban_status[id] = "done"
        todos.upsert(todo)
        return render_kanban_board()
    todo.done = not todo.done
    todos.upsert(todo)
    return todo


@rt("/todo/todos/{id}")
def get(id: int):
    todo = todos.get(id)
    btn = Button(
        "delete",
        hx_delete=f"/todos/{todo.id}",
        target_id=tid(todo.id),
        hx_swap="outerHTML",
    )
    return Div(Div(todo.title), btn)


@rt("/todo/count")
def count():
    result = len(todos())
    # zero is not rendered by the frontend, so we return "0" instead of 0
    if result == 0:
        return "0"
    return result

@app.get("/todo_all")
def get_all():
    """Used for rewards"""
    todo_list: List[dict] = [todo.__dict__ for todo in todos()]
    return Response(json.dumps(todo_list), headers={"Content-Type": "application/json"})

def get_todo_routes():
    return app.routes


if __name__ == "__main__":
    print("Warning: Running todo app in standalone mode")
    app.routes = get_todo_routes()
    serve()
