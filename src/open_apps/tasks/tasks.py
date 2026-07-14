from dataclasses import dataclass, field
from typing import Optional
from abc import ABC, abstractmethod
import hashlib
import re
from deepdiff import DeepDiff
from deepdiff.operator import BaseOperator
from datetime import datetime
import copy
from deepdiff.helper import COLORED_COMPACT_VIEW
from omegaconf.dictconfig import DictConfig
from omegaconf import OmegaConf


class StringSimilarityOperator(BaseOperator):
    """
    Operator is used in DeepDiff to compare strings.
    Ignores case, special characters, and extra spaces when comparing strings.
    """

    @staticmethod
    def normalize_string(s: str) -> str:
        # Convert to lowercase
        s = s.lower()
        # Replace special characters and multiple spaces with single space
        s = re.sub(r"[^\w\s]", "", s)
        # Remove extra whitespace
        s = re.sub(r"\s+", " ", s)
        # Strip leading/trailing whitespace
        return s.strip()

    def give_up_diffing(self, level, diff_instance):
        if isinstance(level.t1, str) and isinstance(level.t2, str):
            # Compare strings case-insensitively
            if self.normalize_string(level.t1) == self.normalize_string(level.t2):
                return True  # Strings are equal, stop diffing
        return False


class AppStateComparison:
    """
    Compare two app states for similarity.

    Args:
        state1: First app state to compare
        state2: Second app state to compare
    """

    def __init__(self, state1: dict, state2: dict):
        self.raw_state1 = state1
        self.raw_state2 = state2

        self.state1 = self.preprocess(self.raw_state1)
        self.state2 = self.preprocess(self.raw_state2)

    def preprocess(self, state: dict) -> dict:
        state = state.copy()
        # Drop underscore-prefixed metadata keys (e.g. ``_url`` injected
        # by the env for URL-based tasks). They would otherwise show up
        # as a key-set mismatch against the target state.
        for k in [k for k in state if k.startswith("_")]:
            del state[k]
        # Temporarily exclude code editor state from task completion comparison.
        state.pop("codeeditor", None)
        state = self._normalize_calendar_invitees(state)
        state = self._remove_id_key(state)
        state = self._normalize_todo_done_field(state)
        state = self._remove_timestamp_from_messenger(state)
        state = self._normalize_map_locations(state)
        state = self.sort_lists(state)
        return state

    def _normalize_todo_done_field(self, state: dict) -> dict:
        for todo in state["todo"]:
            done_value = todo.get("done")
            if (
                done_value is None
                or done_value is False
                or done_value == 0
                or done_value == "0"
            ):
                todo["done"] = False
            elif done_value is True or done_value == 1 or done_value == "1":
                todo["done"] = True
        return state

    def _normalize_calendar_invitees(self, state: dict) -> dict:
        """
        Canonicalize each calendar event's ``invitees`` to a sorted list.
        """
        for event in state["calendar"]:
            if "invitees" not in event:
                continue
            value = event["invitees"]
            if OmegaConf.is_config(value):  # convert hydra config to python format
                value = OmegaConf.to_container(value, resolve=True)
            if isinstance(value, str):  # convert comma separted strings into a list
                names = [part.strip() for part in value.split(",") if part.strip()]
            elif isinstance(value, (list, tuple)):  # normalize list/tuple of strings
                names = [str(part).strip() for part in value if str(part).strip()]
            elif value is None:
                names = []
            else:  # unexpected scalar
                names = [str(value).strip()] if str(value).strip() else []
            event["invitees"] = sorted(names, key=StringSimilarityOperator.normalize_string)
        return state

    def _remove_id_key(self, state: dict) -> dict:
        """Removes id keys from todo and calendar, as ID is
        internal to how entries are stored in the database
        """
        for todo in state["todo"]:
            if "id" in todo:
                del todo["id"]

        for event in state["calendar"]:
            # remove empty values, id, and fields no task compares on
            keys_to_delete = []
            for k, v in event.items():
                if k in ("id", "recurring") or v is None or v == "" or v == []:
                    keys_to_delete.append(k)
            for k in keys_to_delete:
                del event[k]
        return state

    def _remove_timestamp_from_messenger(self, state: dict) -> dict:
        for i, contact in enumerate(state["messenger"]):
            old_message_contents = contact["messages"]
            new_message_contents = [m[0] for m in old_message_contents]
            state["messenger"][i]["messages"] = new_message_contents
        return state

    def _normalize_map_locations(self, state: dict) -> dict:
        # sort map locations by name to avoid ordering issues
        normalized_places = []
        for i, place in enumerate(state["map"]):
            new_place = {
                "name": place["name"],
                "coords": [int(place["coords"][0] * 10), int(place["coords"][1] * 10)],
            }
            normalized_places.append(new_place)
        state["map"] = normalized_places
        return state

    def sort_lists(self, state: dict) -> dict:
        """To ensure comparisons don't fail
        due to different list orders, we sort.
        """
        # field by which to sort
        app_and_field = [
            ("map", "name"),
            ("todo", "title"),
            ("calendar", "title"),
            ("messenger", "user"),
        ]
        for app, field in app_and_field:
            state[app] = sorted(
                state[app],
                key=lambda p, f=field: StringSimilarityOperator.normalize_string(p[f]),
            )
        return state

    @staticmethod
    def are_dicts_similar(
        dict1: dict,
        dict2: dict,
    ) -> bool:
        """
        Compare two dictionaries for similarity, using a custom string comparison function.

        Args:
            dict1: First dictionary to compare
            dict2: Second dictionary to compare
        """
        diff = DeepDiff(
            dict1,
            dict2,
            custom_operators=[StringSimilarityOperator(types=[str])],
            ignore_string_type_changes=True,
            ignore_numeric_type_changes=True,
            ignore_nan_inequality=True,
            ignore_encoding_errors=True,
            view=COLORED_COMPACT_VIEW,
        )
        if diff == {}:
            return True
        print(f"===Differences found: {diff}")
        return False

    def compare(self) -> bool:
        # check that both states have the same apps
        if set(self.state1.keys()) != set(self.state2.keys()):
            print("States have different apps")
            return False

        return self.are_dicts_similar(self.state1, self.state2)


