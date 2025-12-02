from dataclasses import dataclass
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
        state = self._remove_id_key(state)
        state = self._remove_timestamp_from_messenger(state)
        state = self._normalize_map_locations(state)
        return state

    def _remove_id_key(self, state: dict) -> dict:
        """Removes id keys from todo and calendar, as ID is
        internal to how entries are stored in the database
        """
        for todo in state["todo"]:
            if "id" in todo:
                del todo["id"]

        for event in state["calendar"]:
            # remove empty values and id key
            keys_to_delete = []
            for k, v in event.items():
                if k == "id" or v is None or v == "" or v == []:
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
                "coords": [int(place["coords"][0] * 10), int(place["coords"][0] * 10)],
            }
            normalized_places.append(new_place)
        state["map"] = normalized_places
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

    @abstractmethod
    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict
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
        if target_state["calendar"][-1] != self.event:
            target_state["calendar"].append(self.event)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict | DictConfig
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
        self, initial_state: dict, current_state: dict
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
        is_done = None if not self.is_done else True
        target_state["todo"].append({"title": self.todo_name, "done": is_done})
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict
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
        self, initial_state: dict, current_state: dict
    ) -> bool:
        target_state = self.get_target_state(initial_state)
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
        self, initial_state: dict, current_state: dict
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
        new_place = {"name": self.name, "coords": [self.latitude, self.longitude]}
        if target_state["map"][-1] != new_place:
            target_state["map"].append(new_place)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


if __name__ == "__main__":
    pass
