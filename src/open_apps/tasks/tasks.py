from dataclasses import dataclass
from typing import Optional
from abc import ABC, abstractmethod
import hashlib


@dataclass
class Task(ABC):
    goal: str
    goal_category: Optional[str]

    @abstractmethod
    def check_if_task_is_complete(self, initial_state: dict) -> bool:
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

    def target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = initial_state.copy()
        target_state["events"].append(self.event)
        return target_state

    def check_if_task_is_complete(self, initial_state: dict) -> bool:
        # Implement your logic to check if the event has been added successfully
        # commpare initial state and target state
        return True


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