@dataclass
class Task(ABC):
    goal: str
    # Optional descriptor for how the goal is phrased (e.g. the user-goal
    # variation style). Keyword-only so subclasses can keep declaring
    # required positional fields without tripping dataclass field ordering.
    goal_style: Optional[str] = field(default=None, kw_only=True)

    @abstractmethod
    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        # Implement your logic to check if the event has been added successfully
        # commpare initial state and target state
        pass

    @property
    def task_id(self) -> str:
        goal_string = self.goal.encode("utf-8")
        return hashlib.sha256(goal_string).hexdigest()


@dataclass
class AddEventTask(Task):
    """
    Task to add an event to the calendar.
    """

    title: str
    date: str
    description: str | None
    location: str | None
    url: str | None
    invitees: list[str]

    @property
    def event(self) -> dict:
        return {
            "title": self.title,
            "date": self.date,
            "description": self.description if self.description else "",
            "location": self.location if self.location else "",
            "url": self.url if self.url else "",
            "invitees": self.invitees,
        }

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        assert target_state["calendar"], "calendar must be populated"
        if target_state["calendar"][-1] != self.event:
            target_state["calendar"].append(self.event)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        if isinstance(current_state, DictConfig):
            current_state = OmegaConf.to_container(current_state, resolve=True)
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class RemoveEventTask(Task):
    """
    Task to remove an event from the calendar.
    """

    title: str
    date: str

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        idx_to_remove = None
        for i, event in enumerate(target_state["calendar"]):
            if event["title"] == self.title and event["date"] == self.date:
                idx_to_remove = i
        # remove the event to be deleted
        if idx_to_remove is not None:
            target_state["calendar"].pop(idx_to_remove)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class AddToDoTask(Task):
    """
    Task to add a todo to the todo app.
    """

    todo_name: str
    is_done: bool

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        target_state["todo"].append({"title": self.todo_name, "done": self.is_done})
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class MarkToDoDoneTask(Task):
    """
    Mark todo item as done
    """

    todo_name: str

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        target_idx = None
        for i, todo_item in enumerate(target_state["todo"]):
            if self.todo_name == todo_item["title"]:
                new_todo_item = {"title": self.todo_name, "done": 1}
                target_idx = i
        if target_idx is None:
            raise ValueError(f"Todo item {self.todo_name} not found")
        target_state["todo"][target_idx] = new_todo_item
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        try:
            target_state = self.get_target_state(initial_state)
        except ValueError:
            return False
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class SendMessageTask(Task):
    to: str
    message: str
    expected_reply: str | None

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        now = datetime.now()
        # Format the datetime object into the desired string format
        formatted_time_string = now.strftime("%b %d, %I:%M%p")
        contact_idx = None
        for i, contact in enumerate(target_state["messenger"]):
            if contact["user"] == self.to:
                contact_idx = i
        if contact_idx is None:
            raise ValueError(f"Contact {self.to} not found in messenger app")
        messages = target_state["messenger"][contact_idx]["messages"]
        messages.append([self.message, False, self.to, formatted_time_string])
        if self.expected_reply:
            messages.append([self.expected_reply, True, self.to, formatted_time_string])
        target_state["messenger"][contact_idx]["messages"] = messages
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class SavePlaceTask(Task):
    name: str
    latitude: float
    longitude: float

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        assert target_state["map"], "map must be populated"
        new_place = {"name": self.name, "coords": [self.latitude, self.longitude]}
        if target_state["map"][-1] != new_place:
            target_state["map"].append(new_place)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class DeleteToDoTask(Task):
    """Click-only task: delete a todo item via the per-row remove button."""

    todo_name: str

    def get_target_state(self, initial_state: dict) -> dict:
        target_state = copy.deepcopy(initial_state)
        idx_to_remove = None
        for i, item in enumerate(target_state["todo"]):
            if item["title"] == self.todo_name:
                idx_to_remove = i
        if idx_to_remove is not None:
            target_state["todo"].pop(idx_to_remove)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class RemoveLandmarkTask(Task):
    """Click-only task: remove a saved landmark via the per-row delete button."""

    name: str

    def get_target_state(self, initial_state: dict) -> dict:
        target_state = copy.deepcopy(initial_state)
        idx_to_remove = None
        for i, place in enumerate(target_state["map"]):
            if place["name"] == self.name:
                idx_to_remove = i
        if idx_to_remove is not None:
            target_state["map"].pop(idx_to_remove)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


