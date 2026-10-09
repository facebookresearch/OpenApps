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
from open_apps.apps.start_page.helper import create_logo_header
from open_apps.frontend import local_hdrs
from open_apps.theme import theme_style
from open_apps.apps.todo_app.styles import styles


@dataclass
class Todo:
    id: int
    title: str
    done: bool


app, rt = fast_app(default_hdrs=False, hdrs=local_hdrs())
logo_title_container = None


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


def home_link(prominent: bool = False):
    """Back-to-launcher chrome. Text, href, ``role`` and position are the same
    in every layout (agents locate it by them).

    ``prominent`` keeps it a full-size filled button: the default layout's
    navigation tasks depend on screenshot agents finding it. Other layouts
    demote it to a quiet link, the realistic treatment."""
    cls = "todo-home-button" if prominent else "todo-home-link"
    return A("Return to List of Apps", href="/", role="button", cls=cls)


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
        home_link(prominent=True),
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
    # Return upsert's own result, the way the PUT handler below already does.
    # This was `todos[-1]`, which looks like "the last row" but is not: fastlite
    # indexes a table by PRIMARY KEY, so it asked for the row with id == -1 and
    # raised NotFoundError. The insert on the line above had already committed,
    # so the todo was created and only the response failed -- the write landed,
    # the browser got a 500, htmx had nothing to swap, and the new item never
    # appeared. State-based tests pass straight through that.
    return todos.upsert(Todo(id=new_id, title=title, done=False)), mk_input(
        hx_swap_oob="true"
    )


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
