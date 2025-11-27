from dataclasses import dataclass
from typing import Optional
from abc import ABC, abstractmethod
import hashlib
import re
from deepdiff import DeepDiff
from deepdiff.operator import BaseOperator
from datetime import datetime
import copy


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

    keys_to_remove: list[str] = ["id"]

    def __init__(self, state1: dict, state2: dict):
        self.raw_state1 = state1
        self.raw_state2 = state2

        self.state1 = self.preprocess(self.raw_state1)
        self.state2 = self.preprocess(self.raw_state2)

    def preprocess(self, state: dict) -> dict:
        state = state.copy()
        state = self._remove_id_key(state)
        return state

    def _remove_id_key(self, state: dict) -> dict:
        """Removes id keys from todo and calendar, as ID is
        internal to how entries are stored in the database
        """
        for todo in state["todo"]:
            if "id" in todo:
                del todo["id"]

        for event in state["calendar"]:
            if "id" in event:
                del event["id"]
        return state

    def _remove_timestamp_from_messenger(self, state: dict) -> dict:
        for messages in state["messenger"]:
            old_message_contents = messages["messages"]
            new_message_contents = [m.pop() for m in old_message_contents]
            messages["messages"] = new_message_contents
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
            dict1, dict2, custom_operators=[StringSimilarityOperator(types=[str])]
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

    event: dict

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = initial_state.copy()
        target_state["calendar"].append(self.event)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class RemoveEventTask(Task):
    """
    Task to remove an event from the calendar.
    """

    event: dict

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = initial_state.copy()
        idx_to_remove = None
        for i, event in enumerate(target_state["calendar"]):
            if event["title"] == event.title:
                idx_to_remove = i
        # remove the event to be deleted
        target_state.pop(idx_to_remove)
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
        target_state = initial_state.copy()
        target_idx = None
        for i, todo_item in enumerate(target_state["todo"]):
            if self.todo_name in todo_item:
                new_todo_item = [self.todo_name, True]
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

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        now = datetime.now()
        # Format the datetime object into the desired string format
        formatted_time_string = now.strftime("%b %d, %I:%M%p")
        target_state = initial_state.copy()
        messages = target_state["messenger"].get(self.to, [])
        messages.append([self.message, False, self.to, formatted_time_string])
        target_state["messenger"][self.to] = messages
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
        now = datetime.now()
        # Format the datetime object into the desired string format
        formatted_time_string = now.strftime("%b %d, %I:%M%p")
        target_state = initial_state.copy()
        new_place = {"name": self.name, "coords": [self.latitude, self.longitude]}
        target_state["map"].append(new_place)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


if __name__ == "__main__":
    add_meeting_with_dennis = AddEventTask(
        goal="Go to the Calendar app and add my meeting with Dennis on April 1st of 2026. The title should be 'Dennis-Bob'. Set the description as 'paper reading', omit the URL and set the location to New York City. Make sure to add Dennis as an invitee.",
        event={
            "title": "Dennis-Bob",
            "date": "2026-04-01",
            "description": "paper reading",
            "location": "New York City",
            "url": None,
            "invitees": "Dennis",
        },
    )
