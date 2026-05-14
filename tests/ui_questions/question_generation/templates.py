"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Question templates for testing UI understanding.

Each template returns multiple-choice questions whose answer is grounded in what
is *visible in the rendered screenshot of one app*. Templates that would require
inspecting state outside of the screenshot (off-screen list items, other months,
forms/modals shown only after a click, HTML structure, framework choice, etc.)
have been removed.

Two assumptions about the screenshots:

1. The todo list is rendered at full page height but, in the captured screenshot,
   only the first ~12 items are visible. Templates that reason about the list
   only consider the visible prefix (``MAX_VISIBLE_TODOS``).
2. The calendar shows a single month grid. Only events that fall in that month
   appear on screen, and only their title + date number are visible (no location,
   no recurring marker, no description). Calendar templates filter to the
   currently-displayed month using ``current_month`` (defaults to today).
"""

import calendar as cal_mod
import random
from dataclasses import dataclass
from datetime import datetime, date

MAX_VISIBLE_TODOS = 12


@dataclass
class MCQuestion:
    question: str
    choices: dict[str, str]
    correct: str
    category: str
    app: str
    difficulty: str = "easy"

    def format_as_prompt(self) -> str:
        lines = [self.question]
        for key, value in self.choices.items():
            lines.append(f"  {key}) {value}")
        return "\n".join(lines)


def _shuffle_choices(
    correct: str, distractors: list[str], rng: random.Random
) -> tuple[dict[str, str], str]:
    all_options = [correct] + distractors[:3]
    rng.shuffle(all_options)
    labels = ["A", "B", "C", "D"]
    choices = {labels[i]: all_options[i] for i in range(len(all_options))}
    correct_letter = [k for k, v in choices.items() if v == correct][0]
    return choices, correct_letter


def _nearby_integers(correct: int, _upper_bound: int, rng: random.Random) -> list[str]:
    candidates = set()
    for delta in [-2, -1, 1, 2, 3, -3]:
        val = correct + delta
        if val >= 0 and val != correct:
            candidates.add(val)
    candidates = list(candidates)
    rng.shuffle(candidates)
    return [str(c) for c in candidates[:3]]


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _top_level_files(node: dict) -> list[dict]:
    """Files at the root of the codeeditor tree (visible without expanding folders)."""
    return [c for c in node.get("children", []) if c.get("type") == "file"]


def _top_level_folders(node: dict) -> list[dict]:
    """Folders at the root of the codeeditor tree."""
    return [c for c in node.get("children", []) if c.get("type") == "folder"]


def _resolve_current_month(current_month) -> tuple[int, int]:
    """Normalize the current_month context to a (year, month) tuple."""
    if current_month is None:
        today = date.today()
        return today.year, today.month
    if isinstance(current_month, (tuple, list)):
        return int(current_month[0]), int(current_month[1])
    raise ValueError(f"Unrecognized current_month: {current_month!r}")


def _events_in_month(events: list[dict], year: int, month: int) -> list[dict]:
    """Return events whose date (or yearly/monthly recurrence) lands in (year, month)."""
    visible = []
    for e in events:
        d = e.get("date")
        if not d:
            continue
        try:
            ev_date = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        recurring = (e.get("recurring") or "").lower()
        if ev_date.year == year and ev_date.month == month:
            visible.append(e)
        elif recurring == "yearly" and ev_date.month == month:
            visible.append(e)
        elif recurring == "monthly":
            visible.append(e)
        elif recurring == "weekly":
            # weekly events show up every week of every month
            visible.append(e)
    return visible


# ===========================================================================
# Todo App Templates — only reason about the first MAX_VISIBLE_TODOS items
# ===========================================================================


def _visible_todos(state: dict) -> list[dict]:
    return state.get("todo", [])[:MAX_VISIBLE_TODOS]


def todo_count_done(state, config, rng, **_):
    todos = _visible_todos(state)
    if len(todos) < 4:
        return []
    done_count = sum(1 for t in todos if t.get("done"))
    distractors = _nearby_integers(done_count, len(todos), rng)
    choices, correct_letter = _shuffle_choices(str(done_count), distractors, rng)
    return [
        MCQuestion(
            question="Looking at the visible todo list, how many items are checked (marked as done)?",
            choices=choices,
            correct=correct_letter,
            category="element_counting",
            app="todo",
        )
    ]


def todo_count_not_done(state, config, rng, **_):
    todos = _visible_todos(state)
    if len(todos) < 4:
        return []
    not_done_count = sum(1 for t in todos if not t.get("done"))
    distractors = _nearby_integers(not_done_count, len(todos), rng)
    choices, correct_letter = _shuffle_choices(str(not_done_count), distractors, rng)
    return [
        MCQuestion(
            question="Looking at the visible todo list, how many items are unchecked (not done)?",
            choices=choices,
            correct=correct_letter,
            category="element_counting",
            app="todo",
        )
    ]


def todo_identify_done_items(state, config, rng, **_):
    todos = _visible_todos(state)
    done = [t["title"] for t in todos if t.get("done")]
    not_done = [t["title"] for t in todos if not t.get("done")]
    if len(done) < 1 or len(not_done) < 3:
        return []
    questions = []
    for title in done:
        distractors = rng.sample(not_done, 3)
        choices, correct_letter = _shuffle_choices(title, distractors, rng)
        questions.append(
            MCQuestion(
                question="Which of the following visible todo items is shown with its checkbox checked?",
                choices=choices,
                correct=correct_letter,
                category="element_state",
                app="todo",
            )
        )
    return questions


def todo_identify_not_done_items(state, config, rng, **_):
    todos = _visible_todos(state)
    done = [t["title"] for t in todos if t.get("done")]
    not_done = [t["title"] for t in todos if not t.get("done")]
    if len(not_done) < 1 or len(done) < 3:
        return []
    questions = []
    for title in not_done:
        distractors = rng.sample(done, 3)
        choices, correct_letter = _shuffle_choices(title, distractors, rng)
        questions.append(
            MCQuestion(
                question="Which of the following visible todo items is shown with its checkbox unchecked?",
                choices=choices,
                correct=correct_letter,
                category="element_state",
                app="todo",
            )
        )
    return questions


def todo_specific_item_state(state, config, rng, **_):
    todos = _visible_todos(state)
    questions = []
    for t in todos:
        is_done = bool(t.get("done"))
        correct = "Checkbox is checked" if is_done else "Checkbox is unchecked"
        wrong = "Checkbox is unchecked" if is_done else "Checkbox is checked"
        distractors = [wrong, "The item is shown in red", "The item is struck through"]
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(
            MCQuestion(
                question=f"In the visible todo list, what is the state of the checkbox next to '{t['title']}'?",
                choices=choices,
                correct=correct_letter,
                category="element_state",
                app="todo",
                difficulty="medium",
            )
        )
    return questions


def todo_first_item(state, config, rng, **_):
    todos = _visible_todos(state)
    if len(todos) < 4:
        return []
    correct = todos[0]["title"]
    distractors = rng.sample([t["title"] for t in todos[1:]], 3)
    choices, correct_letter = _shuffle_choices(correct, distractors, rng)
    return [
        MCQuestion(
            question="What is the first todo item shown at the top of the list?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="todo",
        )
    ]


def todo_items_at_positions(state, config, rng, **_):
    todos = _visible_todos(state)
    if len(todos) < 6:
        return []
    questions = []
    for pos in range(2, len(todos) + 1):
        correct = todos[pos - 1]["title"]
        others = [t["title"] for i, t in enumerate(todos) if i != pos - 1]
        distractors = rng.sample(others, 3)
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(
            MCQuestion(
                question=f"What is the {_ordinal(pos)} todo item shown in the visible list?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="todo",
                difficulty="medium",
            )
        )
    return questions


def todo_item_neighbor(state, config, rng, **_):
    todos = _visible_todos(state)
    if len(todos) < 5:
        return []
    questions = []
    indices = list(range(len(todos) - 1))
    rng.shuffle(indices)
    for i in indices[:5]:
        current = todos[i]["title"]
        correct = todos[i + 1]["title"]
        others = [t["title"] for j, t in enumerate(todos) if j != i + 1 and j != i]
        if len(others) < 3:
            continue
        distractors = rng.sample(others, 3)
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(
            MCQuestion(
                question=f"In the visible todo list, which item appears directly below '{current}'?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="todo",
                difficulty="medium",
            )
        )
    return questions


def todo_element_type_for_completion(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Checkbox",
        ["Toggle switch", "Radio button", "Star icon"],
        rng,
    )
    return [
        MCQuestion(
            question="What type of UI element appears to the left of each todo item title?",
            choices=choices,
            correct=correct_letter,
            category="element_identification",
            app="todo",
        )
    ]


def todo_controls_per_item(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Edit and Remove",
        ["Delete and Archive", "Save and Cancel", "Complete and Skip"],
        rng,
    )
    return [
        MCQuestion(
            question="What two button labels are shown beneath each todo item?",
            choices=choices,
            correct=correct_letter,
            category="element_identification",
            app="todo",
        )
    ]


def todo_add_button_interaction(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "The 'Add' button immediately to the right of the new-todo input",
        [
            "The 'Edit' button under the first todo item",
            "The checkbox next to the first todo item",
            "The 'Return to List of Apps' button below the list",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Which visible control would you use to submit a new todo after typing it?",
            choices=choices,
            correct=correct_letter,
            category="element_interaction",
            app="todo",
        )
    ]


def todo_item_edit_interaction(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "The 'Edit' button beneath that todo item",
        [
            "The checkbox to the left of the item",
            "The 'Add' button in the input row",
            "The 'Remove' button beneath the next item",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Which visible control would you use to modify the text of a todo item?",
            choices=choices,
            correct=correct_letter,
            category="element_interaction",
            app="todo",
        )
    ]


def todo_input_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "At the top of the todo card, above the visible list items",
        [
            "At the bottom of the page below the return button",
            "In a sidebar on the left side of the page",
            "Inside each todo item row",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where is the new-todo input field positioned on the todo page?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="todo",
        )
    ]


def todo_return_button_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Top right next to the search bar",
        [
            "Above the app title",
            "Inside the new-todo input row",
            "In the right edge of each todo item",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where is the 'Add button' control located on the todo page?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="todo",
        )
    ]


def todo_input_placeholder(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "New Todo",
        ["Add a task", "Enter todo here", "Type a new item"],
        rng,
    )
    return [
        MCQuestion(
            question="What placeholder text is shown in the input field at the top of the todo app?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="todo",
        )
    ]


def todo_add_button_label(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices("Add", ["Submit", "Create", "Save"], rng)
    return [
        MCQuestion(
            question="What is the label on the button to the right of the new-todo input field?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="todo",
        )
    ]


def todo_app_title(state, config, rng, **_):
    title = (config or {}).get("title", "OpenTodos")
    distractors = ["My Tasks", "Todo List", "Task Manager"]
    if title != "OpenTodos":
        distractors = ["OpenTodos"] + distractors[:2]
    choices, correct_letter = _shuffle_choices(title, distractors, rng)
    return [
        MCQuestion(
            question="What title is displayed at the top of the todo app page?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="todo",
        )
    ]


# ===========================================================================
# Calendar App Templates — restricted to the currently-displayed month
# ===========================================================================


def calendar_displayed_month(state, config, rng, current_month=None, **_):
    year, month = _resolve_current_month(current_month)
    correct = f"{cal_mod.month_name[month]} {year}"
    other_months = [m for m in range(1, 13) if m != month]
    rng.shuffle(other_months)
    distractors = [f"{cal_mod.month_name[m]} {year}" for m in other_months[:3]]
    choices, correct_letter = _shuffle_choices(correct, distractors, rng)
    return [
        MCQuestion(
            question="Which month and year does the calendar currently display?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="calendar",
        )
    ]


def calendar_count_events_in_month(state, config, rng, current_month=None, **_):
    year, month = _resolve_current_month(current_month)
    visible = _events_in_month(state.get("calendar", []), year, month)
    count = len(visible)
    if count == 0:
        return []
    distractors = _nearby_integers(count, count + 5, rng)
    choices, correct_letter = _shuffle_choices(str(count), distractors, rng)
    return [
        MCQuestion(
            question="How many events are shown on the calendar grid for the displayed month?",
            choices=choices,
            correct=correct_letter,
            category="element_counting",
            app="calendar",
        )
    ]


def calendar_event_exists_in_month(state, config, rng, current_month=None, **_):
    year, month = _resolve_current_month(current_month)
    visible = _events_in_month(state.get("calendar", []), year, month)
    if len(visible) < 1:
        return []
    visible_titles = [e["title"] for e in visible]
    fake_events = [
        "NeurIPS 2026 Deadline",
        "Team Standup Meeting",
        "Dentist Appointment",
        "ICML 2026 Workshop",
        "Mom's Anniversary",
        "Sprint Planning",
        "Yoga Class",
        "Guitar Lesson",
        "Board Meeting",
    ]
    fake_events = [f for f in fake_events if f not in visible_titles]
    if len(fake_events) < 3:
        return []
    questions = []
    for correct in visible_titles:
        distractors = rng.sample(fake_events, 3)
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(
            MCQuestion(
                question="Which of the following events appears on the displayed calendar month?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="calendar",
            )
        )
    return questions


def calendar_event_day_in_month(state, config, rng, current_month=None, **_):
    """For each visible event, ask which day-of-month its cell falls on."""
    year, month = _resolve_current_month(current_month)
    visible = _events_in_month(state.get("calendar", []), year, month)
    if len(visible) < 1:
        return []
    last_day = cal_mod.monthrange(year, month)[1]
    questions = []
    for ev in visible:
        try:
            ev_date = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        correct_day = ev_date.day
        if correct_day > last_day:
            continue
        other_days = [d for d in range(1, last_day + 1) if d != correct_day]
        distractors = [str(d) for d in rng.sample(other_days, min(3, len(other_days)))]
        if len(distractors) < 3:
            continue
        choices, correct_letter = _shuffle_choices(str(correct_day), distractors, rng)
        questions.append(
            MCQuestion(
                question=f"On which day of the displayed month is the event '{ev['title']}' shown?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="calendar",
                difficulty="medium",
            )
        )
    return questions


def calendar_view_toggle(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Calendar and Agenda",
        ["Day and Week", "Month and Year", "Timeline and Board"],
        rng,
    )
    return [
        MCQuestion(
            question="What two view-toggle buttons are shown above the calendar grid?",
            choices=choices,
            correct=correct_letter,
            category="element_identification",
            app="calendar",
        )
    ]


def calendar_month_navigation(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "'< Prev' on the left and 'Next >' on the right of the month name",
        [
            "A dropdown to select the month",
            "Up and down arrow keys",
            "A search box for jumping to a date",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="What controls are shown for navigating between months on the calendar?",
            choices=choices,
            correct=correct_letter,
            category="navigation",
            app="calendar",
        )
    ]


def calendar_weekday_headers(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Mon, Tue, Wed, Thu, Fri, Sat, Sun",
        [
            "Sun, Mon, Tue, Wed, Thu, Fri, Sat",
            "M, T, W, T, F, S, S",
            "Monday through Friday only",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="What weekday header labels appear across the top of the calendar grid?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="calendar",
        )
    ]


def calendar_app_title(state, config, rng, **_):
    title = (config or {}).get("title", "OpenCalendar")
    distractors = ["My Calendar", "Schedule", "Events"]
    if title != "OpenCalendar":
        distractors = ["OpenCalendar"] + distractors[:2]
    choices, correct_letter = _shuffle_choices(title, distractors, rng)
    return [
        MCQuestion(
            question="What title is displayed at the top of the calendar app page?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="calendar",
        )
    ]


def calendar_add_event_button(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Add Event",
        ["New Event", "Create Event", "+ Event"],
        rng,
    )
    return [
        MCQuestion(
            question="What is the label on the button at the bottom-left of the calendar page?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="calendar",
        )
    ]


def calendar_return_button(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Return to List of Apps",
        ["Back to Home", "Main Menu", "All Apps"],
        rng,
    )
    return [
        MCQuestion(
            question="What is the label on the button at the bottom-right of the calendar page?",
            choices=choices,
            correct=correct_letter,
            category="navigation",
            app="calendar",
        )
    ]


def calendar_add_event_interaction(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "The 'Add Event' button in the footer area",
        [
            "The 'Next >' button beside the month name",
            "A date number inside the calendar grid",
            "The 'Agenda' view-toggle button",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Which visible control would you use to start creating a new calendar event?",
            choices=choices,
            correct=correct_letter,
            category="element_interaction",
            app="calendar",
        )
    ]


def calendar_next_month_interaction(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "The 'Next >' button to the right of the month heading",
        [
            "The '< Prev' button to the left of the month heading",
            "The 'Calendar' view-toggle button",
            "The 'Return to List of Apps' button in the footer",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Which visible control would you use to move to the following month?",
            choices=choices,
            correct=correct_letter,
            category="element_interaction",
            app="calendar",
        )
    ]


def calendar_view_toggle_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Near the top of the page, above the calendar grid",
        [
            "Inside the bottom footer next to the return button",
            "In the leftmost column of the calendar grid",
            "In a right-side sidebar",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where are the 'Calendar' and 'Agenda' view-toggle controls positioned?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="calendar",
        )
    ]


def calendar_footer_controls_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "At the bottom of the calendar page, beneath the grid",
        [
            "Above the month navigation controls",
            "In the first row of weekday headers",
            "Floating over the top-right calendar cell",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where are the 'Add Event' and 'Return to List of Apps' controls located?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="calendar",
        )
    ]


# ===========================================================================
# Messenger App Templates — only the conversation-list view is on screen
# ===========================================================================


def messenger_count_conversations(state, config, rng, **_):
    conversations = state.get("messenger", [])
    total = len(conversations)
    if total == 0:
        return []
    distractors = _nearby_integers(total, total + 4, rng)
    choices, correct_letter = _shuffle_choices(str(total), distractors, rng)
    return [
        MCQuestion(
            question="How many conversation rows are shown in the messenger conversation list?",
            choices=choices,
            correct=correct_letter,
            category="element_counting",
            app="messenger",
        )
    ]


def messenger_contact_exists(state, config, rng, **_):
    conversations = state.get("messenger", [])
    if len(conversations) < 1:
        return []
    contact_names = [c["user"] for c in conversations]
    fake_contacts = ["Diana", "Eve", "Frank", "Grace", "Henry", "Ivan", "Julia"]
    fake_contacts = [f for f in fake_contacts if f not in contact_names]
    if len(fake_contacts) < 3:
        return []
    questions = []
    for name in contact_names:
        distractors = rng.sample(fake_contacts, 3)
        choices, correct_letter = _shuffle_choices(name, distractors, rng)
        questions.append(
            MCQuestion(
                question="Which of the following contact names is shown in the messenger conversation list?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="messenger",
            )
        )
    return questions


def messenger_contact_not_exists(state, config, rng, **_):
    conversations = state.get("messenger", [])
    contact_names = [c["user"] for c in conversations]
    fake_contacts = ["Diana", "Eve", "Frank", "Grace", "Henry"]
    fake_contacts = [f for f in fake_contacts if f not in contact_names]
    if len(fake_contacts) < 3 or len(contact_names) < 3:
        return []
    questions = []
    for fake in fake_contacts[:3]:
        real = rng.sample(contact_names, 3)
        choices, correct_letter = _shuffle_choices(fake, real, rng)
        questions.append(
            MCQuestion(
                question="Which of the following contact names does NOT appear in the messenger conversation list?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="messenger",
                difficulty="medium",
            )
        )
    return questions


def messenger_group_chat_exists(state, config, rng, **_):
    conversations = state.get("messenger", [])
    group_chats = [c for c in conversations if "group" in c["user"].lower()]
    has_group = len(group_chats) > 0
    if has_group:
        correct = f"Yes — '{group_chats[0]['user']}'"
        distractors = [
            "No, only individual conversations",
            "Yes — 'Team Chat'",
            "Yes — 'General Channel'",
        ]
    else:
        correct = "No, only individual conversations"
        distractors = [
            "Yes — 'Fantastic4GroupChat'",
            "Yes — 'Team Chat'",
            "Yes — 'General Channel'",
        ]
    choices, correct_letter = _shuffle_choices(correct, distractors, rng)
    return [
        MCQuestion(
            question="Is a group chat row visible in the messenger conversation list?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="messenger",
        )
    ]


def messenger_app_title(state, config, rng, **_):
    title = (config or {}).get("title", "OpenMessages")
    distractors = ["My Messages", "Chats", "Inbox"]
    if title != "OpenMessages":
        distractors = ["OpenMessages"] + distractors[:2]
    choices, correct_letter = _shuffle_choices(title, distractors, rng)
    return [
        MCQuestion(
            question="What title is displayed at the top of the messenger page?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="messenger",
        )
    ]


def messenger_return_button(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Return to List of Apps",
        ["Back", "Home", "Main Menu"],
        rng,
    )
    return [
        MCQuestion(
            question="What is the label on the button at the bottom of the messenger page?",
            choices=choices,
            correct=correct_letter,
            category="navigation",
            app="messenger",
        )
    ]


def messenger_open_chat_interaction(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Select the conversation row with the contact's name and avatar",
        [
            "Use a compose box at the bottom of the conversation list",
            "Click the page title at the top",
            "Use the 'Return to List of Apps' button",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Which visible element would you use to open a conversation from the messenger list?",
            choices=choices,
            correct=correct_letter,
            category="element_interaction",
            app="messenger",
        )
    ]


def messenger_return_button_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Below the conversation list",
        [
            "Above the app title",
            "To the right of each contact name",
            "Inside a top navigation bar",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where is the 'Return to List of Apps' button positioned on the messenger list page?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="messenger",
        )
    ]


# ===========================================================================
# Map App Templates — sidebar + map are fully visible
# ===========================================================================


def map_count_saved_places(state, config, rng, **_):
    places = state.get("map", [])
    total = len(places)
    if total == 0:
        return []
    distractors = _nearby_integers(total, total + 5, rng)
    choices, correct_letter = _shuffle_choices(str(total), distractors, rng)
    return [
        MCQuestion(
            question="How many entries are listed under 'Saved Locations' in the map sidebar?",
            choices=choices,
            correct=correct_letter,
            category="element_counting",
            app="map",
        )
    ]


def map_place_exists(state, config, rng, **_):
    places = state.get("map", [])
    if len(places) < 1:
        return []
    place_names = [p["name"] for p in places]
    fake_places = [
        "Golden Gate Bridge",
        "Eiffel Tower",
        "Buckingham Palace",
        "Sydney Opera House",
        "Big Ben",
        "Colosseum",
        "Taj Mahal",
    ]
    fake_places = [f for f in fake_places if f not in place_names]
    if len(fake_places) < 3:
        return []
    questions = []
    for name in place_names:
        distractors = rng.sample(fake_places, 3)
        choices, correct_letter = _shuffle_choices(name, distractors, rng)
        questions.append(
            MCQuestion(
                question="Which of the following appears under 'Saved Locations' in the map sidebar?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="map",
            )
        )
    return questions


def map_place_not_saved(state, config, rng, **_):
    places = state.get("map", [])
    place_names = [p["name"] for p in places]
    fake_places = [
        "Golden Gate Bridge",
        "Eiffel Tower",
        "Buckingham Palace",
        "Sydney Opera House",
        "Big Ben",
        "Colosseum",
    ]
    fake_places = [f for f in fake_places if f not in place_names]
    if len(fake_places) < 3 or len(place_names) < 3:
        return []
    questions = []
    for fake in fake_places[:3]:
        real = rng.sample(place_names, 3)
        choices, correct_letter = _shuffle_choices(fake, real, rng)
        questions.append(
            MCQuestion(
                question="Which of the following is NOT listed under 'Saved Locations' in the map sidebar?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="map",
                difficulty="medium",
            )
        )
    return questions


def map_delete_button(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "An 'x' button next to each saved location in the sidebar",
        [
            "A trash-can icon at the bottom of the sidebar",
            "A 'Delete' button below the map",
            "There is no visible delete control",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="What control is shown next to each saved location row in the map sidebar?",
            choices=choices,
            correct=correct_letter,
            category="element_interaction",
            app="map",
        )
    ]


def map_sidebar_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "On the right side of the page, next to the map",
        [
            "On the left side of the page",
            "At the bottom of the page, below the map",
            "In a floating overlay on top of the map",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where is the sidebar containing search and saved locations positioned?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="map",
        )
    ]


def map_search_interaction(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Type in the search input and use the 'Search' button beside it",
        [
            "Click directly on the 'Saved Locations' heading",
            "Use the map tile-provider radio buttons",
            "Use the 'x' button beside a saved location",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Which visible controls would you use to look up a location in the map sidebar?",
            choices=choices,
            correct=correct_letter,
            category="element_interaction",
            app="map",
        )
    ]


def map_return_button_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "At the top of the right-hand sidebar",
        [
            "At the bottom-left corner of the map",
            "Inside the saved-location list",
            "Centered below the map",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where is the 'Return to List of Apps' button located on the map page?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="map",
        )
    ]


def map_tile_provider_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "In a control panel over the top-right of the map area",
        [
            "Under the 'Saved Locations' heading in the sidebar",
            "In a footer below the map",
            "Above the app title in the sidebar",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where are the map tile-provider radio options positioned?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="map",
        )
    ]


def map_search_placeholder(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Search location...",
        ["Find a place", "Where to?", "Enter address"],
        rng,
    )
    return [
        MCQuestion(
            question="What placeholder text is shown inside the search input in the map sidebar?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="map",
        )
    ]


def map_search_button_label(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Search",
        ["Go", "Find", "Lookup"],
        rng,
    )
    return [
        MCQuestion(
            question="What is the label on the button to the right of the search input in the map sidebar?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="map",
        )
    ]


def map_return_button_label(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Return to List of Apps",
        ["Back to Home", "Go Home", "Main Menu"],
        rng,
    )
    return [
        MCQuestion(
            question="What is the label on the button at the top-right of the map page?",
            choices=choices,
            correct=correct_letter,
            category="navigation",
            app="map",
        )
    ]


def map_saved_locations_heading(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Saved Locations",
        ["My Bookmarks", "Favorite Places", "Pinned Locations"],
        rng,
    )
    return [
        MCQuestion(
            question="What heading appears in the map sidebar above the list of saved places?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="map",
        )
    ]


def map_current_location_info_heading(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Current Location Info",
        ["Selected Point Details", "Location Data", "Coordinates"],
        rng,
    )
    return [
        MCQuestion(
            question="What heading appears in the map sidebar between the search input and 'Saved Locations'?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="map",
        )
    ]


def map_app_title(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "OpenMaps",
        ["My Maps", "Map App", "Locations"],
        rng,
    )
    return [
        MCQuestion(
            question="What title is displayed at the top of the map sidebar?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="map",
        )
    ]


def map_tile_provider_options(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "OpenStreetMap, OpenStreetMap HOT, OpenTopoMap, ESRI Satellite, ESRI Terrain, CartoDB Dark, CartoDB Voyager, OpenStreetMap Transport",
        [
            "Google Maps, Apple Maps, Bing Maps",
            "Mapbox Streets, Mapbox Satellite, Mapbox Outdoors",
            "Only OpenStreetMap is offered",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="What set of map tile-provider radio options is shown at the top-right of the map?",
            choices=choices,
            correct=correct_letter,
            category="element_identification",
            app="map",
        )
    ]


# ===========================================================================
# Code Editor App Templates — only the visible (collapsed) tree is on screen
# ===========================================================================


def codeeditor_top_level_file_count(state, config, rng, **_):
    files = _top_level_files(state.get("codeeditor", {}))
    total = len(files)
    if total == 0:
        return []
    distractors = _nearby_integers(total, total + 5, rng)
    choices, correct_letter = _shuffle_choices(str(total), distractors, rng)
    return [
        MCQuestion(
            question="How many top-level file entries are shown in the code editor's file tree?",
            choices=choices,
            correct=correct_letter,
            category="element_counting",
            app="codeeditor",
        )
    ]


def codeeditor_top_level_folder_count(state, config, rng, **_):
    folders = _top_level_folders(state.get("codeeditor", {}))
    total = len(folders)
    if total == 0:
        return []
    distractors = _nearby_integers(total, total + 4, rng)
    choices, correct_letter = _shuffle_choices(str(total), distractors, rng)
    return [
        MCQuestion(
            question="How many top-level folder entries are shown in the code editor's file tree?",
            choices=choices,
            correct=correct_letter,
            category="element_counting",
            app="codeeditor",
        )
    ]


def codeeditor_top_level_file_exists(state, config, rng, **_):
    files = _top_level_files(state.get("codeeditor", {}))
    if len(files) < 1:
        return []
    file_names = [f["name"] for f in files]
    fake_files = [
        "index.html",
        "main.go",
        "README.md",
        "app.js",
        "config.yaml",
        "Makefile",
        "test.py",
    ]
    fake_files = [f for f in fake_files if f not in file_names]
    if len(fake_files) < 3:
        return []
    questions = []
    for name in file_names:
        distractors = rng.sample(fake_files, 3)
        choices, correct_letter = _shuffle_choices(name, distractors, rng)
        questions.append(
            MCQuestion(
                question="Which of the following file names is shown at the top level of the code editor's file tree?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="codeeditor",
            )
        )
    return questions


def codeeditor_top_level_folder_exists(state, config, rng, **_):
    folders = _top_level_folders(state.get("codeeditor", {}))
    if len(folders) < 1:
        return []
    folder_names = [f["name"] for f in folders]
    fake_folders = ["src", "lib", "tests", "docs", "build", "dist", "assets"]
    fake_folders = [f for f in fake_folders if f not in folder_names]
    if len(fake_folders) < 3:
        return []
    questions = []
    for name in folder_names:
        distractors = rng.sample(fake_folders, 3)
        choices, correct_letter = _shuffle_choices(name, distractors, rng)
        questions.append(
            MCQuestion(
                question="Which of the following folder names is shown at the top level of the code editor's file tree?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="codeeditor",
            )
        )
    return questions


def codeeditor_file_extension(state, config, rng, **_):
    files = _top_level_files(state.get("codeeditor", {}))
    if len(files) < 1:
        return []
    ext_to_lang = {
        ".py": "Python",
        ".c": "C",
        ".js": "JavaScript",
        ".css": "CSS",
        ".yaml": "YAML",
        ".md": "Markdown",
        ".go": "Go",
        ".html": "HTML",
    }
    all_langs = list(ext_to_lang.values())
    questions = []
    for f in files:
        name = f["name"]
        ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        lang = ext_to_lang.get(ext)
        if not lang:
            continue
        distractors = [l for l in all_langs if l != lang]
        rng.shuffle(distractors)
        choices, correct_letter = _shuffle_choices(lang, distractors[:3], rng)
        questions.append(
            MCQuestion(
                question=f"Based on the file extension shown in the file tree, what language is '{name}'?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="codeeditor",
            )
        )
    return questions


def codeeditor_sidebar_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "On the left side of the page, in a narrow column",
        [
            "Across the top of the page as a horizontal list",
            "On the right side of the page",
            "Hidden behind a hamburger menu",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where is the file tree positioned in the code editor?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="codeeditor",
        )
    ]


def codeeditor_new_file_buttons(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Two buttons labeled 'New File' and 'New Folder' at the top of the sidebar",
        [
            "A single '+' button at the top of the sidebar",
            "A 'File' menu in a top menu bar",
            "Right-click the file tree to open a context menu",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="What buttons are shown at the top of the code editor's file tree sidebar?",
            choices=choices,
            correct=correct_letter,
            category="element_identification",
            app="codeeditor",
        )
    ]


def codeeditor_language_dropdown(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "A 'Language:' dropdown",
        [
            "A row of language icon buttons",
            "A text input where you type the language",
            "Radio buttons for each language",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="How is the syntax language selected in the top-right of the code editor?",
            choices=choices,
            correct=correct_letter,
            category="element_identification",
            app="codeeditor",
        )
    ]


def codeeditor_theme_dropdown(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "A 'Theme:' dropdown",
        [
            "A toggle between light and dark mode only",
            "A color-swatch picker",
            "There is no theme selector",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="How is the editor theme selected in the top-right of the code editor?",
            choices=choices,
            correct=correct_letter,
            category="element_identification",
            app="codeeditor",
        )
    ]


def codeeditor_app_title(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "OpenCodeEditor",
        ["My Editor", "CodeApp", "Editor"],
        rng,
    )
    return [
        MCQuestion(
            question="What title is displayed at the top-left of the code editor page?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="codeeditor",
        )
    ]


def codeeditor_no_file_selected_message(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "No file selected",
        ["Open a file to begin", "Choose a file from the sidebar", "Welcome"],
        rng,
    )
    return [
        MCQuestion(
            question="What message is shown in the code editor's main pane header when no file is open?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="codeeditor",
        )
    ]


def codeeditor_new_file_interaction(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "The 'New File' button at the top of the file-tree sidebar",
        [
            "The 'Language:' dropdown in the editor header",
            "The 'Theme:' dropdown in the editor header",
            "A top-level file entry in the tree",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Which visible control would you use to create a new file in the code editor?",
            choices=choices,
            correct=correct_letter,
            category="element_interaction",
            app="codeeditor",
        )
    ]


def codeeditor_theme_interaction(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "The 'Theme:' dropdown in the top-right of the editor pane",
        [
            "The 'New Folder' button in the sidebar",
            "The file tree entry icons",
            "The 'Language:' dropdown",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Which visible control would you use to change the editor's color theme?",
            choices=choices,
            correct=correct_letter,
            category="element_interaction",
            app="codeeditor",
        )
    ]


def codeeditor_main_pane_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "To the right of the file-tree sidebar",
        [
            "Above the file-tree sidebar",
            "Below the 'Return to List of Apps' button",
            "Inside a pop-up modal",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where is the main editor pane positioned relative to the file tree?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="codeeditor",
        )
    ]


def codeeditor_dropdowns_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "In the top-right area of the main editor pane",
        [
            "At the bottom of the file-tree sidebar",
            "Centered below the app title",
            "Under each file name in the tree",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where are the 'Language:' and 'Theme:' dropdowns positioned in the code editor?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="codeeditor",
        )
    ]


# ===========================================================================
# Start Page Templates — 5 visible app tiles (no OnlineShop)
# ===========================================================================

_START_PAGE_APPS = [
    "OpenTodos",
    "OpenCalendar",
    "OpenMessages",
    "OpenMaps",
    "OpenCodeEditor",
]
_FAKE_APPS = [
    "OpenNotes",
    "OpenWeather",
    "OpenMusic",
    "OpenFitness",
    "OpenBanking",
    "OpenTravel",
    "OpenShop",
]


def start_page_app_count(state, config, rng, **_):
    correct = str(len(_START_PAGE_APPS))
    distractors = _nearby_integers(len(_START_PAGE_APPS), 10, rng)
    choices, correct_letter = _shuffle_choices(correct, distractors, rng)
    return [
        MCQuestion(
            question="How many app tiles are shown in the grid on the start page?",
            choices=choices,
            correct=correct_letter,
            category="element_counting",
            app="start_page",
        )
    ]


def start_page_app_names_positive(state, config, rng, **_):
    questions = []
    for app_name in _START_PAGE_APPS:
        distractors = rng.sample(_FAKE_APPS, 3)
        choices, correct_letter = _shuffle_choices(app_name, distractors, rng)
        questions.append(
            MCQuestion(
                question="Which of the following app names is shown on a tile on the start page?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="start_page",
            )
        )
    return questions


def start_page_not_an_app(state, config, rng, **_):
    questions = []
    for fake in _FAKE_APPS[:4]:
        real = rng.sample(_START_PAGE_APPS, 3)
        choices, correct_letter = _shuffle_choices(fake, real, rng)
        questions.append(
            MCQuestion(
                question="Which of the following app names is NOT shown on any tile on the start page?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="start_page",
                difficulty="medium",
            )
        )
    return questions


def start_page_headline(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Welcome to OpenApps!",
        ["My Apps", "App Dashboard", "Home"],
        rng,
    )
    return [
        MCQuestion(
            question="What headline text is displayed at the top of the start page?",
            choices=choices,
            correct=correct_letter,
            category="element_content",
            app="start_page",
        )
    ]


def start_page_tile_layout(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Colored rounded tiles arranged in a grid, each containing an icon and the app name",
        [
            "A vertical list of plain text links",
            "A horizontal carousel of screenshots",
            "A sidebar with collapsible sections",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="How are the apps presented visually on the start page?",
            choices=choices,
            correct=correct_letter,
            category="element_identification",
            app="start_page",
        )
    ]


def start_page_app_order(state, config, rng, **_):
    questions = []
    for i, app_name in enumerate(_START_PAGE_APPS):
        distractors = [a for a in _START_PAGE_APPS if a != app_name]
        rng.shuffle(distractors)
        choices, correct_letter = _shuffle_choices(app_name, distractors[:3], rng)
        questions.append(
            MCQuestion(
                question=f"What is the {_ordinal(i+1)} app tile shown on the start page (left-to-right, top-to-bottom)?",
                choices=choices,
                correct=correct_letter,
                category="element_content",
                app="start_page",
                difficulty="medium",
            )
        )
    return questions


def start_page_tile_interaction(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Select the colored tile with the desired app's icon and name",
        [
            "Use a search input at the top of the page",
            "Click a text-only link in a left sidebar",
            "Open a dropdown menu beneath the headline",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Which visible element would you use to open one of the apps from the start page?",
            choices=choices,
            correct=correct_letter,
            category="element_interaction",
            app="start_page",
        )
    ]


def start_page_tile_grid_location(state, config, rng, **_):
    choices, correct_letter = _shuffle_choices(
        "Below the welcome headline, in the center of the page",
        [
            "In a vertical sidebar on the left edge",
            "Inside a footer at the bottom of the page",
            "In a dropdown menu at the top-right",
        ],
        rng,
    )
    return [
        MCQuestion(
            question="Where is the grid of app tiles positioned on the start page?",
            choices=choices,
            correct=correct_letter,
            category="element_location",
            app="start_page",
        )
    ]


# ===========================================================================
# Template Registry
# ===========================================================================

ALL_TEMPLATES: dict[str, list] = {
    "todo": [
        todo_count_done,
        todo_count_not_done,
        todo_identify_done_items,
        todo_identify_not_done_items,
        todo_specific_item_state,
        todo_first_item,
        todo_items_at_positions,
        todo_item_neighbor,
        todo_element_type_for_completion,
        todo_controls_per_item,
        todo_add_button_interaction,
        todo_item_edit_interaction,
        todo_input_location,
        todo_return_button_location,
        todo_input_placeholder,
        todo_add_button_label,
        todo_app_title,
    ],
    "calendar": [
        calendar_displayed_month,
        calendar_count_events_in_month,
        calendar_event_exists_in_month,
        calendar_event_day_in_month,
        calendar_view_toggle,
        calendar_month_navigation,
        calendar_weekday_headers,
        calendar_app_title,
        calendar_add_event_button,
        calendar_return_button,
        calendar_add_event_interaction,
        calendar_next_month_interaction,
        calendar_view_toggle_location,
        calendar_footer_controls_location,
    ],
    "messenger": [
        messenger_count_conversations,
        messenger_contact_exists,
        messenger_contact_not_exists,
        messenger_group_chat_exists,
        messenger_app_title,
        messenger_return_button,
        messenger_open_chat_interaction,
        messenger_return_button_location,
    ],
    "map": [
        map_count_saved_places,
        map_place_exists,
        map_place_not_saved,
        map_delete_button,
        map_sidebar_location,
        map_search_interaction,
        map_return_button_location,
        map_tile_provider_location,
        map_search_placeholder,
        map_search_button_label,
        map_return_button_label,
        map_saved_locations_heading,
        map_current_location_info_heading,
        map_app_title,
        map_tile_provider_options,
    ],
    "codeeditor": [
        codeeditor_top_level_file_count,
        codeeditor_top_level_folder_count,
        codeeditor_top_level_file_exists,
        codeeditor_top_level_folder_exists,
        codeeditor_file_extension,
        codeeditor_sidebar_location,
        codeeditor_new_file_buttons,
        codeeditor_language_dropdown,
        codeeditor_theme_dropdown,
        codeeditor_app_title,
        codeeditor_no_file_selected_message,
        codeeditor_new_file_interaction,
        codeeditor_theme_interaction,
        codeeditor_main_pane_location,
        codeeditor_dropdowns_location,
    ],
    "start_page": [
        start_page_app_count,
        start_page_app_names_positive,
        start_page_not_an_app,
        start_page_headline,
        start_page_tile_layout,
        start_page_app_order,
        start_page_tile_interaction,
        start_page_tile_grid_location,
    ],
}
