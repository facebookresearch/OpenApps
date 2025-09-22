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


@dataclass
class Todo:
    id: int
    title: str
    done: bool


app, rt = fast_app()
logo_title_container = None
styles = Style("")

def set_environment(config):
    """Set environment variables for the todo app"""
    global app, logo_title_container, styles
    app.config = config
    db = database(config.todo.database_path)
    global todos
    # create a new table if it doesn't exist
    todos = db.create(Todo, pk="id")

    print("Populating initial todos from config") # config.todo.init_todos should be a list of (title, done) tuples
    for idx, (title, done) in enumerate(config.todo.init_todos):
        todos.insert(Todo(id=idx, title=title, done=done))

    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.todo,
        base_url="/todo",
        current_file_path=__file__
    )

    font_family = app.config.todo.font_family
    font_size = app.config.todo.base_font_size
    font_color = getattr(app.config.todo, "font_color", "blue")
    edit_remove_save_button_font_color = getattr(app.config.todo, "edit_remove_save_button_font_color", "")
    edit_button_color = app.config.todo.edit_button_color
    remove_button_color = app.config.todo.remove_button_color
    add_button_color = app.config.todo.add_button_color
    save_button_color = app.config.todo.save_button_color
    background_color = getattr(app.config.todo, "background_color", "")
    form_background_color = getattr(app.config.todo, "form_background_color", "")
    styles.children = [f"""
        body {{
            font-family: {font_family};
            font-size: {font_size};
            color: {font_color};
            background-color: {background_color}
        }}
        .todo, .card, .group, .add-btn {{
            background-color: {background_color}
            color: {font_color};
        }}
        a {{
            color: {font_color};
            text-decoration: none;
        }}
        .todo-item, .todo-controls {{
            list-style-type: none;
            color: {font_color};
        }}
        .todo-general {{
            background-color: {form_background_color}
        }}
        .todo-controls {{
            margin-left: 12px;
        }}
        .todo-btn {{
            transform: scale(.7);
            color: {font_color};
        }}
        .edit-btn {{
            background-color: {edit_button_color};
            border: 1px solid {edit_button_color};
            color: {edit_remove_save_button_font_color};
        }}
        .remove-btn {{
            background-color: {remove_button_color};
            border: 1px solid {remove_button_color};
            color: {edit_remove_save_button_font_color};
        }}
        .add-btn {{
            background-color: {add_button_color};
        }}
        .save-btn {{
            background-color: {save_button_color};
            border: 1px solid {save_button_color};
            color: {edit_remove_save_button_font_color};
        }}
    """]

id_curr = "current-todo"


def tid(id):
    return f"todo-{id}"


@patch
def __ft__(self: Todo):
    checkbox = Input(
        type="checkbox",
        checked=self.done,
        hx_put=f"/todo/toggle/{self.id}",
        target_id=tid(self.id),
        hx_swap="outerHTML",
        style="margin-right: 10px;"
    )
    # show = Span(self.title, f"/todos/{self.id}", id_curr, style="text-decoration: none;")
    show = Span(self.title, style="text-decoration: none;")
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
    return Div(Li(checkbox, show, cls="todo-item"), Li(edit, remove, cls="todo-controls"), id=tid(self.id))


def mk_input(**kw):
    return Input(id="new-title", name="title", placeholder="New Todo", **kw)


@rt("/todo")
def get():
    add = Form(
        Group(
            mk_input(),
            Button("Add", cls="add-btn", id="submit-button"),
        ),
        hx_post="/todo",  # Update this path
        target_id="todo-list",
        hx_swap="beforeend",
        data_theme=app.config.todo.form_background_color,
    )
    card = (Card(Ul(*todos(), id="todo-list"), header=add, footer=Div(id=id_curr), cls="todo-general"),)
    home_button = A("Return to List of Apps", href="/", role="button", cls="contrast", style="margin-top: 1rem;")
    return Div(
        styles,
        logo_title_container,
        card,
        home_button,
    )


@rt("/todo/todos/{id}")
def delete(id: int):
    todos.delete(id)
    return clear(id_curr)


@rt("/todo")
def post(todo: Todo):
    return todos.insert(todo), mk_input(hx_swap_oob="true")


@rt("/todo/edit/{id}")
def get(id: int):
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
    return todos.upsert(todo), clear(id_curr)


@rt("/todo/toggle/{id}")
def put(id: int):
    todo = todos.get(id)
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
