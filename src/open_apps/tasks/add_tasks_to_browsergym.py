import os
from typing import Optional, Tuple
import requests

import playwright.sync_api
from playwright.sync_api import sync_playwright
import wandb
from browsergym.core.task import AbstractBrowserTask
from open_apps.tasks.tasks import Task
import gymnasium as gym
from browsergym.core.env import BrowserEnv


def register_tasks_with_browsergym(
    tasks: list[Task], base_url: Optional[str] = None
) -> None:
    """create a list of all tasks to be registered with browsergym"""
    # TODO: call this function somewhere before we start browser gym
    for task in enumerate(tasks):
        register_task(
            id=task.task_id,
            task_class=OpenAppsTask,
            task_kwargs={"task_config": task},
            nondeterministic=False,
        )


class OpenAppsTask(AbstractBrowserTask):
    """
    Abstract class for OpenApps.
    """

    # gym metadata (default value, can be overloaded per task)
    nondeterministic: bool = False

    @classmethod
    def get_task_id(cls, task_id: str | None = None) -> str:
        if task_id is None:
            raise ValueError("task_id must be provided.")
        return task_id

    def _get_goal(self):
        return f"{self.goal}"

    def __init__(
        self,
        seed: int,
        task_config: Task,
        base_url: str,
        episode_max_time: int = 1000000,
        remove_human_display: bool = True,
        screen_resolution: Tuple[int, int] = (1024, 640),
    ) -> None:
        """
        Args:
            seed: random seed.
            task_config: Task, the task configuration object, speicifying the goal, task_id, etc.
            base_url: str the base URL where the task's HTML file is to be found, typically set in launch_experiment.py
            episode_max_time: int, episode max time in milliseconds. Default: 1000000 ms.
            remove_human_display: bool, whether or not to remove the human display (goal, time left, last reward etc.) from the DOM. Default: True.
            screen_resolution: Tuple[int, int], the screen resolution (width, height) of the browser window. Default: (1024, 640).

        """
        super().__init__(seed)

        self.goal = task_config.goal
        self.task = task_config
        self.task_id = task_config.task_id
        self.goal_category = (
            task_config.goal_category
        )  # optional string: set in task init, to categorize the goal (prompt), e.g "typos, foreign language, etc."

        # task properties, will be used to set up the browsergym environment
        self.viewport = {"width": screen_resolution[0], "height": screen_resolution[1]}
        self.slow_mo = 100  # ms
        self.timeout = 5000  # ms

        assert episode_max_time > 0

        self.url = base_url
        self.episode_max_time = episode_max_time
        self.remove_human_display = remove_human_display

    def _get_info(self):
        info = {}  # e.g. episodeID, reward, ect
        return info

    def setup(self, page: playwright.sync_api.Page) -> tuple[str, dict]:
        if wandb.run is not None:
            wandb.summary["goal"] = self._get_goal()
            wandb.summary["url"] = self.url
            wandb.summary["task_id"] = self.task_id
            wandb.summary["task_class_name"] = self.__class__.__name__
            wandb.summary["goal_category"] = self.goal_category
        self.page = page
        self.page.goto(self.url)
        self.initial_state = get_current_state(self.url)
        return self._get_goal(), self._get_info()

    def validate(
        self, page: playwright.sync_api.Page, chat_messages: list[str]
    ) -> Tuple[float, bool, str, dict]:

        info = self._get_info()
        reward = self.reward()
        if reward >= 1.0:
            done = True
        else:
            done = False

        msg = ""
        return reward, done, msg, info

    def reward(self) -> float:
        """Return 1.0 if the item was marked as done, else 0.0."""
        current_state = get_current_state(self.url)

        return (
            1.0
            if self.task.check_if_task_is_complete(self.initial_state, current_state)
            else 0.0
        )

    def teardown(self) -> None:
        pass

    def cheat(self, page: playwright.sync_api.Page, chat_messages: list[str]) -> None:
        pass


def safe_get_json(url: str):
    """Safely perform a GET request and return JSON, or empty list on failure."""
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return []


def get_current_state(url: str) -> dict:
    """Fetch the current state from the given URL."""
    state = {}
    state["todo"] = safe_get_json(url + "/todo_all")
    # calendar has "id": int column and often other fields that are null too
    state["calendar"] = safe_get_json(url + "/calendar_all")
    state["map"] = safe_get_json(url + "/maps/landmarks")
    state["messenger"] = safe_get_json(url + "/messages_all")
    state["online_shop"] = safe_get_json(url + "/onlineshop_all")
    state["codeeditor"] = safe_get_json(url + "/codeeditor_all")
    return state