# Maps a target-app key to URL-path prefixes that count as "in that app".
# Mirrors open_apps.mcp.registry.APP_URL_PATHS; inlined here to keep the
# tasks package import light (avoids pulling in hydra/uvicorn).
_NAV_APP_URL_PREFIXES: dict[str, tuple[str, ...]] = {
    "todo": ("/todo",),
    "calendar": ("/calendar",),
    "messages": ("/messages",),
    "codeeditor": ("/codeeditor",),
    "map": ("/maps",),
}


@dataclass
class NavigateToAppTask(Task):
    """Click-only task: starting in ``source_app``, end up in ``target_app``.

    Reward = 1 once the page URL's path lives under the target app's URL
    prefix. Relies on the env injecting ``current_state['_url']`` before
    invoking this task's check.
    """

    source_app: str
    target_app: str

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        url = current_state.get("_url", "") if isinstance(current_state, dict) else ""
        if not url:
            return False
        try:
            from urllib.parse import urlparse

            path = urlparse(url).path or "/"
        except Exception:
            return False
        prefixes = _NAV_APP_URL_PREFIXES.get(self.target_app, (f"/{self.target_app}",))
        return any(
            path == p or path.startswith(p + "/") or path.rstrip("/") == p
            for p in prefixes
        )


if __name__ == "__main__":
    pass
