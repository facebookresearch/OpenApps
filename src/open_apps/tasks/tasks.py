from dataclasses import dataclass
from typing import Optional
from abc import ABC, abstractmethod
import hashlib
import re


def are_strings_similar(str1: str, str2: str) -> bool:
    """
    Compare two strings while ignoring case, special characters, and extra spaces.

    Args:
        str1: First string to compare
        str2: Second string to compare

    Returns:
        bool: True if strings are similar, False otherwise
    """

    def normalize_string(s: str) -> str:
        # Convert to lowercase
        s = s.lower()
        # Replace special characters and multiple spaces with single space
        s = re.sub(r"[^\w\s]", "", s)
        # Remove extra whitespace
        s = re.sub(r"\s+", " ", s)
        # Strip leading/trailing whitespace
        return s.strip()

    return normalize_string(str1) == normalize_string(str2)


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
        target_state["calendar"]["events"].append(self.event)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        # TODO: consider fuzzy string matching using functiona above
        return target_state == current_state


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
