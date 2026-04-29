"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Question templates for testing UI understanding.

Each template is a function that takes app state (from /*_all endpoints) and/or
Hydra config, and returns a list of MCQuestion instances. Templates are grouped
by the UI-understanding skill they test.
"""

from dataclasses import dataclass
import random


@dataclass
class MCQuestion:
    question: str
    choices: dict[str, str]  # {"A": "...", "B": "...", ...}
    correct: str  # "A", "B", "C", or "D"
    category: str
    app: str
    difficulty: str = "easy"  # easy, medium, hard

    def format_as_prompt(self) -> str:
        lines = [self.question]
        for key, value in self.choices.items():
            lines.append(f"  {key}) {value}")
        return "\n".join(lines)


def _shuffle_choices(
    correct: str, distractors: list[str], rng: random.Random
) -> tuple[dict[str, str], str]:
    """Shuffle correct answer among distractors, return (choices_dict, correct_letter)."""
    all_options = [correct] + distractors[:3]
    rng.shuffle(all_options)
    labels = ["A", "B", "C", "D"]
    choices = {labels[i]: all_options[i] for i in range(len(all_options))}
    correct_letter = [k for k, v in choices.items() if v == correct][0]
    return choices, correct_letter


def _nearby_integers(
    correct: int, upper_bound: int, rng: random.Random
) -> list[str]:
    """Generate 3 plausible but wrong integer alternatives."""
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


def _collect_files(node: dict) -> list[dict]:
    """Recursively collect all files from a codeeditor state tree."""
    files = []
    if node.get("type") == "file":
        files.append(node)
    for child in node.get("children", []):
        files.extend(_collect_files(child))
    return files


def _collect_folders(node: dict) -> list[dict]:
    """Recursively collect all folders from a codeeditor state tree."""
    folders = []
    if node.get("type") == "folder" and node.get("name"):
        folders.append(node)
    for child in node.get("children", []):
        folders.extend(_collect_folders(child))
    return folders


# ===========================================================================
# Todo App Templates
# ===========================================================================


def todo_count_done(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    todos = state["todo"]
    done_count = sum(1 for t in todos if t.get("done"))
    distractors = _nearby_integers(done_count, len(todos), rng)
    choices, correct_letter = _shuffle_choices(str(done_count), distractors, rng)
    return [
        MCQuestion(
            question="How many todo items are currently marked as done (checked off)?",
            choices=choices, correct=correct_letter,
            category="element_counting", app="todo",
        )
    ]


def todo_count_not_done(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    todos = state["todo"]
    not_done_count = sum(1 for t in todos if not t.get("done"))
    distractors = _nearby_integers(not_done_count, len(todos), rng)
    choices, correct_letter = _shuffle_choices(str(not_done_count), distractors, rng)
    return [
        MCQuestion(
            question="How many todo items are still incomplete (not checked off)?",
            choices=choices, correct=correct_letter,
            category="element_counting", app="todo",
        )
    ]


def todo_total_count(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    total = len(state["todo"])
    distractors = _nearby_integers(total, total + 5, rng)
    choices, correct_letter = _shuffle_choices(str(total), distractors, rng)
    return [
        MCQuestion(
            question="How many todo items are displayed in the list in total?",
            choices=choices, correct=correct_letter,
            category="element_counting", app="todo",
        )
    ]


def todo_identify_done_items(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Generate one question per done item: 'Which of these is done?'"""
    todos = state["todo"]
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
                question="Which of the following todo items is currently marked as done (checked)?",
                choices=choices, correct=correct_letter,
                category="element_state", app="todo",
            )
        )
    return questions


def todo_identify_not_done_items(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Generate one question per not-done item: 'Which of these is incomplete?'"""
    todos = state["todo"]
    done = [t["title"] for t in todos if t.get("done")]
    not_done = [t["title"] for t in todos if not t.get("done")]
    if len(not_done) < 1 or len(done) < 3:
        return []
    questions = []
    for title in not_done:
        distractors = rng.sample(done, min(3, len(done)))
        choices, correct_letter = _shuffle_choices(title, distractors, rng)
        questions.append(
            MCQuestion(
                question="Which of the following todo items is still incomplete (not checked)?",
                choices=choices, correct=correct_letter,
                category="element_state", app="todo",
            )
        )
    return questions


def todo_specific_item_state(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """For each item, ask whether it is done or not."""
    todos = state["todo"]
    questions = []
    for t in todos:
        is_done = bool(t.get("done"))
        correct = "Checked (done)" if is_done else "Unchecked (not done)"
        wrong = "Unchecked (not done)" if is_done else "Checked (done)"
        distractors = [wrong, "Grayed out (archived)", "Highlighted (in progress)"]
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(
            MCQuestion(
                question=f"What is the current state of the todo item '{t['title']}'?",
                choices=choices, correct=correct_letter,
                category="element_state", app="todo", difficulty="medium",
            )
        )
    return questions


def todo_first_item(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    todos = state["todo"]
    if len(todos) < 4:
        return []
    correct = todos[0]["title"]
    distractors = rng.sample([t["title"] for t in todos[1:]], 3)
    choices, correct_letter = _shuffle_choices(correct, distractors, rng)
    return [
        MCQuestion(
            question="What is the first todo item shown at the top of the list?",
            choices=choices, correct=correct_letter,
            category="element_content", app="todo",
        )
    ]


def todo_last_item(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    todos = state["todo"]
    if len(todos) < 4:
        return []
    correct = todos[-1]["title"]
    distractors = rng.sample([t["title"] for t in todos[:-1]], 3)
    choices, correct_letter = _shuffle_choices(correct, distractors, rng)
    return [
        MCQuestion(
            question="What is the last todo item shown at the bottom of the list?",
            choices=choices, correct=correct_letter,
            category="element_content", app="todo",
        )
    ]


def todo_items_at_positions(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Ask about items at positions 2 through min(10, len-1)."""
    todos = state["todo"]
    if len(todos) < 6:
        return []
    questions = []
    upper = min(10, len(todos))
    for pos in range(2, upper + 1):
        correct = todos[pos - 1]["title"]
        others = [t["title"] for i, t in enumerate(todos) if i != pos - 1]
        distractors = rng.sample(others, min(3, len(others)))
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(
            MCQuestion(
                question=f"What is the {_ordinal(pos)} todo item in the list?",
                choices=choices, correct=correct_letter,
                category="element_content", app="todo", difficulty="medium",
            )
        )
    return questions


def todo_item_neighbor(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """What item comes right after X?"""
    todos = state["todo"]
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
                question=f"Which todo item appears directly after '{current}' in the list?",
                choices=choices, correct=correct_letter,
                category="element_content", app="todo", difficulty="medium",
            )
        )
    return questions


def todo_item_not_in_list(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Generate multiple 'which does NOT appear' questions."""
    todos = state["todo"]
    titles = {t["title"] for t in todos}
    fake_items = [
        "Feed the cat", "Fix the sink", "Paint the fence", "Return library books",
        "Renew passport", "Schedule haircut", "Clean garage", "Buy birthday card",
        "Wash the dishes", "Iron clothes", "Mow the lawn", "Write a letter",
    ]
    fake_items = [f for f in fake_items if f not in titles]
    if len(fake_items) < 3 or len(todos) < 3:
        return []
    questions = []
    for fake in fake_items[:4]:
        real_items = rng.sample([t["title"] for t in todos], 3)
        choices, correct_letter = _shuffle_choices(fake, real_items, rng)
        questions.append(
            MCQuestion(
                question="Which of the following items does NOT appear in the todo list?",
                choices=choices, correct=correct_letter,
                category="element_content", app="todo", difficulty="medium",
            )
        )
    return questions


def todo_button_purpose(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Toggles the item between done and not done",
        ["Deletes the todo item", "Opens the item for editing", "Selects the item for bulk actions"],
        rng,
    )
    return [MCQuestion(
        question="What happens when you click the checkbox next to a todo item?",
        choices=choices, correct=correct_letter,
        category="element_interaction", app="todo",
    )]


def todo_edit_button_purpose(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Opens an inline edit form to change the item's title and status",
        ["Deletes the item after confirmation", "Marks the item as high priority", "Copies the item text to clipboard"],
        rng,
    )
    return [MCQuestion(
        question="What happens when you click the 'Edit' button on a todo item?",
        choices=choices, correct=correct_letter,
        category="element_interaction", app="todo",
    )]


def todo_remove_button_purpose(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Permanently removes the todo item from the list",
        ["Marks the item as done", "Moves the item to a trash folder", "Hides the item but keeps it in the database"],
        rng,
    )
    return [MCQuestion(
        question="What happens when you click the 'Remove' button on a todo item?",
        choices=choices, correct=correct_letter,
        category="element_interaction", app="todo",
    )]


def todo_element_type_for_completion(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Checkbox", ["Toggle switch", "Radio button", "Button labeled 'Done'"], rng,
    )
    return [MCQuestion(
        question="What type of UI element is used to mark a todo item as complete?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="todo",
    )]


def todo_controls_per_item(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Edit and Remove",
        ["Delete and Archive", "Save and Cancel", "Complete and Skip"],
        rng,
    )
    return [MCQuestion(
        question="What two action buttons appear next to each todo item?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="todo",
    )]


def todo_input_placeholder(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "New Todo", ["Add a task", "Enter todo here", "Type a new item"], rng,
    )
    return [MCQuestion(
        question="What placeholder text is shown in the input field for adding a new todo?",
        choices=choices, correct=correct_letter,
        category="element_content", app="todo",
    )]


def todo_add_button_label(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices("Add", ["Submit", "Create", "Save"], rng)
    return [MCQuestion(
        question="What is the label on the button used to add a new todo item?",
        choices=choices, correct=correct_letter,
        category="element_content", app="todo",
    )]


def todo_app_title(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    title = config.get("title", "OpenTodos")
    distractors = ["My Tasks", "Todo List", "Task Manager"]
    if title != "OpenTodos":
        distractors = ["OpenTodos"] + distractors[:2]
    choices, correct_letter = _shuffle_choices(title, distractors, rng)
    return [MCQuestion(
        question="What title is displayed at the top of the todo app page?",
        choices=choices, correct=correct_letter,
        category="element_content", app="todo",
    )]


def todo_nav_element(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A 'Return to List of Apps' link at the bottom",
        ["A home icon in the top navigation bar", "Clicking the app logo/title", "A back arrow button in the header"],
        rng,
    )
    return [MCQuestion(
        question="How do you navigate back to the main app list from the todo app?",
        choices=choices, correct=correct_letter,
        category="navigation", app="todo",
    )]


def todo_edit_form_elements(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A text input for the title, a 'Done' checkbox, and a 'Save' button",
        [
            "A text input for the title and a 'Submit' button",
            "A text area, a priority dropdown, and an 'Update' button",
            "A text input, a date picker, and a 'Confirm' button",
        ],
        rng,
    )
    return [MCQuestion(
        question="What elements appear in the edit form when you edit a todo item?",
        choices=choices, correct=correct_letter,
        category="form_structure", app="todo",
    )]


def todo_list_element_type(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "An unordered list (<ul>) with list items",
        ["A table with rows", "A grid of cards", "A series of <div> paragraphs"],
        rng,
    )
    return [MCQuestion(
        question="What HTML structure is used to display the list of todo items?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="todo", difficulty="hard",
    )]


# ===========================================================================
# Calendar App Templates
# ===========================================================================


def calendar_count_events(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    events = state["calendar"]
    total = len(events)
    distractors = _nearby_integers(total, total + 10, rng)
    choices, correct_letter = _shuffle_choices(str(total), distractors, rng)
    return [MCQuestion(
        question="How many events are stored in the calendar in total?",
        choices=choices, correct=correct_letter,
        category="element_counting", app="calendar",
    )]


def calendar_count_birthdays(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    events = state["calendar"]
    birthday_count = sum(1 for e in events if "birthday" in e.get("title", "").lower())
    if birthday_count == 0:
        return []
    distractors = _nearby_integers(birthday_count, birthday_count + 8, rng)
    choices, correct_letter = _shuffle_choices(str(birthday_count), distractors, rng)
    return [MCQuestion(
        question="How many birthday events are on the calendar?",
        choices=choices, correct=correct_letter,
        category="element_counting", app="calendar",
    )]


def calendar_count_conference_deadlines(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    events = state["calendar"]
    deadline_count = sum(
        1 for e in events
        if "deadline" in e.get("title", "").lower() or "paper" in e.get("title", "").lower()
    )
    if deadline_count == 0:
        return []
    distractors = _nearby_integers(deadline_count, deadline_count + 8, rng)
    choices, correct_letter = _shuffle_choices(str(deadline_count), distractors, rng)
    return [MCQuestion(
        question="How many conference deadline or paper submission events are on the calendar?",
        choices=choices, correct=correct_letter,
        category="element_counting", app="calendar",
    )]


def calendar_count_recurring(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    events = state["calendar"]
    recurring_count = sum(1 for e in events if e.get("recurring"))
    if recurring_count == 0:
        return []
    distractors = _nearby_integers(recurring_count, recurring_count + 8, rng)
    choices, correct_letter = _shuffle_choices(str(recurring_count), distractors, rng)
    return [MCQuestion(
        question="How many events on the calendar are set to recur (yearly, monthly, or weekly)?",
        choices=choices, correct=correct_letter,
        category="element_counting", app="calendar",
    )]


def calendar_event_dates(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Ask for the date of multiple specific events."""
    events = state["calendar"]
    events_with_dates = [e for e in events if e.get("date")]
    if len(events_with_dates) < 6:
        return []
    all_dates = list({e["date"] for e in events_with_dates})
    rng.shuffle(events_with_dates)
    questions = []
    for target in events_with_dates[:8]:
        correct = target["date"]
        other_dates = [d for d in all_dates if d != correct]
        if len(other_dates) < 3:
            continue
        distractors = rng.sample(other_dates, 3)
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(MCQuestion(
            question=f"What date is the event '{target['title']}' scheduled for?",
            choices=choices, correct=correct_letter,
            category="element_content", app="calendar", difficulty="medium",
        ))
    return questions


def calendar_event_is_recurring(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """For specific events, ask whether they recur."""
    events = state["calendar"]
    recurring = [e for e in events if e.get("recurring")]
    non_recurring = [e for e in events if not e.get("recurring")]
    if len(recurring) < 2 or len(non_recurring) < 2:
        return []
    questions = []
    samples = rng.sample(recurring, min(5, len(recurring))) + rng.sample(non_recurring, min(5, len(non_recurring)))
    rng.shuffle(samples)
    for event in samples:
        is_rec = bool(event.get("recurring"))
        correct = f"Yes, it recurs {event['recurring']}" if is_rec else "No, it is a one-time event"
        distractors = [
            "No, it is a one-time event" if is_rec else "Yes, it recurs yearly",
            "Yes, it recurs weekly",
            "Yes, it recurs monthly",
        ]
        if is_rec:
            distractors = [d for d in distractors if event["recurring"] not in d][:3]
            if len(distractors) < 3:
                distractors.append("No, it is a one-time event")
        choices, correct_letter = _shuffle_choices(correct, distractors[:3], rng)
        questions.append(MCQuestion(
            question=f"Is the event '{event['title']}' a recurring event?",
            choices=choices, correct=correct_letter,
            category="element_state", app="calendar", difficulty="medium",
        ))
    return questions


def calendar_event_location(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Ask for the location of specific events."""
    events = state["calendar"]
    with_location = [e for e in events if e.get("location")]
    if len(with_location) < 4:
        return []
    questions = []
    rng.shuffle(with_location)
    for event in with_location[:6]:
        correct = event["location"]
        fake_locations = ["Room 301", "Virtual", "New York City", "Conference Hall B", "Online", "Library"]
        fake_locations = [f for f in fake_locations if f != correct]
        distractors = rng.sample(fake_locations, min(3, len(fake_locations)))
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(MCQuestion(
            question=f"What is the location listed for the event '{event['title']}'?",
            choices=choices, correct=correct_letter,
            category="element_content", app="calendar",
        ))
    return questions


def calendar_event_exists_multi(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Multiple 'which event appears on the calendar' questions."""
    events = state["calendar"]
    if len(events) < 4:
        return []
    event_titles = [e["title"] for e in events]
    fake_events = [
        "NeurIPS 2026 Deadline", "Team Standup Meeting", "Dentist Appointment",
        "ICML 2026 Workshop", "Mom's Anniversary", "Sprint Planning",
        "Yoga Class", "Guitar Lesson", "Board Meeting",
    ]
    fake_events = [f for f in fake_events if f not in event_titles]
    questions = []
    rng.shuffle(event_titles)
    for correct in event_titles[:6]:
        distractors = rng.sample(fake_events, min(3, len(fake_events)))
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(MCQuestion(
            question="Which of the following events appears on the calendar?",
            choices=choices, correct=correct_letter,
            category="element_content", app="calendar",
        ))
    return questions


def calendar_event_not_exists(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Which event does NOT appear on the calendar?"""
    events = state["calendar"]
    event_titles = [e["title"] for e in events]
    fake_events = [
        "NeurIPS 2026 Deadline", "Team Standup Meeting", "Dentist Appointment",
        "ICML 2026 Workshop", "Sprint Retrospective", "Board Meeting",
    ]
    fake_events = [f for f in fake_events if f not in event_titles]
    if len(fake_events) < 2 or len(event_titles) < 3:
        return []
    questions = []
    for fake in fake_events[:4]:
        real = rng.sample(event_titles, 3)
        choices, correct_letter = _shuffle_choices(fake, real, rng)
        questions.append(MCQuestion(
            question="Which of the following events does NOT appear on the calendar?",
            choices=choices, correct=correct_letter,
            category="element_content", app="calendar", difficulty="medium",
        ))
    return questions


def calendar_birthday_month(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Whose birthday is in month X?"""
    events = state["calendar"]
    birthdays = [e for e in events if "birthday" in e.get("title", "").lower() and e.get("date")]
    if len(birthdays) < 4:
        return []
    import calendar as cal_mod
    questions = []
    rng.shuffle(birthdays)
    for bday in birthdays[:6]:
        month_num = int(bday["date"].split("-")[1])
        month_name = cal_mod.month_name[month_num]
        name = bday["title"].replace("'s Birthday", "")
        correct = month_name
        other_months = [cal_mod.month_name[m] for m in range(1, 13) if m != month_num]
        distractors = rng.sample(other_months, 3)
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(MCQuestion(
            question=f"In what month is {name}'s birthday?",
            choices=choices, correct=correct_letter,
            category="element_content", app="calendar", difficulty="medium",
        ))
    return questions


def calendar_whose_birthday(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Given a date, whose birthday is it?"""
    events = state["calendar"]
    birthdays = [e for e in events if "birthday" in e.get("title", "").lower()]
    if len(birthdays) < 4:
        return []
    names = [e["title"].replace("'s Birthday", "") for e in birthdays]
    questions = []
    rng.shuffle(birthdays)
    for bday in birthdays[:8]:
        name = bday["title"].replace("'s Birthday", "")
        date = bday.get("date", "")
        others = [n for n in names if n != name]
        if len(others) < 3:
            continue
        distractors = rng.sample(others, 3)
        choices, correct_letter = _shuffle_choices(name, distractors, rng)
        questions.append(MCQuestion(
            question=f"Whose birthday is on {date}?",
            choices=choices, correct=correct_letter,
            category="element_content", app="calendar", difficulty="medium",
        ))
    return questions


def calendar_recurring_type(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A dropdown/select menu with options like Weekly, Monthly, Yearly",
        ["Radio buttons for each recurrence type", "A text input where you type the recurrence", "Checkboxes for each day of the week"],
        rng,
    )
    return [MCQuestion(
        question="What type of UI element is used to choose how often a calendar event recurs?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="calendar",
    )]


def calendar_view_toggle(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Calendar (grid) and Agenda (list)",
        ["Day and Week views", "Month and Year views", "Timeline and Board views"],
        rng,
    )
    return [MCQuestion(
        question="What two view modes can you toggle between on the calendar page?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="calendar",
    )]


def calendar_create_event_fields(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Title, Date, Description, URL, Invitees, Location, Recurring",
        [
            "Title, Date, Time, Priority, Color",
            "Title, Start Date, End Date, All Day, Reminders",
            "Title, Date, Category, Assignee, Status",
        ],
        rng,
    )
    return [MCQuestion(
        question="What fields are available in the form to create a new calendar event?",
        choices=choices, correct=correct_letter,
        category="form_structure", app="calendar",
    )]


def calendar_month_navigation(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "'< Prev' and 'Next >' buttons flanking the month name",
        ["A dropdown to select the month", "Left and right arrow keys", "Swiping gestures on the calendar grid"],
        rng,
    )
    return [MCQuestion(
        question="How do you navigate to a different month on the calendar?",
        choices=choices, correct=correct_letter,
        category="navigation", app="calendar",
    )]


def calendar_delete_event_element(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A 'Delete Event' button on the event detail page",
        ["Right-clicking the event and selecting 'Delete'", "Dragging the event to a trash icon", "A swipe-to-delete gesture on the event"],
        rng,
    )
    return [MCQuestion(
        question="How do you delete an event from the calendar?",
        choices=choices, correct=correct_letter,
        category="element_interaction", app="calendar",
    )]


def calendar_date_input_type(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A text input with placeholder 'YYYY-MM-DD'",
        ["A date picker calendar widget", "A set of three dropdowns (year, month, day)", "A date/time combined picker"],
        rng,
    )
    return [MCQuestion(
        question="How is the event date entered in the 'Create New Event' form?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="calendar",
    )]


def calendar_weekday_headers(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Mon, Tue, Wed, Thu, Fri, Sat, Sun",
        ["Sunday through Saturday", "M, T, W, T, F, S, S", "Monday through Friday only"],
        rng,
    )
    return [MCQuestion(
        question="What column headers appear across the top of the calendar grid?",
        choices=choices, correct=correct_letter,
        category="element_content", app="calendar",
    )]


def calendar_add_event_location(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A button at the bottom of the calendar page that opens a new tab",
        ["A '+' floating action button in the corner", "A menu item in a hamburger menu", "Double-clicking on a calendar day cell"],
        rng,
    )
    return [MCQuestion(
        question="How do you access the form to add a new calendar event?",
        choices=choices, correct=correct_letter,
        category="navigation", app="calendar",
    )]


# ===========================================================================
# Messenger App Templates
# ===========================================================================


def messenger_count_conversations(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    conversations = state["messenger"]
    total = len(conversations)
    distractors = _nearby_integers(total, total + 4, rng)
    choices, correct_letter = _shuffle_choices(str(total), distractors, rng)
    return [MCQuestion(
        question="How many conversations are shown in the messenger app?",
        choices=choices, correct=correct_letter,
        category="element_counting", app="messenger",
    )]


def messenger_contact_exists_multi(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """One question per contact: does this contact appear?"""
    conversations = state["messenger"]
    if len(conversations) < 2:
        return []
    contact_names = [c["user"] for c in conversations]
    fake_contacts = ["Diana", "Eve", "Frank", "Grace", "Henry", "Ivan", "Julia"]
    fake_contacts = [f for f in fake_contacts if f not in contact_names]
    questions = []
    for name in contact_names:
        distractors = rng.sample(fake_contacts, min(3, len(fake_contacts)))
        choices, correct_letter = _shuffle_choices(name, distractors, rng)
        questions.append(MCQuestion(
            question="Which of the following contacts appears in the messenger?",
            choices=choices, correct=correct_letter,
            category="element_content", app="messenger",
        ))
    return questions


def messenger_contact_not_exists(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    conversations = state["messenger"]
    contact_names = [c["user"] for c in conversations]
    fake_contacts = ["Diana", "Eve", "Frank", "Grace", "Henry"]
    fake_contacts = [f for f in fake_contacts if f not in contact_names]
    if len(fake_contacts) < 3 or len(contact_names) < 3:
        return []
    questions = []
    for fake in fake_contacts[:3]:
        real = rng.sample(contact_names, min(3, len(contact_names)))
        choices, correct_letter = _shuffle_choices(fake, real, rng)
        questions.append(MCQuestion(
            question="Which of the following contacts does NOT appear in the messenger?",
            choices=choices, correct=correct_letter,
            category="element_content", app="messenger", difficulty="medium",
        ))
    return questions


def messenger_message_count_per_contact(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """One question per conversation: how many messages?"""
    conversations = state["messenger"]
    questions = []
    for conv in conversations:
        msg_count = len(conv["messages"])
        distractors = _nearby_integers(msg_count, msg_count + 5, rng)
        choices, correct_letter = _shuffle_choices(str(msg_count), distractors, rng)
        questions.append(MCQuestion(
            question=f"How many messages are in the conversation with {conv['user']}?",
            choices=choices, correct=correct_letter,
            category="element_counting", app="messenger", difficulty="medium",
        ))
    return questions


def messenger_first_message(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """What is the first message in each conversation?"""
    conversations = state["messenger"]
    questions = []
    for conv in conversations:
        if not conv["messages"]:
            continue
        first_msg = conv["messages"][0][0]
        if len(first_msg) > 70:
            first_msg = first_msg[:67] + "..."
        other_msgs = [m[0][:67] + "..." if len(m[0]) > 70 else m[0] for m in conv["messages"][1:]]
        if len(other_msgs) < 3:
            continue
        distractors = rng.sample(other_msgs, 3)
        choices, correct_letter = _shuffle_choices(first_msg, distractors, rng)
        questions.append(MCQuestion(
            question=f"What is the first (oldest) message in the conversation with {conv['user']}?",
            choices=choices, correct=correct_letter,
            category="element_content", app="messenger", difficulty="medium",
        ))
    return questions


def messenger_last_message(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """What is the last message in each conversation?"""
    conversations = state["messenger"]
    questions = []
    for conv in conversations:
        if not conv["messages"]:
            continue
        last_msg = conv["messages"][-1][0]
        if len(last_msg) > 70:
            last_msg = last_msg[:67] + "..."
        other_msgs = [m[0][:67] + "..." if len(m[0]) > 70 else m[0] for m in conv["messages"][:-1]]
        if len(other_msgs) < 3:
            continue
        distractors = rng.sample(other_msgs, 3)
        choices, correct_letter = _shuffle_choices(last_msg, distractors, rng)
        questions.append(MCQuestion(
            question=f"What is the most recent message in the conversation with {conv['user']}?",
            choices=choices, correct=correct_letter,
            category="element_content", app="messenger", difficulty="medium",
        ))
    return questions


def messenger_who_sent_last(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Who sent the last message in each conversation?"""
    conversations = state["messenger"]
    all_senders = set()
    for conv in conversations:
        for msg in conv["messages"]:
            all_senders.add(msg[1])
    questions = []
    for conv in conversations:
        if not conv["messages"]:
            continue
        correct = conv["messages"][-1][1]
        others = [s for s in all_senders if s != correct]
        if len(others) < 3:
            others += ["Diana", "Eve", "Frank"]
        distractors = rng.sample(list(set(others))[:6], min(3, len(others)))
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(MCQuestion(
            question=f"Who sent the most recent message in the conversation with {conv['user']}?",
            choices=choices, correct=correct_letter,
            category="element_content", app="messenger",
        ))
    return questions


def messenger_send_button_icon(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A paper plane icon",
        ["An arrow pointing right", "A checkmark icon", "The word 'Send'"],
        rng,
    )
    return [MCQuestion(
        question="What icon or label is on the button to send a message in the messenger?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="messenger",
    )]


def messenger_search_feature(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A magnifying glass icon button in the chat header that reveals a search bar",
        ["A search bar always visible at the top of the chat", "A keyboard shortcut (Ctrl+F)", "There is no search feature in conversations"],
        rng,
    )
    return [MCQuestion(
        question="How do you search for messages within a conversation?",
        choices=choices, correct=correct_letter,
        category="element_interaction", app="messenger",
    )]


def messenger_group_chat_exists(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    conversations = state["messenger"]
    group_chats = [c for c in conversations if "group" in c["user"].lower()]
    has_group = len(group_chats) > 0
    if has_group:
        correct = f"Yes — '{group_chats[0]['user']}'"
        distractors = ["No, only individual conversations", "Yes — 'Team Chat'", "Yes — 'General Channel'"]
    else:
        correct = "No, only individual conversations"
        distractors = ["Yes — 'Fantastic4GroupChat'", "Yes — 'Team Chat'", "Yes — 'General Channel'"]
    choices, correct_letter = _shuffle_choices(correct, distractors, rng)
    return [MCQuestion(
        question="Is there a group chat visible in the messenger?",
        choices=choices, correct=correct_letter,
        category="element_content", app="messenger",
    )]


def messenger_back_button(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A left-pointing arrow icon in the top-left of the chat header",
        ["A 'Back' text link below the messages", "The browser back button is the only option", "A hamburger menu with a 'Back' option"],
        rng,
    )
    return [MCQuestion(
        question="How do you navigate from a chat back to the conversation list in the messenger?",
        choices=choices, correct=correct_letter,
        category="navigation", app="messenger",
    )]


def messenger_input_placeholder(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Type a message", ["Write something...", "Enter message", "Say something..."],
        rng,
    )
    return [MCQuestion(
        question="What placeholder text appears in the message input field?",
        choices=choices, correct=correct_letter,
        category="element_content", app="messenger",
    )]


def messenger_who_sent_first(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Who sent the first message in each conversation?"""
    conversations = state["messenger"]
    all_senders = set()
    for conv in conversations:
        for msg in conv["messages"]:
            all_senders.add(msg[1])
    questions = []
    for conv in conversations:
        if not conv["messages"]:
            continue
        correct = conv["messages"][0][1]
        others = [s for s in all_senders if s != correct]
        if len(others) < 3:
            others += ["Diana", "Eve", "Frank"]
        distractors = rng.sample(list(set(others))[:6], min(3, len(others)))
        choices, correct_letter = _shuffle_choices(correct, distractors, rng)
        questions.append(MCQuestion(
            question=f"Who sent the first message in the conversation with {conv['user']}?",
            choices=choices, correct=correct_letter,
            category="element_content", app="messenger",
        ))
    return questions


def messenger_total_message_count(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    conversations = state["messenger"]
    total = sum(len(c["messages"]) for c in conversations)
    if total == 0:
        return []
    distractors = _nearby_integers(total, total + 8, rng)
    choices, correct_letter = _shuffle_choices(str(total), distractors, rng)
    return [MCQuestion(
        question="How many messages are there across all conversations in the messenger?",
        choices=choices, correct=correct_letter,
        category="element_counting", app="messenger",
    )]


def messenger_conversation_with_most_messages(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    conversations = state["messenger"]
    if len(conversations) < 3:
        return []
    sorted_convs = sorted(conversations, key=lambda c: len(c["messages"]), reverse=True)
    correct = sorted_convs[0]["user"]
    distractors = [c["user"] for c in sorted_convs[1:4]]
    if len(distractors) < 3:
        return []
    choices, correct_letter = _shuffle_choices(correct, distractors, rng)
    return [MCQuestion(
        question="Which conversation has the most messages?",
        choices=choices, correct=correct_letter,
        category="element_content", app="messenger", difficulty="medium",
    )]


def messenger_chat_bubble_alignment(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Sent messages appear on the right; received messages on the left",
        [
            "All messages are left-aligned",
            "Sent messages are on the left; received on the right",
            "Messages alternate sides regardless of sender",
        ],
        rng,
    )
    return [MCQuestion(
        question="How are sent vs. received messages visually distinguished in the chat?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="messenger",
    )]


# ===========================================================================
# Map App Templates
# ===========================================================================


def map_count_saved_places(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    places = state["map"]
    total = len(places)
    distractors = _nearby_integers(total, total + 5, rng)
    choices, correct_letter = _shuffle_choices(str(total), distractors, rng)
    return [MCQuestion(
        question="How many saved locations (bookmarked places) are shown on the map?",
        choices=choices, correct=correct_letter,
        category="element_counting", app="map",
    )]


def map_place_exists_multi(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """One question per saved place."""
    places = state["map"]
    if len(places) < 2:
        return []
    place_names = [p["name"] for p in places]
    fake_places = [
        "Golden Gate Bridge", "Eiffel Tower", "Buckingham Palace",
        "Sydney Opera House", "Big Ben", "Colosseum", "Taj Mahal",
    ]
    fake_places = [f for f in fake_places if f not in place_names]
    questions = []
    for name in place_names:
        distractors = rng.sample(fake_places, min(3, len(fake_places)))
        choices, correct_letter = _shuffle_choices(name, distractors, rng)
        questions.append(MCQuestion(
            question="Which of the following is a saved location on the map?",
            choices=choices, correct=correct_letter,
            category="element_content", app="map",
        ))
    return questions


def map_place_not_saved(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    places = state["map"]
    place_names = [p["name"] for p in places]
    fake_places = [
        "Golden Gate Bridge", "Eiffel Tower", "Buckingham Palace",
        "Sydney Opera House", "Big Ben", "Colosseum",
    ]
    fake_places = [f for f in fake_places if f not in place_names]
    if len(fake_places) < 3 or len(place_names) < 3:
        return []
    questions = []
    for fake in fake_places[:3]:
        real = rng.sample(place_names, 3)
        choices, correct_letter = _shuffle_choices(fake, real, rng)
        questions.append(MCQuestion(
            question="Which of the following locations is NOT saved on the map?",
            choices=choices, correct=correct_letter,
            category="element_content", app="map", difficulty="medium",
        ))
    return questions


def map_delete_location_element(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "An 'x' button next to the location name in the sidebar list",
        ["Right-clicking the map marker and selecting 'Remove'", "Dragging the marker off the map", "A 'Delete All' button at the bottom of the sidebar"],
        rng,
    )
    return [MCQuestion(
        question="How do you delete a saved location from the map?",
        choices=choices, correct=correct_letter,
        category="element_interaction", app="map",
    )]


def map_sidebar_location(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "On the right side of the page, next to the map",
        ["On the left side of the page", "At the bottom of the page, below the map", "In a floating overlay on top of the map"],
        rng,
    )
    return [MCQuestion(
        question="Where is the sidebar that shows saved locations and search?",
        choices=choices, correct=correct_letter,
        category="element_location", app="map",
    )]


def map_search_element(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A text input with placeholder 'Search location...' and a 'Search' button",
        ["A search icon that expands into an overlay search bar", "A dropdown with predefined locations to choose from", "A voice-activated search with a microphone icon"],
        rng,
    )
    return [MCQuestion(
        question="How is the search feature presented in the map app?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="map",
    )]


def map_save_location_interaction(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A bookmark icon button next to each search result",
        ["A 'Save' button that appears when you click on the map", "Dragging a pin onto the map from a toolbar", "A right-click context menu on the map"],
        rng,
    )
    return [MCQuestion(
        question="How do you save a new location from search results in the map app?",
        choices=choices, correct=correct_letter,
        category="element_interaction", app="map",
    )]


def map_marker_customization(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A modal dialog that lets you choose an icon and a color",
        ["You cannot customize markers", "A color picker tooltip on the marker itself", "A settings page accessible from the sidebar"],
        rng,
    )
    return [MCQuestion(
        question="How can you customize the appearance of a map marker?",
        choices=choices, correct=correct_letter,
        category="element_interaction", app="map",
    )]


def map_click_info(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "The 'Current Location Info' box shows the latitude, longitude, and address",
        ["Nothing happens when you click the map", "A tooltip with the place name pops up", "The map zooms in to that location"],
        rng,
    )
    return [MCQuestion(
        question="What happens when you click on a spot on the map?",
        choices=choices, correct=correct_letter,
        category="element_interaction", app="map",
    )]


def map_return_button_label(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Return to List of Apps",
        ["Back to Home", "Go Home", "Main Menu"],
        rng,
    )
    return [MCQuestion(
        question="What is the label on the button that navigates back to the start page from the map app?",
        choices=choices, correct=correct_letter,
        category="element_content", app="map",
    )]


def map_saved_locations_heading(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Saved Locations",
        ["My Bookmarks", "Favorite Places", "Pinned Locations"],
        rng,
    )
    return [MCQuestion(
        question="What heading appears above the list of saved places in the map sidebar?",
        choices=choices, correct=correct_letter,
        category="element_content", app="map",
    )]


def map_current_location_info(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Current Location Info",
        ["Selected Point Details", "Location Data", "Coordinates"],
        rng,
    )
    return [MCQuestion(
        question="What heading labels the section that displays information about a clicked map location?",
        choices=choices, correct=correct_letter,
        category="element_content", app="map",
    )]


def map_tile_provider(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "OpenStreetMap (by default)",
        ["Google Maps", "Apple Maps", "Mapbox"],
        rng,
    )
    return [MCQuestion(
        question="What map tile provider does the map app use by default?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="map",
    )]


def map_width_ratio(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "The map takes about 80% width and the sidebar about 20%",
        ["The map and sidebar are equal width (50/50)", "The sidebar is wider than the map", "The map is full width with a collapsible sidebar overlay"],
        rng,
    )
    return [MCQuestion(
        question="What is the approximate width split between the map and the sidebar?",
        choices=choices, correct=correct_letter,
        category="element_location", app="map", difficulty="hard",
    )]


# ===========================================================================
# Code Editor App Templates
# ===========================================================================


def codeeditor_file_count(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    files = _collect_files(state.get("codeeditor", {}))
    total = len(files)
    if total == 0:
        return []
    distractors = _nearby_integers(total, total + 5, rng)
    choices, correct_letter = _shuffle_choices(str(total), distractors, rng)
    return [MCQuestion(
        question="How many files are in the code editor's file tree?",
        choices=choices, correct=correct_letter,
        category="element_counting", app="codeeditor",
    )]


def codeeditor_folder_count(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    folders = _collect_folders(state.get("codeeditor", {}))
    total = len(folders)
    if total == 0:
        return []
    distractors = _nearby_integers(total, total + 4, rng)
    choices, correct_letter = _shuffle_choices(str(total), distractors, rng)
    return [MCQuestion(
        question="How many folders are in the code editor's file tree?",
        choices=choices, correct=correct_letter,
        category="element_counting", app="codeeditor",
    )]


def codeeditor_file_exists(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Which files exist in the editor?"""
    files = _collect_files(state.get("codeeditor", {}))
    if len(files) < 3:
        return []
    file_names = [f["name"] for f in files]
    fake_files = ["index.html", "main.go", "README.md", "app.js", "config.yaml", "Makefile", "test.py"]
    fake_files = [f for f in fake_files if f not in file_names]
    questions = []
    for name in file_names:
        distractors = rng.sample(fake_files, min(3, len(fake_files)))
        choices, correct_letter = _shuffle_choices(name, distractors, rng)
        questions.append(MCQuestion(
            question="Which of the following files exists in the code editor?",
            choices=choices, correct=correct_letter,
            category="element_content", app="codeeditor",
        ))
    return questions


def codeeditor_file_not_exists(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    files = _collect_files(state.get("codeeditor", {}))
    file_names = [f["name"] for f in files]
    fake_files = ["index.html", "main.go", "README.md", "app.js", "config.yaml", "Makefile"]
    fake_files = [f for f in fake_files if f not in file_names]
    if len(fake_files) < 2 or len(file_names) < 3:
        return []
    questions = []
    for fake in fake_files[:3]:
        real = rng.sample(file_names, min(3, len(file_names)))
        choices, correct_letter = _shuffle_choices(fake, real, rng)
        questions.append(MCQuestion(
            question="Which of the following files does NOT exist in the code editor?",
            choices=choices, correct=correct_letter,
            category="element_content", app="codeeditor", difficulty="medium",
        ))
    return questions


def codeeditor_folder_exists(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    folders = _collect_folders(state.get("codeeditor", {}))
    if len(folders) < 2:
        return []
    folder_names = [f["name"] for f in folders]
    fake_folders = ["src", "lib", "tests", "docs", "build", "dist", "assets"]
    fake_folders = [f for f in fake_folders if f not in folder_names]
    questions = []
    for name in folder_names:
        distractors = rng.sample(fake_folders, min(3, len(fake_folders)))
        choices, correct_letter = _shuffle_choices(name, distractors, rng)
        questions.append(MCQuestion(
            question="Which of the following folders exists in the code editor's file tree?",
            choices=choices, correct=correct_letter,
            category="element_content", app="codeeditor",
        ))
    return questions


def codeeditor_file_content(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """What does a specific file contain?"""
    files = _collect_files(state.get("codeeditor", {}))
    if len(files) < 3:
        return []
    questions = []
    rng.shuffle(files)
    for f in files[:4]:
        content = f.get("content", "").strip()
        if not content:
            continue
        snippet = content.split("\n")[0][:80]
        other_files = [of for of in files if of["name"] != f["name"] and of.get("content", "").strip()]
        if len(other_files) < 3:
            continue
        fake_snippets = [of.get("content", "").strip().split("\n")[0][:80] for of in rng.sample(other_files, 3)]
        choices, correct_letter = _shuffle_choices(snippet, fake_snippets, rng)
        questions.append(MCQuestion(
            question=f"What is the first line of code in the file '{f['name']}'?",
            choices=choices, correct=correct_letter,
            category="element_content", app="codeeditor", difficulty="medium",
        ))
    return questions


def codeeditor_file_extension(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """What language/type is each file based on its extension?"""
    files = _collect_files(state.get("codeeditor", {}))
    if len(files) < 3:
        return []
    ext_to_lang = {
        ".py": "Python", ".c": "C", ".js": "JavaScript", ".css": "CSS",
        ".yaml": "YAML", ".md": "Markdown", ".go": "Go", ".html": "HTML",
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
        questions.append(MCQuestion(
            question=f"Based on its file extension, what programming language is '{name}'?",
            choices=choices, correct=correct_letter,
            category="element_content", app="codeeditor",
        ))
    return questions


def codeeditor_empty_folder(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Which folders are empty?"""
    folders = _collect_folders(state.get("codeeditor", {}))
    empty = [f["name"] for f in folders if len(f.get("children", [])) == 0]
    non_empty = [f["name"] for f in folders if len(f.get("children", [])) > 0]
    if not empty or len(non_empty) < 2:
        return []
    questions = []
    for name in empty:
        fake_folders = ["src", "lib", "tests", "docs"]
        distractors = non_empty + [f for f in fake_folders if f not in [name] + non_empty]
        rng.shuffle(distractors)
        choices, correct_letter = _shuffle_choices(name, distractors[:3], rng)
        questions.append(MCQuestion(
            question="Which of the following folders in the code editor is empty (contains no files)?",
            choices=choices, correct=correct_letter,
            category="element_state", app="codeeditor", difficulty="medium",
        ))
    return questions


def codeeditor_sidebar_location(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A file tree on the left side, taking about 1/6 of the page width",
        ["A file list across the top of the page", "A right-side panel taking half the width", "A floating file browser overlay in the center"],
        rng,
    )
    return [MCQuestion(
        question="Where is the file browser located in the code editor?",
        choices=choices, correct=correct_letter,
        category="element_location", app="codeeditor",
    )]


def codeeditor_language_selector(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A dropdown/select menu with language options like Python, JavaScript, CSS",
        ["Language is auto-detected from the file extension with no manual control", "Radio buttons for each language", "A text input where you type the language name"],
        rng,
    )
    return [MCQuestion(
        question="How do you change the syntax highlighting language in the code editor?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="codeeditor",
    )]


def codeeditor_theme_selector(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "A dropdown/select menu with themes like monokai, eclipse, solarized, dracula",
        ["A toggle between light and dark mode only", "A color picker for custom theming", "There is no theme selection"],
        rng,
    )
    return [MCQuestion(
        question="How do you change the editor color theme in the code editor?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="codeeditor",
    )]


def codeeditor_new_file_button(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Two buttons in the sidebar: 'New File' and 'New Folder'",
        ["A single '+' button that opens a menu", "Right-clicking in the file tree", "A menu bar option under 'File'"],
        rng,
    )
    return [MCQuestion(
        question="How do you create a new file or folder in the code editor?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="codeeditor",
    )]


def codeeditor_rename_interaction(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Double-clicking on the file name in the header area",
        ["Right-clicking the file and selecting 'Rename'", "A dedicated 'Rename' button in the toolbar", "You cannot rename files"],
        rng,
    )
    return [MCQuestion(
        question="How do you rename a file in the code editor?",
        choices=choices, correct=correct_letter,
        category="element_interaction", app="codeeditor",
    )]


def codeeditor_action_buttons(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Save and Delete buttons",
        ["Save, Run, and Debug buttons", "Save, Undo, and Redo buttons", "Only a Save button"],
        rng,
    )
    return [MCQuestion(
        question="What action buttons appear when viewing a file in the code editor?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="codeeditor",
    )]


def codeeditor_folder_collapse(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Click the triangle/arrow icon next to the folder name to expand or collapse it",
        ["Double-click the folder name", "Hover over the folder to auto-expand", "Folders are always fully expanded"],
        rng,
    )
    return [MCQuestion(
        question="How do you expand or collapse a folder in the code editor's file tree?",
        choices=choices, correct=correct_letter,
        category="element_interaction", app="codeeditor",
    )]


def codeeditor_tab_behavior(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Open files appear as tabs in a tab bar above the editor with close ('x') buttons",
        ["There is only one editor view with no tabs", "Files open in separate browser tabs", "A split-pane view shows multiple files side by side"],
        rng,
    )
    return [MCQuestion(
        question="How are multiple open files managed in the code editor?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="codeeditor",
    )]


# ===========================================================================
# Start Page Templates
# ===========================================================================


def start_page_app_count(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices("6", ["4", "5", "8"], rng)
    return [MCQuestion(
        question="How many app tiles are displayed on the start (home) page?",
        choices=choices, correct=correct_letter,
        category="element_counting", app="start_page",
    )]


def start_page_app_names(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "OpenNotes", ["OpenTodos", "OpenCalendar", "OpenMessages"], rng,
    )
    return [MCQuestion(
        question="Which of the following is NOT an app shown on the start page?",
        choices=choices, correct=correct_letter,
        category="element_content", app="start_page",
    )]


def start_page_app_names_positive(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Generate a question per real app name."""
    real_apps = ["OpenTodos", "OpenCalendar", "OpenMessages", "OpenMaps", "OpenCodeEditor", "OpenShop"]
    fake_apps = ["OpenNotes", "OpenWeather", "OpenMusic", "OpenFitness", "OpenBanking", "OpenTravel"]
    questions = []
    for app_name in real_apps:
        distractors = rng.sample(fake_apps, 3)
        choices, correct_letter = _shuffle_choices(app_name, distractors, rng)
        questions.append(MCQuestion(
            question="Which of the following is an app available on the start page?",
            choices=choices, correct=correct_letter,
            category="element_content", app="start_page",
        ))
    return questions


def start_page_headline(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Welcome to OpenApps!", ["My Apps", "App Dashboard", "Home"], rng,
    )
    return [MCQuestion(
        question="What headline text is displayed on the start page?",
        choices=choices, correct=correct_letter,
        category="element_content", app="start_page",
    )]


def start_page_tile_layout(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Colored tiles/cards with an icon, title, and description in a grid",
        ["A plain text list of links", "A sidebar navigation menu", "A horizontal scrolling carousel of screenshots"],
        rng,
    )
    return [MCQuestion(
        question="How are apps visually presented on the start page?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="start_page",
    )]


def start_page_tile_contents(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "An icon image, the app title, and a description",
        ["Only the app title as a text link", "A screenshot of the app and its title", "The app title and a star rating"],
        rng,
    )
    return [MCQuestion(
        question="What information is shown on each app tile on the start page?",
        choices=choices, correct=correct_letter,
        category="element_content", app="start_page",
    )]


def start_page_app_order(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """What is the first/second/third app tile on the start page?"""
    app_order = ["OpenTodos", "OpenCalendar", "OpenMessages", "OpenMaps", "OpenCodeEditor", "OpenShop"]
    fake_apps = ["OpenNotes", "OpenWeather", "OpenMusic", "OpenFitness"]
    questions = []
    for i, app_name in enumerate(app_order):
        distractors = [a for a in app_order if a != app_name]
        rng.shuffle(distractors)
        choices, correct_letter = _shuffle_choices(app_name, distractors[:3], rng)
        questions.append(MCQuestion(
            question=f"What is the {_ordinal(i+1)} app tile shown on the start page?",
            choices=choices, correct=correct_letter,
            category="element_content", app="start_page", difficulty="medium",
        ))
    return questions


def start_page_not_an_app(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    """Multiple 'which is NOT an app' with different fakes."""
    real_apps = ["OpenTodos", "OpenCalendar", "OpenMessages", "OpenMaps", "OpenCodeEditor", "OpenShop"]
    fake_apps = ["OpenNotes", "OpenWeather", "OpenMusic", "OpenFitness", "OpenBanking", "OpenTravel"]
    questions = []
    for fake in fake_apps[:4]:
        real = rng.sample(real_apps, 3)
        choices, correct_letter = _shuffle_choices(fake, real, rng)
        questions.append(MCQuestion(
            question="Which of the following is NOT an app shown on the start page?",
            choices=choices, correct=correct_letter,
            category="element_content", app="start_page", difficulty="medium",
        ))
    return questions


# ===========================================================================
# Cross-App Templates
# ===========================================================================


def cross_app_which_uses_table(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Calendar (for the monthly grid view)",
        ["Todo (for the task list)", "Messenger (for the conversation list)", "Maps (for the saved locations list)"],
        rng,
    )
    return [MCQuestion(
        question="Which app uses an HTML table as its main layout element?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="cross_app", difficulty="hard",
    )]


def cross_app_which_has_search(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Maps, Messenger, and Online Shop",
        ["Only Maps and Online Shop", "All apps have search", "Only the Online Shop"],
        rng,
    )
    return [MCQuestion(
        question="Which apps include a search feature?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="cross_app", difficulty="hard",
    )]


def cross_app_return_button(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Yes, every app has a 'Return to List of Apps' link or button",
        ["No, only the Todo and Calendar apps have it", "No, you must use the browser back button from some apps", "Only the start page has navigation links to other apps"],
        rng,
    )
    return [MCQuestion(
        question="Do all sub-apps provide a way to navigate back to the main start page?",
        choices=choices, correct=correct_letter,
        category="navigation", app="cross_app",
    )]


def cross_app_which_uses_checkboxes(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "The Todo app (for marking items done)",
        ["The Calendar app (for selecting event days)", "The Messenger app (for selecting messages)", "The Maps app (for toggling map layers)"],
        rng,
    )
    return [MCQuestion(
        question="Which app uses checkboxes as a primary interaction element?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="cross_app", difficulty="hard",
    )]


def cross_app_which_uses_dropdowns(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Calendar (recurring frequency) and Code Editor (language, theme)",
        ["Only the Online Shop (product categories)", "Todo (priority levels) and Calendar (recurring)", "None of the apps use dropdown menus"],
        rng,
    )
    return [MCQuestion(
        question="Which apps use dropdown/select menus in their interface?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="cross_app", difficulty="hard",
    )]


def cross_app_which_has_sidebar(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Maps (saved locations & search) and Code Editor (file tree)",
        ["Only Maps has a sidebar", "All apps have sidebars", "None of the apps have sidebars"],
        rng,
    )
    return [MCQuestion(
        question="Which apps feature a sidebar panel in their layout?",
        choices=choices, correct=correct_letter,
        category="element_location", app="cross_app", difficulty="hard",
    )]


def cross_app_which_uses_tabs(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "The Code Editor (for open files)",
        ["The Calendar (for switching between months)", "The Todo app (for filtering done vs. not done)", "The Messenger (for switching between chats)"],
        rng,
    )
    return [MCQuestion(
        question="Which app uses a tab bar in its interface?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="cross_app", difficulty="hard",
    )]


def cross_app_which_has_modals(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "The Calendar (event detail dialog), Maps (marker customization), and Code Editor (error/folder dialogs)",
        ["Only the Calendar has modals", "None of the apps use modals", "Only Maps and the Online Shop use modals"],
        rng,
    )
    return [MCQuestion(
        question="Which apps use modal dialogs or pop-up overlays?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="cross_app", difficulty="hard",
    )]


def cross_app_which_has_forms(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Todo (add/edit items), Calendar (create event), and Messenger (send message)",
        ["Only the Calendar has forms", "All apps except Maps have forms", "Only Todo and Calendar have forms"],
        rng,
    )
    return [MCQuestion(
        question="Which apps contain HTML forms for user input?",
        choices=choices, correct=correct_letter,
        category="form_structure", app="cross_app", difficulty="hard",
    )]


def cross_app_which_uses_sqlite(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Todo, Calendar, and Messenger each use their own SQLite database",
        ["Only the Online Shop uses a database", "All apps share a single SQLite database", "No apps use databases — they store data in memory"],
        rng,
    )
    return [MCQuestion(
        question="Which apps store their data in a SQLite database?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="cross_app", difficulty="hard",
    )]


def cross_app_which_has_pagination(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "The Calendar (month navigation) and the Online Shop (search results pages)",
        ["All apps have pagination", "Only the Todo app paginates its list", "None of the apps have pagination"],
        rng,
    )
    return [MCQuestion(
        question="Which apps use pagination to navigate through content?",
        choices=choices, correct=correct_letter,
        category="navigation", app="cross_app", difficulty="hard",
    )]


def cross_app_which_has_avatars(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "The Messenger app shows avatar images for each contact",
        ["All apps show user avatars", "Only the start page shows icons per app", "None of the apps display avatar images"],
        rng,
    )
    return [MCQuestion(
        question="Which app displays user avatar images?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="cross_app",
    )]


def cross_app_which_has_icons(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "The start page (app tile icons), Maps (marker icons), and Messenger (Font Awesome icons)",
        ["Only the start page uses icons", "No apps use icons — they rely on text labels only", "Only Maps uses icons for markers"],
        rng,
    )
    return [MCQuestion(
        question="Which apps make use of icons in their interface?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="cross_app", difficulty="hard",
    )]


def cross_app_framework_identification(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Most apps use FastHTML; Maps and Online Shop use FastAPI",
        ["All apps use React", "All apps use FastAPI", "Most apps use Django; Maps uses Flask"],
        rng,
    )
    return [MCQuestion(
        question="What web framework(s) power the OpenApps applications?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="cross_app", difficulty="hard",
    )]


def cross_app_which_has_hidden_inputs(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "Todo (edit form ID field) and Messenger (interlocutor field)",
        ["No apps use hidden inputs", "Only the Online Shop checkout form", "All apps use hidden input fields"],
        rng,
    )
    return [MCQuestion(
        question="Which apps use hidden input fields in their forms?",
        choices=choices, correct=correct_letter,
        category="form_structure", app="cross_app", difficulty="hard",
    )]


def cross_app_which_uses_htmx(state: dict, config: dict, rng: random.Random) -> list[MCQuestion]:
    choices, correct_letter = _shuffle_choices(
        "The Todo app uses htmx for adding, toggling, and editing items without page reload",
        ["All apps use htmx", "No apps use htmx — they all use standard form submissions", "Only the Messenger uses htmx for sending messages"],
        rng,
    )
    return [MCQuestion(
        question="Which app uses htmx for dynamic, partial page updates?",
        choices=choices, correct=correct_letter,
        category="element_identification", app="cross_app", difficulty="hard",
    )]


# ===========================================================================
# Template Registry
# ===========================================================================

ALL_TEMPLATES: dict[str, list] = {
    "todo": [
        todo_count_done,
        todo_count_not_done,
        todo_total_count,
        todo_identify_done_items,
        todo_identify_not_done_items,
        todo_specific_item_state,
        todo_first_item,
        todo_last_item,
        todo_items_at_positions,
        todo_item_neighbor,
        todo_item_not_in_list,
        todo_button_purpose,
        todo_edit_button_purpose,
        todo_remove_button_purpose,
        todo_element_type_for_completion,
        todo_controls_per_item,
        todo_input_placeholder,
        todo_add_button_label,
        todo_app_title,
        todo_nav_element,
        todo_edit_form_elements,
        todo_list_element_type,
    ],
    "calendar": [
        calendar_count_events,
        calendar_count_birthdays,
        calendar_count_conference_deadlines,
        calendar_count_recurring,
        calendar_event_dates,
        calendar_event_is_recurring,
        calendar_event_location,
        calendar_event_exists_multi,
        calendar_event_not_exists,
        calendar_birthday_month,
        calendar_whose_birthday,
        calendar_recurring_type,
        calendar_view_toggle,
        calendar_create_event_fields,
        calendar_month_navigation,
        calendar_delete_event_element,
        calendar_date_input_type,
        calendar_weekday_headers,
        calendar_add_event_location,
    ],
    "messenger": [
        messenger_count_conversations,
        messenger_contact_exists_multi,
        messenger_contact_not_exists,
        messenger_message_count_per_contact,
        messenger_first_message,
        messenger_last_message,
        messenger_who_sent_last,
        messenger_who_sent_first,
        messenger_total_message_count,
        messenger_conversation_with_most_messages,
        messenger_send_button_icon,
        messenger_search_feature,
        messenger_group_chat_exists,
        messenger_back_button,
        messenger_input_placeholder,
        messenger_chat_bubble_alignment,
    ],
    "map": [
        map_count_saved_places,
        map_place_exists_multi,
        map_place_not_saved,
        map_delete_location_element,
        map_sidebar_location,
        map_search_element,
        map_save_location_interaction,
        map_marker_customization,
        map_click_info,
        map_return_button_label,
        map_saved_locations_heading,
        map_current_location_info,
        map_tile_provider,
        map_width_ratio,
    ],
    "codeeditor": [
        codeeditor_file_count,
        codeeditor_folder_count,
        codeeditor_file_exists,
        codeeditor_file_not_exists,
        codeeditor_folder_exists,
        codeeditor_file_content,
        codeeditor_file_extension,
        codeeditor_empty_folder,
        codeeditor_sidebar_location,
        codeeditor_language_selector,
        codeeditor_theme_selector,
        codeeditor_new_file_button,
        codeeditor_rename_interaction,
        codeeditor_action_buttons,
        codeeditor_folder_collapse,
        codeeditor_tab_behavior,
    ],
    "start_page": [
        start_page_app_count,
        start_page_app_names,
        start_page_app_names_positive,
        start_page_headline,
        start_page_tile_layout,
        start_page_tile_contents,
        start_page_app_order,
        start_page_not_an_app,
    ],
}
