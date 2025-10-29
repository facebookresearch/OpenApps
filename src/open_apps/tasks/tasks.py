from dataclasses import dataclass
from typing import Optional
from abc import ABC, abstractmethod
import hashlib
import re
from deepdiff import DeepDiff
from deepdiff.operator import BaseOperator


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
    # TODO: consider removing "id" key
    diff = DeepDiff(
        dict1, dict2, custom_operators=[StringSimilarityOperator(types=[str])]
    )
    return diff == {}


@dataclass
class Task(ABC):
    goal: str
    goal_category: Optional[str]

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
        return are_dicts_similar(target_state, current_state)


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
        return are_dicts_similar(target_state, current_state)


add_meeting_with_dennis_task = AddEventTask(
    goal="Go to the Calendar app and add my meeting with Dennis on April 1st of 2026. The title should be 'Dennis-Bob'. Set the description as 'paper reading', omit the URL and set the location to New York City. Make sure to add Dennis as an invitee.",
    event={
        "title": "Dennis-Bob",
        "date": "2026-04-01",
        "description": "paper reading",
        "location": "New York City",
        "url": None,
        "invitees": "Dennis",
    },
    goal_category="explicit",
)
